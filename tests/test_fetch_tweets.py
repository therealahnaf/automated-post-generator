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
        self.assertFalse(result["official_x_api_used"])
        self.assertEqual(
            result["open_source_project"],
            "https://github.com/FxEmbed/FxEmbed",
        )


if __name__ == "__main__":
    unittest.main()
