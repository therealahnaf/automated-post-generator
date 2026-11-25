import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from tools.news import collect_source_media as collector
from tools.news import generate_carousel, generate_carousel_copy, generate_post, media_plan


def picture(size=(1200, 900), color=(40, 90, 160)) -> Image.Image:
    """A distinct test image per colour: its layout is seeded by the colour, so no two look alike to the
    collector's duplicate check."""
    import random

    rng = random.Random(sum(color) * 7 + color[0])
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    for _ in range(14):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        w, h = rng.randrange(60, 400), rng.randrange(60, 400)
        draw.rectangle((x, y, x + w, y + h), fill=tuple(rng.randrange(256) for _ in range(3)))
    return image


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_tweet(root: Path, photos: int = 1, card_url: str | None = None) -> Path:
    media = root / "media"
    media.mkdir(exist_ok=True)
    downloaded = []
    for number in range(1, photos + 1):
        path = media / f"photo-{number}.png"
        picture(color=(20 * number, 120, 60)).save(path)
        downloaded.append({"local_path": f"media/{path.name}"})
    item = {"id": "2124865060397019480", "text": "NVIDIA Board reinstates Jensen Huang.", "downloaded_photos": downloaded}
    if card_url:
        item["card"] = {"url": card_url}
    tweet = root / "tweet.json"
    tweet.write_text(json.dumps({"items": [item], "workflow_type": "news", "headline_highlight": "red",
                                 "post_language": "english"}), encoding="utf-8")
    return tweet


PAGE = """<html><head><title>NVIDIA Board Reinstates Jensen Huang</title>
<meta property="og:site_name" content="NVIDIA Newsroom">
<meta property="og:image" content="https://nvidianews.nvidia.com/hero.png">
<meta name="description" content="NVIDIA today announced that effective immediately, Jensen Huang has been reinstated as CEO.">
</head><body><nav><img src="/nav-logo.png"></nav>
<p>The Board of Directors voted unanimously to reinstate Mr. Huang following meetings with Mr. Huang and his team.</p>
<figure><img src="/figure.png" alt="Jensen Huang on stage"><figcaption>Jensen Huang at GTC 2026.</figcaption></figure>
<img src="/company-logo.png"></body></html>"""


class FakeResponse:
    def __init__(self, url: str, body: bytes, content_type: str, status: int = 200):
        self.url, self.status_code, self.ok = url, status, status < 400
        self.headers = {"content-type": content_type}
        self.content = body
        self.text = body.decode("utf-8", "replace") if content_type.startswith("text") else ""
        self.raw = io.BytesIO(body)
        self.encoding = "utf-8"

    def json(self):
        return json.loads(self.content)


class FakeSession:
    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.headers = {}

    def get(self, url, **_):
        return self.routes.get(url) or FakeResponse(url, b"not found", "text/plain", 404)


