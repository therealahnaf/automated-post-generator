import unittest
from unittest.mock import Mock, patch

from tools.news import publish_instagram


class PublishInstagramTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = publish_instagram.InstagramConfig(
            user_id="17841412762716180",
            access_token="secret-instagram-token",
            graph_version="v25.0",
        )

    def test_publish_requires_exact_confirmation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--confirm yes"):
            publish_instagram.require_publish_confirmation(True, "YES")
        publish_instagram.require_publish_confirmation(True, "yes")
        publish_instagram.require_publish_confirmation(False, None)

    def test_requires_public_https_image_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "publicly reachable HTTPS"):
            publish_instagram.validate_image_url("C:/output/post.png")
        self.assertEqual(
            publish_instagram.validate_image_url("https://example.com/post.png"),
            "https://example.com/post.png",
        )

    def test_create_container_uses_bearer_header_and_caption(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"id": "container-id"}
        session = Mock()
        session.post.return_value = response

        container_id = publish_instagram.create_image_container(
            session,
            self.config,
            "https://example.com/post.png",
            "Approved caption",
        )

        self.assertEqual(container_id, "container-id")
        call = session.post.call_args
        self.assertEqual(
            call.args[0],
            "https://graph.instagram.com/v25.0/17841412762716180/media",
        )
        self.assertEqual(call.kwargs["data"]["caption"], "Approved caption")
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            "Bearer secret-instagram-token",
        )
        self.assertNotIn("access_token", call.kwargs["data"])

    def test_carousel_item_uses_public_url_without_caption(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"id": "child-id"}
        session = Mock()
        session.post.return_value = response

        child_id = publish_instagram.create_carousel_item_container(
            session,
            self.config,
            "https://example.com/source.jpg",
        )

        self.assertEqual(child_id, "child-id")
        data = session.post.call_args.kwargs["data"]
        self.assertEqual(data["image_url"], "https://example.com/source.jpg")
        self.assertEqual(data["is_carousel_item"], "true")
        self.assertNotIn("caption", data)

    def test_carousel_parent_preserves_child_order_and_caption(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"id": "carousel-id"}
        session = Mock()
        session.post.return_value = response

        carousel_id = publish_instagram.create_carousel_container(
            session,
            self.config,
            ["generated-child", "tweet-child-1", "tweet-child-2"],
            "Approved caption",
        )

        self.assertEqual(carousel_id, "carousel-id")
        data = session.post.call_args.kwargs["data"]
        self.assertEqual(data["media_type"], "CAROUSEL")
        self.assertEqual(
            data["children"],
            "generated-child,tweet-child-1,tweet-child-2",
        )
        self.assertEqual(data["caption"], "Approved caption")

    @patch("tools.news.publish_instagram.time.sleep")
    def test_waits_until_container_is_finished(self, mock_sleep) -> None:
        processing = Mock()
        processing.ok = True
        processing.status_code = 200
        processing.json.return_value = {"status_code": "IN_PROGRESS"}
        finished = Mock()
        finished.ok = True
        finished.status_code = 200
        finished.json.return_value = {"status_code": "FINISHED"}
        session = Mock()
        session.get.side_effect = [processing, finished]

        result = publish_instagram.wait_for_container(
            session,
            self.config,
            "container-id",
            attempts=2,
            interval_seconds=0,
        )

        self.assertEqual(result["status_code"], "FINISHED")
        mock_sleep.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
