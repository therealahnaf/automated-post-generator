import io
import json
import unittest
from unittest.mock import patch

import fetch_tweets


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

    def test_detects_possible_long_post_preview(self) -> None:
        self.assertTrue(fetch_tweets.looks_possibly_truncated("A" * 260))
        self.assertTrue(fetch_tweets.looks_possibly_truncated("News preview…"))
        self.assertFalse(fetch_tweets.looks_possibly_truncated("A complete post."))

    @patch("fetch_tweets.urlopen")
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

    @patch("fetch_tweets.urlopen")
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

    @patch("fetch_tweets.urlopen")
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

    @patch("fetch_tweets.urlopen")
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
            "fetch_tweets.fetch_tweet",
            return_value={"id": "2079479742802141202", "text": "Tweet"},
        ):
            result = fetch_tweets.fetch_tweets(
                ["https://x.com/Polymarket/status/2079479742802141202"]
            )

        self.assertEqual(result["provider"], "fxtwitter")
        self.assertEqual(result["full_text_provider_api"], "https://api.vxtwitter.com")
        self.assertFalse(result["official_x_api_used"])
        self.assertEqual(
            result["open_source_project"],
            "https://github.com/FxEmbed/FxEmbed",
        )


if __name__ == "__main__":
    unittest.main()
