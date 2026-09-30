import json
import tempfile
import unittest
from pathlib import Path

from tools.news import prepare_platform_descriptions as prepare


class PreparePlatformDescriptionsTests(unittest.TestCase):
    def test_fixed_notice_is_added_once_before_sources_and_hashtags(self) -> None:
        description = "English copy.\n\nSources:\nReuters\n\n#news #technology #ai"
        caption = prepare.order_description(description, "english")
        self.assertEqual(caption, (
            f"English copy.\n\n{prepare.SOURCE_LINK_NOTICE}\n\n"
            "Sources:\nReuters\n\n#news #technology #ai"
        ))
        self.assertEqual(prepare.order_description(caption, "english"), caption)

    def test_notice_precedes_hashtags_when_there_is_no_source_block(self) -> None:
        caption = prepare.order_description("English copy.\n\n#news #technology #ai", "english")
        self.assertEqual(caption, (
            f"English copy.\n\n{prepare.SOURCE_LINK_NOTICE}\n\n#news #technology #ai"
        ))

    def test_required_notice_counts_toward_the_caption_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "platform maximum"):
            prepare.order_description("A" * 2200, "english")

    def test_legacy_bilingual_copy_becomes_english_and_preserves_sources(self) -> None:
        description = (
            "English description.\n\n---\n\n"
            "বাংলা বিবরণ।\n\nSources:\n"
            "@example on X\n"
            "Example"
        )
        facebook = prepare.order_description(description, "english")
        instagram = prepare.order_description(description, "bangla")

        self.assertTrue(facebook.startswith("English description."))
        self.assertEqual(instagram, facebook)
        self.assertNotIn("বাংলা", instagram)
        self.assertNotIn("---", instagram)
        self.assertEqual(
            facebook.split("Sources:\n", 1)[1],
            instagram.split("Sources:\n", 1)[1],
        )

    def test_cli_writes_both_platform_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            description = root / "description.txt"
            tweet = root / "tweet.json"
            output = root / "platforms"
            description.write_text(
                "English.\n\nSources:\nExample\n\n#news #technology #ai",
                encoding="utf-8",
            )
            tweet.write_text(
                json.dumps(
                    {
                        "workflow_type": "news",
                        "requested_urls": ["https://x.com/example/status/123"],
                        "platform_languages": {
                            "facebook": "bangla",
                            "instagram": "english",
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                prepare.main(
                    [
                        "--description-file",
                        str(description),
                        "--tweet-json",
                        str(tweet),
                        "--output-dir",
                        str(output),
                        "--english-title",
                        "Approved English headline",
                    ]
                ),
                0,
            )
            self.assertTrue(
                (output / "facebook-description.txt")
                .read_text(encoding="utf-8")
                .startswith("English.")
            )
            self.assertTrue(
                (output / "instagram-description.txt")
                .read_text(encoding="utf-8")
                .startswith("English.")
            )
            self.assertEqual(
                (output / "facebook-description.txt").read_text(encoding="utf-8"),
                (output / "instagram-description.txt").read_text(encoding="utf-8"),
            )
            for platform in ("facebook", "instagram"):
                caption = (output / f"{platform}-description.txt").read_text(encoding="utf-8")
                self.assertEqual(caption.count(prepare.SOURCE_LINK_NOTICE), 1)
            facebook_manifest = json.loads((output / "facebook-description.website.json").read_text())
            instagram_manifest = json.loads((output / "instagram-description.website.json").read_text())
            self.assertEqual(facebook_manifest["archive_key"], instagram_manifest["archive_key"])
            self.assertEqual(facebook_manifest["description"], "English.")
            self.assertEqual(facebook_manifest["title"], "Approved English headline")


if __name__ == "__main__":
    unittest.main()