class CollectorTests(unittest.TestCase):
    def test_primary_sources_only(self) -> None:
        self.assertTrue(collector.is_primary("https://nvidianews.nvidia.com/news/x"))
        self.assertTrue(collector.is_primary("https://github.com/php/php-src/security/advisories/1"))
        self.assertFalse(collector.is_primary("https://apnews.com/article/1"))
        self.assertFalse(collector.is_primary("https://www.theverge.com/a"))
        self.assertFalse(collector.is_primary("https://x.com/nvidia/status/1"))
        self.assertFalse(collector.is_primary("https://example.com/paper.pdf"))

    def test_sentences_stay_whole_across_abbreviations(self) -> None:
        text = ("The Board voted to reinstate Mr. Huang after meetings with Mr. Huang and the U.S. government. "
                "We are grateful to Colette Kress for her outstanding service as interim CEO.")
        self.assertEqual(collector.sentences(text), [
            "The Board voted to reinstate Mr. Huang after meetings with Mr. Huang and the U.S. government.",
            "We are grateful to Colette Kress for her outstanding service as interim CEO.",
        ])

    def test_collect_numbers_media_and_quotes_from_primary_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = "https://nvidianews.nvidia.com/news/reinstated"
            tweet = write_tweet(root, photos=1, card_url=page)
            sources = root / "final.sources.json"
            sources.write_text(json.dumps({"urls": ["https://apnews.com/article/1", page]}), encoding="utf-8")
            routes = {
                page: FakeResponse(page, PAGE.encode(), "text/html; charset=utf-8"),
                "https://nvidianews.nvidia.com/hero.png": FakeResponse("", png_bytes(picture((1600, 900))), "image/png"),
                "https://nvidianews.nvidia.com/figure.png": FakeResponse("", png_bytes(picture((1000, 1000), (150, 30, 30))), "image/png"),
            }
            with patch.object(collector.requests, "Session", return_value=FakeSession(routes)):
                manifest = collector.collect(tweet, sources, [], root / "source-media")
            self.assertEqual([item["id"] for item in manifest["tweet_photos"]], ["t1"])
            self.assertEqual([item["id"] for item in manifest["images"]], ["s1", "s2"])
            self.assertEqual(manifest["images"][1]["caption"], "Jensen Huang at GTC 2026.")
            self.assertEqual(manifest["images"][0]["publisher"], "NVIDIA Newsroom")
            texts = [quote["text"] for quote in manifest["quotes"]]
            self.assertIn("The Board of Directors voted unanimously to reinstate Mr. Huang following meetings with "
                          "Mr. Huang and his team.", texts)
            self.assertEqual([page_info["url"] for page_info in manifest["pages"]], [page])  # AP skipped
            self.assertTrue((root / "source-media" / "contact-sheet.jpg").is_file())

    def test_letterboxed_copy_is_marked_as_the_same_picture(self) -> None:
        original = picture((1600, 1000), (200, 60, 60))
        ImageDraw.Draw(original).rectangle((0, 0, 1599, 999), outline=(250, 250, 250), width=6)
        boxed = Image.new("RGB", (1400, 1400), (12, 12, 12))
        boxed.paste(original.resize((1300, 812)), (50, 294))
        other = picture((1600, 1000), (60, 60, 200))
        prints = {name: collector.content_fingerprint(image) for name, image in
                  (("t1", boxed), ("s1", original), ("s2", other))}
        manifest = {"tweet_photos": [{"id": "t1"}], "images": [{"id": "s1"}, {"id": "s2"}]}
        collector.mark_similar(manifest, prints)
        self.assertEqual(manifest["tweet_photos"][0]["similar_to"], ["s1"])
        self.assertEqual(manifest["images"][1]["similar_to"], [])

    def test_logos_are_flagged_but_sparse_charts_are_not(self) -> None:
        logo = Image.new("RGB", (1200, 630), (0, 0, 0))
        ImageDraw.Draw(logo).rectangle((520, 230, 680, 400), fill=(118, 185, 0))
        chart = Image.new("RGB", (1600, 900), (255, 255, 255))
        draw = ImageDraw.Draw(chart)
        draw.line((80, 820, 1540, 820), fill=(60, 60, 60), width=3)
        draw.line((80, 60, 80, 820), fill=(60, 60, 60), width=3)
        draw.line((100, 700, 600, 400, 1100, 300, 1500, 120), fill=(40, 80, 220), width=5)
        self.assertTrue(collector.looks_like_logo(logo))
        self.assertFalse(collector.looks_like_logo(chart))
        self.assertFalse(collector.looks_like_logo(picture()))

    def test_collect_never_fails_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet = write_tweet(root, photos=0, card_url="https://blocked.example.com/p")
            with patch.object(collector.requests, "Session", return_value=FakeSession({})):
                code = collector.main(["--tweet-json", str(tweet), "--output-dir", str(root / "sm")])
            self.assertEqual(code, 0)
            manifest = json.loads((root / "sm" / "source-media.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["images"], [])
            self.assertEqual(manifest["pages"][0]["error"], "http 404")


def manifest_for(root: Path) -> dict:
    paths = []
    for name, color in (("t1", (30, 120, 60)), ("s1", (120, 40, 40)), ("s2", (40, 40, 140))):
        path = root / f"{name}.png"
        picture(color=color).save(path)
        paths.append(path)
    return {
        "tweet_photos": [{"id": "t1", "path": str(paths[0]), "size": [1200, 900]}],
        "images": [
            {"id": "s1", "path": str(paths[1]), "publisher": "NVIDIA Newsroom", "caption": "Jensen Huang", "alt": "", "size": [1200, 900]},
            {"id": "s2", "path": str(paths[2]), "publisher": "GitHub", "caption": "", "alt": "Advisory", "size": [1200, 900]},
        ],
        "quotes": [{"id": "q1", "text": "The Board believes Jensen is the right person to lead NVIDIA.", "publisher": "NVIDIA Newsroom"},
                   {"id": "q2", "text": "A second sentence from the release that is long enough.", "publisher": "NVIDIA Newsroom"},
                   {"id": "q3", "text": "A third sentence from the release that is long enough too.", "publisher": "NVIDIA Newsroom"}],
    }


class MediaPlanTests(unittest.TestCase):
    def test_resolves_kinds_and_credits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            resolved = media_plan.resolve(
                {"cover": {"image": "s1", "fill": True},
                 "slides": [{"image": "t1", "text": True}, {"image": "s2"}, {"quote": "q1"}]},
                manifest_for(Path(directory)))
        self.assertEqual(resolved["cover"]["credit"], "Image: NVIDIA Newsroom")
        self.assertTrue(resolved["cover"]["fill"])
        self.assertEqual([slide["kind"] for slide in resolved["slides"]], ["detail", "photo", "quote"])
        self.assertEqual([slide["credit"] for slide in resolved["slides"]],
                         ["Image via X", "Image: GitHub", "Source: NVIDIA Newsroom"])
        self.assertEqual(resolved["slides"][2]["text"], "The Board believes Jensen is the right person to lead NVIDIA.")

    def test_rejects_bad_plans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = manifest_for(Path(directory))
            bad = [
                {"cover": "s9", "slides": []},
                {"cover": "s1", "slides": [{"image": "s1"}]},
                {"cover": None, "slides": [{"image": "t1", "quote": "q1"}]},
                {"cover": None, "slides": [{"quote": "q1"}, {"quote": "q2"}, {"quote": "q3"}]},
                {"cover": None, "slides": [{"quote": "q1"}] * 1 + [{"image": "t1"}] * 9},
                {"cover": None, "slides": [], "badge": "OFFICIAL"},
                {"cover": None, "slides": [{"image": "t1", "text": "yes"}]},
                {"cover": "s1", "slides": [{"image": "s2"}]},  # s2 is the same picture as s1
            ]
            bad.append({"cover": "t1", "slides": []})  # t1 flagged as a logo below
            manifest["tweet_photos"][0]["logo"] = True
            manifest["images"][0]["similar_to"] = ["s2"]
            manifest["images"][1]["similar_to"] = ["s1"]
            for plan in bad:
                with self.assertRaises(ValueError, msg=json.dumps(plan)):
                    media_plan.resolve(plan, manifest)


class PlannedCarouselTests(unittest.TestCase):
    def test_cover_copy_and_carousel_follow_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet = write_tweet(root, photos=1)
            manifest = manifest_for(root)
            manifest["tweet_photos"][0]["path"] = str((root / "media" / "photo-1.png").resolve())
            plan_path = root / "media-plan.resolved.json"
            plan_path.write_text(json.dumps(media_plan.resolve(
                {"cover": "s1", "slides": [{"image": "t1", "text": True}, {"image": "s2"}, {"quote": "q1"}]},
                manifest)), encoding="utf-8")

            primary = root / "primary.png"
            self.assertEqual(generate_post.main([
                "NVIDIA reinstated Jensen Huang.", "--headline", "NVIDIA Reinstates Jensen Huang as CEO",
                "--tweet-json", str(tweet), "--style", "cinema", "--media-plan", str(plan_path),
                "--output", str(primary)]), 0)
            metadata = json.loads(primary.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["feature_image_credit"], "Image: NVIDIA Newsroom")
            self.assertEqual(Path(metadata["feature_image_source"]).name, "s1.png")
            self.assertNotEqual(metadata["background_source"], "openai-image-api")

            description = root / "description.txt"
            description.write_text("NVIDIA's board reinstated Jensen Huang.\n\nSources:\nNVIDIA", encoding="utf-8")
            copy_path = root / "copy.json"
            with patch.object(generate_carousel_copy.news_description, "require_api_key"), \
                    patch.object(generate_carousel_copy.news_description, "make_client"), \
                    patch.object(generate_carousel_copy, "generate_short_descriptions",
                                 return_value=["The board voted unanimously after meetings with Huang."]) as writer:
                self.assertEqual(generate_carousel_copy.main([
                    "--tweet-json", str(tweet), "--post-metadata", str(primary.with_suffix(".json")),
                    "--description-file", str(description), "--output", str(copy_path),
                    "--media-plan", str(plan_path)]), 0)
            self.assertEqual(writer.call_args.args[4], 1)  # one text slide
            self.assertEqual(writer.call_args.args[5], ["a photo from the X post"])
            payload = json.loads(copy_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "plan")

            package = root / "package"
            self.assertEqual(generate_carousel.main([
                "--tweet-json", str(tweet), "--primary-image", str(primary), "--copy-json", str(copy_path),
                "--output-dir", str(package)]), 0)
            carousel = json.loads((package / "carousel.json").read_text(encoding="utf-8"))
            self.assertEqual([Path(path).name for path in carousel["secondary_images"]],
                             ["02-detail-1.png", "03-photo-2.png", "04-quote-3.png"])
            self.assertEqual(carousel["credits"], ["Image via X", "Image: GitHub", "Source: NVIDIA Newsroom"])
            for path in [carousel["primary_image"], *carousel["secondary_images"]]:
                with Image.open(path) as card:
                    self.assertEqual(card.size, (1080, 1350))

    def test_without_a_plan_nothing_changes(self) -> None:
        parser = generate_post.build_parser()
        self.assertIsNone(parser.parse_args(["news", "--headline", "H"]).media_plan)


if __name__ == "__main__":
    unittest.main()
