import json
import tempfile
import unittest
from pathlib import Path

import finalize_description


class FinalizeDescriptionTests(unittest.TestCase):
    def test_appends_sources_in_order_and_deduplicates(self) -> None:
        result = finalize_description.append_sources(
            "English.\n\n---\n\nবাংলা।",
            [
                "https://x.com/example/status/123",
                "https://example.com/report",
                "https://example.com/report",
            ],
        )

        self.assertEqual(
            result,
            "English.\n\n---\n\nবাংলা।\n\nSources:\n"
            "https://x.com/example/status/123\n"
            "https://example.com/report",
        )

    def test_replaces_existing_source_block(self) -> None:
        result = finalize_description.append_sources(
            "Copy.\n\nSources:\nhttps://old.example/story",
            ["https://new.example/story"],
        )

        self.assertEqual(
            result,
            "Copy.\n\nSources:\nhttps://new.example/story",
        )

    def test_reads_requested_x_urls_from_tweet_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tweet.json"
            path.write_text(
                json.dumps(
                    {
                        "requested_urls": [
                            "https://x.com/example/status/123",
                            "https://x.com/example/status/456",
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                finalize_description.read_tweet_source_urls(path),
                [
                    "https://x.com/example/status/123",
                    "https://x.com/example/status/456",
                ],
            )

    def test_rejects_platform_overflow_after_sources(self) -> None:
        with self.assertRaisesRegex(ValueError, "platform maximum"):
            finalize_description.append_sources(
                "A" * 40,
                ["https://example.com/source"],
                max_characters=50,
            )

    def test_rejects_non_http_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid source URL"):
            finalize_description.append_sources(
                "Copy.",
                ["javascript:alert(1)"],
            )


if __name__ == "__main__":
    unittest.main()
