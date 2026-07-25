import json
import tempfile
import unittest
from pathlib import Path

from tools.news import prepare_platform_descriptions as prepare


class PreparePlatformDescriptionsTests(unittest.TestCase):
    def test_orders_each_platform_and_preserves_sources(self) -> None:
        description = (
            "English description.\n\n---\n\n"
            "বাংলা বিবরণ।\n\nSources:\n"
            "@example on X\n"
            "Example"
        )
        facebook = prepare.order_description(description, "english")
        instagram = prepare.order_description(description, "bangla")

        self.assertTrue(facebook.startswith("English description."))
        self.assertTrue(instagram.startswith("বাংলা বিবরণ।"))
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
                "English.\n\n---\n\nবাংলা।\n\nSources:\nExample",
                encoding="utf-8",
            )
            tweet.write_text(
                json.dumps(
                    {
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
                    ]
                ),
                0,
            )
            self.assertTrue(
                (output / "facebook-description.txt")
                .read_text(encoding="utf-8")
                .startswith("বাংলা।")
            )
            self.assertTrue(
                (output / "instagram-description.txt")
                .read_text(encoding="utf-8")
                .startswith("English.")
            )


if __name__ == "__main__":
    unittest.main()
