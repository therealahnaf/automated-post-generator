import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from tools.news import cinema_post, generate_carousel, generate_post


def write_tweet(root: Path, photos: list[Path], highlight: str = "cyan", language: str = "english") -> Path:
    path = root / "tweet.json"
    path.write_text(json.dumps({
        "requested_urls": ["https://x.com/example/status/123"],
        "post_language": language,
        "platform_languages": {"facebook": language, "instagram": language},
        "headline_highlight": highlight,
        "items": [{"id": "123", "text": "A source story.", "downloaded_photos": [{"local_path": str(photo)} for photo in photos]}],
    }), encoding="utf-8")
    return path


class CinemaTextTests(unittest.TestCase):
    def test_displayable_keeps_drawable_text_and_drops_the_rest(self) -> None:
        self.assertEqual(cinema_post.displayable("Rig Hits $3.1B — 27% 🚀", cinema_post.BBH_BOGLE), "Rig Hits $3.1B — 27%")
        self.assertEqual(cinema_post.displayable("GPT‑6 → Café", cinema_post.BBH_BOGLE), "GPT-6 -> Cafe")

    def test_dual_settles_on_one_colour_per_post(self) -> None:
        picks = {cinema_post.resolve_highlight("dual", str(seed)) for seed in range(40)}
        self.assertEqual(picks, {"red", "cyan"})
        self.assertEqual(cinema_post.resolve_highlight("dual", "123"), cinema_post.resolve_highlight("dual", "123"))

    def test_swipe_hint_only_drops_when_the_one_photo_is_on_the_headline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "photo.jpg"
            Image.new("RGB", (900, 700), "white").save(photo)
            self.assertFalse(cinema_post.has_more_cards(write_tweet(root, [photo]), photo))
            self.assertTrue(cinema_post.has_more_cards(write_tweet(root, [photo, photo]), photo))
            self.assertTrue(cinema_post.has_more_cards(write_tweet(root, []), None))
            self.assertTrue(cinema_post.has_more_cards(None, photo))


class CinemaScriptTests(unittest.TestCase):
    def test_photo_tweet_renders_cinema_without_the_image_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "photo.jpg"
            Image.new("RGB", (1000, 1000), "white").save(photo)
            tweet = write_tweet(root, [photo])
            output = root / "post.png"
            with mock.patch.object(generate_post, "generate_background", side_effect=AssertionError("no image API")):
                result = generate_post.main(["A source story", "--headline", "A concise headline", "--tweet-json", str(tweet),
                                             "--style", "cinema", "--output", str(output)])
            metadata = json.loads(output.with_suffix(".json").read_text("utf-8"))
            with Image.open(output) as card:
                size = card.size
        self.assertEqual(result, 0)
        self.assertEqual(size, (1080, 1350))
        self.assertEqual(metadata["style"], "cinema")
        self.assertEqual(Path(metadata["feature_image_source"]), photo.resolve())
        self.assertEqual(Path(metadata["background_source"]).parent, cinema_post.BACKDROP_DIR)
        self.assertIsNone(metadata["background_asset_path"])

    def test_photo_less_tweet_generates_and_keeps_a_cinema_background(self) -> None:
        payload = io.BytesIO()
        Image.new("RGB", (1024, 1280), "navy").save(payload, format="PNG")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet = write_tweet(root, [])
            output = root / "post.png"
            with (
                mock.patch.dict(generate_post.os.environ, {"OPENAI_API_KEY": "test-key"}),
                mock.patch.object(generate_post, "make_client"),
                mock.patch.object(generate_post, "generate_background", return_value=payload.getvalue()) as generate,
            ):
                result = generate_post.main(["A source story", "--headline", "A concise headline", "--tweet-json", str(tweet),
                                             "--style", "cinema", "--output", str(output)])
            metadata = json.loads(output.with_suffix(".json").read_text("utf-8"))
            background_saved = Path(metadata["background_asset_path"]).is_file()
        self.assertEqual(result, 0)
        self.assertIn("top 55 percent", generate.call_args.args[1])
        self.assertEqual(metadata["background_source"], "openai-image-api")
        self.assertTrue(background_saved)

    def test_localized_headline_keeps_the_brand_block_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "photo.jpg"
            Image.new("RGB", (900, 700), "white").save(photo)
            backgrounds = root / "backgrounds"
            backgrounds.mkdir()
            Image.new("RGB", (1024, 1280), "navy").save(backgrounds / "bg-1.png")
            tweet = write_tweet(root, [photo], language="bangla")
            output = root / "post.png"
            result = generate_post.main(["A source story", "--headline", "A concise headline", "--tweet-json", str(tweet),
                                         "--translated-title", "একটি শিরোনাম", "--style", "cinema",
                                         "--background-dir", str(backgrounds), "--output", str(output)])
            metadata = json.loads(output.with_suffix(".json").read_text("utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(metadata["style"], "brand-block")

    def test_cinema_primary_gets_cinema_detail_and_summary_cards(self) -> None:
        for mode in ("media", "fallback"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                media = root / "media.png"
                Image.new("RGB", (900, 500), (20, 80, 220)).save(media)
                tweet = write_tweet(root, [media])
                primary = root / "primary.png"
                Image.new("RGB", (1080, 1350), (5, 10, 15)).save(primary)
                primary.with_suffix(".json").write_text(json.dumps({"style": "cinema", "background_source": "tweet-photo"}))
                copy = root / "copy.json"
                copy.write_text(json.dumps({
                    "workflow_type": "news", "copy_language": "english", "mode": mode,
                    "secondary_source_images": [str(media)] if mode == "media" else [],
                    "short_descriptions": ["A concise contextual detail."],
                }), encoding="utf-8")
                output = root / "cards"
                result = generate_carousel.main(["--tweet-json", str(tweet), "--primary-image", str(primary),
                                                 "--copy-json", str(copy), "--output-dir", str(output),
                                                 "--platform", "facebook", "--date", "2026-11-22"])
                metadata = json.loads((output / "carousel.json").read_text("utf-8"))
                with Image.open(output / "02-detail-1.png") as card:
                    size = card.size
                self.assertEqual(result, 0)
                self.assertEqual(size, (1080, 1350))
                self.assertEqual(Path(metadata["background_sources"][0]).parent, cinema_post.BACKDROP_DIR)


if __name__ == "__main__":
    unittest.main()
