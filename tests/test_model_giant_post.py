import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from tools.models import generate_post, giant_post


def write_photo(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    image = Image.new("RGB", size, color)
    ImageDraw.Draw(image).rectangle((20, 20, size[0] - 20, size[1] - 20), outline=(255, 255, 255), width=8)
    image.save(path)
    return path


def write_job(root: Path, sizes: list[tuple[int, int]], *, model: str = "Qwen4-Max",
              company: str = "Alibaba Qwen") -> tuple[Path, Path]:
    media = root / "media"
    media.mkdir()
    photos = [write_photo(media / f"photo-{index}.png", size, (30 + index * 40, 60, 90))
              for index, size in enumerate(sizes, start=1)]
    tweet = root / "tweet.json"
    tweet.write_text(json.dumps({"items": [{
        "id": "2122148127520289151",
        "text": "A model launch with benchmark charts.",
        "downloaded_photos": [{"local_path": f"media/{photo.name}"} for photo in photos],
    }]}), encoding="utf-8")
    descriptions = [
        f"Benchmark {index} shows a gain over the previous model. It also costs less per task." for index in range(1, len(sizes) + 1)
    ] or ["The first description segment.", "The second description segment."]
    copy = root / "copy.json"
    copy.write_text(json.dumps({"model_name": model, "company_name": company, "headline": f"Meet {model}",
                                "short_descriptions": descriptions}), encoding="utf-8")
    return tweet, copy


def run(tweet: Path, copy: Path, output: Path, *extra: str) -> int:
    return generate_post.main(["--tweet-json", str(tweet), "--copy-json", str(copy), "--output-dir", str(output),
                               "--date", "2026-11-23", *extra])


class GiantMarkTests(unittest.TestCase):
    def test_logo_matches_the_company_never_the_model(self) -> None:
        self.assertEqual(giant_post.logo_slug("Alibaba Qwen"), "qwen")
        self.assertEqual(giant_post.logo_slug("Google DeepMind"), "deepmind")
        self.assertEqual(giant_post.logo_slug("Thinking Machines Lab"), "thinkingmachines")
        self.assertEqual(giant_post.logo_slug("OpenAI"), "openai")
        self.assertIsNone(giant_post.logo_slug("xtools-at"))

    def test_fallback_letters(self) -> None:
        self.assertEqual(giant_post.fallback_letters("Shanghai Artificial Intelligence Laboratory"), "SAI")
        self.assertEqual(giant_post.fallback_letters("xtools-at"), "XA")
        self.assertEqual(giant_post.fallback_letters("Reflection"), "RE")
        self.assertEqual(giant_post.fallback_letters("The Lab of Things"), "LT")

    def test_highlight_alternates_by_section(self) -> None:
        colours = [giant_post.section_highlight("seed", section) for section in range(5)]
        self.assertEqual(len(set(colours)), 2)
        self.assertTrue(all(left != right for left, right in zip(colours, colours[1:])))

    def test_caption_split_has_two_halves_without_dangling_punctuation(self) -> None:
        first, second = giant_post.split_caption(
            "It adds a mixture-of-experts architecture and a 1M-token context; Qwen claims up to 50% fewer tokens.")
        self.assertTrue(first and second)
        self.assertFalse(first.endswith((",", ";")))

    def test_plan_spreads_landscape_photos_within_ten_slides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wide = [write_photo(root / f"w{index}.png", (300, 200), (90, 90, 90)) for index in range(9)]
            tall = write_photo(root / "tall.png", (200, 300), (90, 90, 90))
            self.assertEqual(giant_post.plan_sections(wide[:3]), ["spread"] * 3)
            self.assertEqual(giant_post.plan_sections(wide[:5]), ["spread"] * 4 + ["single"])
            self.assertEqual(giant_post.plan_sections(wide), ["single"] * 9)
            self.assertEqual(giant_post.plan_sections([tall, wide[0]]), ["single", "spread"])
            for count in range(1, 10):
                plan = giant_post.plan_sections(wide[:count])
                self.assertLessEqual(1 + sum(2 if kind == "spread" else 1 for kind in plan), 10)

    def test_cli_renders_alternating_spreads_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, copy = write_job(root, [(1600, 900), (1900, 876), (1762, 950)])
            first, second = root / "first", root / "second"
            self.assertEqual(run(tweet, copy, first), 0)
            self.assertEqual(run(tweet, copy, second), 0)
            metadata = json.loads((first / "post.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["primary_style"], "giant-mark")
            self.assertEqual([section["layout"] for section in metadata["sections"]],
                             ["lead-and-wide", "adaptive-max", "lead-and-wide"])
            highlights = [section["highlight"] for section in metadata["sections"]]
            self.assertTrue(all(left != right for left, right in zip(highlights, highlights[1:])))
            self.assertEqual(len(metadata["secondary_images"]), 6)
            self.assertEqual(Path(metadata["secondary_images"][0]).name, "02-feature-1-left.png")
            self.assertEqual(Path(metadata["secondary_images"][1]).name, "03-feature-1-right.png")
            self.assertEqual(len(metadata["background_sources"]), 4)
            for path in [metadata["primary_image"], *metadata["secondary_images"]]:
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1080, 1350))
                twin = second / Path(path).name
                self.assertEqual(Path(path).read_bytes(), twin.read_bytes())

    def test_cli_without_photos_uses_summary_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, copy = write_job(root, [])
            output = root / "cards"
            self.assertEqual(run(tweet, copy, output), 0)
            metadata = json.loads((output / "post.json").read_text(encoding="utf-8"))
            self.assertEqual([Path(path).name for path in metadata["secondary_images"]],
                             ["02-summary-1.png", "03-summary-2.png"])
            self.assertEqual([section["layout"] for section in metadata["sections"]], ["summary", "summary"])

    def test_rerender_removes_slides_the_previous_render_listed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, copy = write_job(root, [(1600, 900)])
            output = root / "cards"
            self.assertEqual(run(tweet, copy, output), 0)
            self.assertTrue((output / "03-feature-1-right.png").is_file())
            self.assertEqual(run(tweet, copy, output, "--primary-style", "signal-stack-condensed"), 0)
            self.assertFalse((output / "03-feature-1-right.png").exists())
            self.assertTrue((output / "02-feature-1.png").is_file())

    def test_website_archive_gets_each_spread_whole(self) -> None:
        from tools.news import content_archive
        import shutil

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, copy = write_job(root, [(1600, 900), (600, 900)])
            output = root / "cards"
            self.assertEqual(run(tweet, copy, output), 0)
            metadata = json.loads((output / "post.json").read_text(encoding="utf-8"))
            media = [Path(metadata["primary_image"]), *map(Path, metadata["secondary_images"])]
            self.assertEqual(len(media), 4)
            archived = content_archive.website_media(media)
            joined = Path(metadata["spreads"][0]["joined"])
            self.assertEqual(archived, [media[0], joined, media[3]])
            with Image.open(joined) as whole:
                self.assertEqual(whole.size, (2160, 1350))
            # Slides copied elsewhere still match by content, when post.json travels with them.
            copied = root / "copied"
            copied.mkdir()
            for path in [*media, output / "post.json"]:
                shutil.copy2(path, copied / path.name)
            moved = [copied / path.name for path in media]
            self.assertEqual(content_archive.website_media(moved), [moved[0], joined, moved[3]])
            # Media without a renderer sidecar is archived exactly as published.
            self.assertEqual(content_archive.website_media([tweet]), [tweet])

    def test_unsettable_name_falls_back_to_classic_design(self) -> None:
        with self.assertRaises(giant_post.UnsupportedText):
            giant_post.render_cover("文心一言", "百度", date(2026, 11, 23), seed="1", has_photos=False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, copy = write_job(root, [], model="文心一言", company="百度")
            output = root / "cards"
            self.assertEqual(run(tweet, copy, output), 0)
            metadata = json.loads((output / "post.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["primary_style"], "signal-stack-condensed")


if __name__ == "__main__":
    unittest.main()
