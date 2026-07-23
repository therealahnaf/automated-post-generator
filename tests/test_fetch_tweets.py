import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tools.news import fetch_tweets


class FetchTweetsTests(unittest.TestCase):
    def test_normalizes_x_and_twitter_status_urls(self) -> None:
        expected = "https://x.com/Polymarket/status/2079479742802141202"
        self.assertEqual(
            fetch_tweets.normalize_tweet_url(
                "https://x.com/Polymarket/status/2079479742802141202?s=20"
            ),
            expected,
        )
        self.assertEqual(
            fetch_tweets.normalize_tweet_url(
                "https://twitter.com/Polymarket/status/2079479742802141202"
            ),
            expected,
        )

    def test_rejects_non_status_url(self) -> None:
        with self.assertRaises(ValueError):
            fetch_tweets.normalize_tweet_url("https://x.com/Polymarket")

    def test_builds_fxtwitter_endpoint(self) -> None:
        self.assertEqual(
            fetch_tweets.fxtwitter_endpoint(
                "https://x.com/Polymarket/status/2079479742802141202"
            ),
            "https://api.fxtwitter.com/Polymarket/status/2079479742802141202",
        )

    def test_builds_fxtwitter_v2_thread_endpoint(self) -> None:
        self.assertEqual(
            fetch_tweets.fxtwitter_thread_endpoint(
                "https://x.com/Polymarket/status/2079479742802141202"
            ),
            "https://api.fxtwitter.com/2/thread/2079479742802141202",
        )

    def test_detects_possible_long_post_preview(self) -> None:
        self.assertTrue(fetch_tweets.looks_possibly_truncated("A" * 260))
        self.assertTrue(fetch_tweets.looks_possibly_truncated("News preview…"))
        self.assertFalse(fetch_tweets.looks_possibly_truncated("A complete post."))

    @patch("tools.news.fetch_tweets.urlopen")
    def test_fetches_and_validates_tweet(self, mock_urlopen) -> None:
        payload = {
            "code": 200,
            "tweet": {
                "id": "2079479742802141202",
                "text": "A real tweet",
            },
        }
        mock_urlopen.return_value = io.StringIO(json.dumps(payload))

        tweet = fetch_tweets.fetch_tweet(
            "https://x.com/Polymarket/status/2079479742802141202?s=20"
        )

        self.assertEqual(tweet["id"], "2079479742802141202")
        self.assertEqual(tweet["text"], "A real tweet")
        self.assertEqual(tweet["full_text_recovery"], {"attempted": False})

    @patch("tools.news.fetch_tweets.urlopen")
    def test_recovers_longer_text_from_vxtwitter(self, mock_urlopen) -> None:
        preview = "A" * 270
        full_text = preview + " with the missing long-post ending."
        primary_payload = {
            "code": 200,
            "tweet": {
                "id": "2079479742802141202",
                "text": preview,
                "raw_text": {"text": preview, "display_text_range": [0, len(preview)]},
            },
        }
        fallback_payload = {
            "tweetID": "2079479742802141202",
            "text": full_text,
        }
        mock_urlopen.side_effect = [
            io.StringIO(json.dumps(primary_payload)),
            io.StringIO(json.dumps(fallback_payload)),
        ]

        tweet = fetch_tweets.fetch_tweet(
            "https://x.com/Polymarket/status/2079479742802141202"
        )

        self.assertEqual(tweet["text"], full_text)
        self.assertEqual(tweet["text_before_recovery"], preview)
        self.assertEqual(tweet["text_source"], "vxtwitter")
        self.assertTrue(tweet["full_text_recovery"]["succeeded"])
        self.assertEqual(tweet["raw_text"]["display_text_range"], [0, len(full_text)])

    @patch("tools.news.fetch_tweets.urlopen")
    def test_rejects_mismatched_full_text_fallback_id(self, mock_urlopen) -> None:
        mock_urlopen.return_value = io.StringIO(
            json.dumps({"tweetID": "999", "text": "Wrong tweet"})
        )

        with self.assertRaisesRegex(RuntimeError, "expected 2079479742802141202"):
            fetch_tweets.fetch_full_text_candidate(
                "https://x.com/Polymarket/status/2079479742802141202",
                api_base=fetch_tweets.DEFAULT_FULL_TEXT_API_BASE,
                timeout=30,
            )

    @patch("tools.news.fetch_tweets.urlopen")
    def test_rejects_mismatched_tweet_id(self, mock_urlopen) -> None:
        payload = {
            "code": 200,
            "tweet": {"id": "999", "text": "Wrong tweet"},
        }
        mock_urlopen.return_value = io.StringIO(json.dumps(payload))

        with self.assertRaisesRegex(RuntimeError, "expected 2079479742802141202"):
            fetch_tweets.fetch_tweet(
                "https://x.com/Polymarket/status/2079479742802141202"
            )

    def test_result_declares_open_source_non_official_backend(self) -> None:
        with patch(
            "tools.news.fetch_tweets.fetch_thread",
            return_value={"id": "2079479742802141202", "text": "Tweet"},
        ):
            result = fetch_tweets.fetch_tweets(
                ["https://x.com/Polymarket/status/2079479742802141202"],
                post_language="bangla",
                headline_highlight="cyan",
            )

        self.assertEqual(result["provider"], "fxtwitter")
        self.assertEqual(result["provider_api"], "https://api.fxtwitter.com/2")
        self.assertEqual(result["full_text_provider_api"], "https://api.vxtwitter.com")
        self.assertFalse(result["official_x_api_used"])
        self.assertEqual(result["post_language"], "bangla")
        self.assertEqual(result["headline_highlight"], "cyan")
        self.assertEqual(
            result["open_source_project"],
            "https://github.com/FxEmbed/FxEmbed",
        )

    @patch("tools.news.fetch_tweets.fetch_binary")
    def test_downloads_tweet_photos_in_source_order(self, mock_fetch_binary) -> None:
        payload = io.BytesIO()
        Image.new("RGB", (320, 180), "blue").save(payload, format="JPEG")
        mock_fetch_binary.return_value = (payload.getvalue(), "image/jpeg")
        tweet = {
            "id": "2079590123038204255",
            "media": {
                "photos": [
                    {"id": "first", "type": "photo", "url": "https://cdn/1.jpg"},
                    {"id": "second", "type": "photo", "url": "https://cdn/2.jpg"},
                ]
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            downloaded = fetch_tweets.download_tweet_photos(
                tweet,
                Path(directory),
                timeout=30,
            )

            self.assertEqual([item["position"] for item in downloaded], [1, 2])
            self.assertEqual(
                [Path(item["local_path"]).name for item in downloaded],
                [
                    "2079590123038204255-photo-1.jpg",
                    "2079590123038204255-photo-2.jpg",
                ],
            )
            self.assertTrue(all(Path(item["local_path"]).is_file() for item in downloaded))
            self.assertEqual(tweet["downloaded_photos"], downloaded)
        self.assertEqual(
            [call.args[0] for call in mock_fetch_binary.call_args_list],
            ["https://cdn/1.jpg", "https://cdn/2.jpg"],
        )

    @patch("tools.news.fetch_tweets.urlopen")
    def test_fetches_full_same_author_thread_with_v2(self, mock_urlopen) -> None:
        payload = {
            "code": 200,
            "status": {"id": "100", "text": "Root"},
            "thread": [
                {"id": "100", "text": "Root"},
                {"id": "101", "text": "Continuation"},
            ],
        }
        mock_urlopen.return_value = io.StringIO(json.dumps(payload))

        tweet = fetch_tweets.fetch_thread("https://x.com/example/status/100")

        self.assertEqual([item["id"] for item in tweet["thread"]], ["100", "101"])
        self.assertEqual(tweet["thread_count"], 2)
        self.assertEqual(tweet["text_source"], "fxtwitter_v2")

    @patch("tools.news.fetch_tweets.fetch_binary")
    def test_downloads_thread_and_quote_photos_but_ignores_videos(
        self, mock_fetch_binary
    ) -> None:
        payload = io.BytesIO()
        Image.new("RGB", (320, 180), "blue").save(payload, format="JPEG")
        mock_fetch_binary.return_value = (payload.getvalue(), "image/jpeg")
        tweet = {
            "id": "100",
            "text": "Root",
            "quote": {
                "id": "90",
                "text": "Quoted",
                "media": {"photos": [{"type": "photo", "url": "https://cdn/q.jpg"}]},
            },
        }
        tweet["thread"] = [
            tweet,
            {
                "id": "101",
                "text": "Continuation",
                "media": {
                    "videos": [
                        {
                            "type": "video",
                            "formats": [
                                {"container": "video/mp4", "url": "https://cdn/v.mp4"}
                            ],
                        }
                    ],
                    "photos": [
                        {"type": "photo", "url": "https://cdn/thread.jpg"}
                    ],
                },
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            downloaded = fetch_tweets.download_tweet_media(
                tweet, Path(directory), timeout=30
            )

        self.assertEqual([item["kind"] for item in downloaded], ["photo", "photo"])
        self.assertEqual([item["origin"] for item in downloaded], ["quote", "thread"])
        self.assertEqual([item["source_status_id"] for item in downloaded], ["90", "101"])
        self.assertEqual(
            [item["source_url"] for item in downloaded],
            ["https://cdn/q.jpg", "https://cdn/thread.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
