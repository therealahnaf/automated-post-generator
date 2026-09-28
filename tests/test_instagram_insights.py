"""Parser and error-handling tests without a live Meta token."""

import unittest
from unittest.mock import Mock

from content_api.instagram_insights import InsightsError, fetch_metrics


class InstagramInsightsTests(unittest.TestCase):
    def test_mixed_response_shapes_and_missing_metrics(self):
        response = Mock(status_code=200)
        response.json.return_value = {"data": [
            {"name": "views", "total_value": {"value": 100}},
            {"name": "likes", "values": [{"value": 5}]},
        ]}
        session = Mock()
        session.get.return_value = response
        metrics = fetch_metrics(session, "123", "private-token", "v25.0")
        self.assertEqual(metrics["views"], 100)
        self.assertEqual(metrics["likes"], 5)
        self.assertIsNone(metrics["shares"])
        self.assertEqual(session.get.call_args.kwargs["headers"],
                         {"Authorization": "Bearer private-token"})

    def test_rate_limit_is_not_swallowed(self):
        response = Mock(status_code=429)
        response.json.return_value = {"error": {"code": 4, "message": "secret"}}
        session = Mock()
        session.get.return_value = response
        with self.assertRaises(InsightsError) as context:
            fetch_metrics(session, "123", "private-token", "v25.0")
        self.assertEqual(context.exception.code, "rate_limited")
        self.assertNotIn("secret", str(context.exception))


if __name__ == "__main__":
    unittest.main()
