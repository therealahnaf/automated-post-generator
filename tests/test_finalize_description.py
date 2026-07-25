import json
import tempfile
import unittest
from pathlib import Path

from tools.news import finalize_description


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
            "@example on X\n"
            "Example",
        )

    def test_replaces_existing_source_block(self) -> None:
        result = finalize_description.append_sources(
            "Copy.\n\nSources:\n@old_account on X\nOld Publisher",
            ["https://x.com/new_account/status/123"],
        )

        self.assertEqual(
            result,
            "Copy.\n\nSources:\n@new_account on X",
        )

    def test_formats_social_accounts_and_known_publishers_without_links(self) -> None:
        result = finalize_description.append_sources(
            "Copy.",
            [
                "https://twitter.com/Polymarket/status/123",
                "https://www.instagram.com/openai/p/example/",
                "https://www.reuters.com/technology/example-story/",
                "https://openai.com/index/example/",
            ],
        )

        self.assertEqual(
            result,
            "Copy.\n\nSources:\n"
            "@Polymarket on X\n"
            "@openai on Instagram\n"
            "Reuters\n"
            "OpenAI",
        )
        self.assertNotIn("http", result)

    def test_deduplicates_multiple_articles_from_same_publisher(self) -> None:
        result = finalize_description.append_sources(
            "Copy.",
            [
                "https://www.reuters.com/technology/first/",
                "https://www.reuters.com/world/second/",
            ],
        )

        self.assertEqual(result, "Copy.\n\nSources:\nReuters")

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
