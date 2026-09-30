import json
import tempfile
import unittest
from pathlib import Path

from tools.news import prepare_platform_descriptions as prepare


class PreparePlatformDescriptionsTests(unittest.TestCase):
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
            facebook_manifest = json.loads((output / "facebook-description.website.json").read_text())
            instagram_manifest = json.loads((output / "instagram-description.website.json").read_text())
            self.assertEqual(facebook_manifest["archive_key"], instagram_manifest["archive_key"])
            self.assertEqual(facebook_manifest["description"], "English.")
            self.assertEqual(facebook_manifest["title"], "Approved English headline")


if __name__ == "__main__":
    unittest.main()
