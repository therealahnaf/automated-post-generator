import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.news import generate_carousel


class NewsGenerateCarouselTests(unittest.TestCase):
    def make_backgrounds(self, root: Path) -> Path:
        directory = root / "backgrounds"
        directory.mkdir()
        Image.new("RGB", (1080, 1350), (20, 40, 60)).save(directory / "bg-1.png")
        Image.new("RGB", (1080, 1350), (60, 20, 40)).save(directory / "bg-2.png")
        return directory

    def make_tweet(self, root: Path, language: str = "english") -> Path:
        path = root / "tweet.json"
        path.write_text(
            json.dumps(
                {
                    "post_language": language,
                    "platform_languages": {
                        "facebook": language,
                        "instagram": language,
                    },
                    "items": [{"id": "123", "text": "A source-grounded story."}],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_media_carousel_keeps_primary_first_and_adds_detail_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backgrounds = self.make_backgrounds(root)
            tweet = self.make_tweet(root)
            primary = root / "primary.png"
            Image.new("RGB", (1080, 1350), (5, 10, 15)).save(primary)
            media = root / "media.png"
            Image.new("RGB", (900, 500), (20, 80, 220)).save(media)
            copy = root / "copy.json"
            copy.write_text(
                json.dumps(
                    {
                        "workflow_type": "news",
                        "copy_language": "english",
                        "mode": "media",
                        "secondary_source_images": [str(media.resolve())],
                        "short_descriptions": ["A concise contextual detail."],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "cards"
            exit_code = generate_carousel.main(
                [
                    "--tweet-json", str(tweet),
                    "--primary-image", str(primary),
                    "--copy-json", str(copy),
                    "--output-dir", str(output),
                    "--background-dir", str(backgrounds),
                    "--platform", "facebook",
                    "--seed", "42",
                    "--date", "2026-08-18",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((output / "01-headline.png").is_file())
            self.assertTrue((output / "02-detail-1.png").is_file())
            metadata = json.loads((output / "carousel.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["secondary_mode"], "media")
            self.assertEqual(metadata["background_seed"], 42)
            self.assertEqual(metadata["source_images"], [str(media.resolve())])

    def test_no_media_carousel_adds_one_text_only_second_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backgrounds = self.make_backgrounds(root)
            tweet = self.make_tweet(root, language="bangla")
            primary = root / "primary.png"
            Image.new("RGB", (1080, 1350), (5, 10, 15)).save(primary)
            copy = root / "copy.json"
            copy.write_text(
                json.dumps(
                    {
                        "workflow_type": "news",
                        "copy_language": "bangla",
                        "mode": "fallback",
                        "secondary_source_images": [],
                        "short_descriptions": ["একটি সংক্ষিপ্ত সংবাদ সারাংশ।"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = root / "cards"
            exit_code = generate_carousel.main(
                [
                    "--tweet-json", str(tweet),
                    "--primary-image", str(primary),
                    "--copy-json", str(copy),
                    "--output-dir", str(output),
                    "--background-dir", str(backgrounds),
                    "--platform", "instagram",
                    "--date", "2026-08-18",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((output / "02-detail-1.png").is_file())
            metadata = json.loads((output / "carousel.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["post_language"], "bangla")
            self.assertEqual(len(metadata["secondary_images"]), 1)


if __name__ == "__main__":
    unittest.main()
