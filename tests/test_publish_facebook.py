import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.news import publish_facebook


class PublishFacebookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = publish_facebook.FacebookConfig(
            page_id="123456",
            page_token="secret-page-token",
            graph_version="v25.0",
        )

    def test_publish_requires_exact_confirmation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--confirm yes"):
            publish_facebook.require_publish_confirmation(True, "YES")
        publish_facebook.require_publish_confirmation(True, "yes")
        publish_facebook.require_publish_confirmation(False, None)

    def test_verify_page_rejects_token_for_another_page(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"id": "999", "name": "Wrong Page"}
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "Page-token mismatch"):
            publish_facebook.verify_page(session, self.config)

    def test_publish_photo_uses_bearer_header_and_expected_endpoint(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"id": "photo-id", "post_id": "post-id"}
        session = Mock()
        session.post.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "post.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            result = publish_facebook.publish_photo(
                session,
                self.config,
                image_path,
                "Approved description",
            )

        self.assertEqual(result["post_id"], "post-id")
        call = session.post.call_args
        self.assertEqual(
            call.args[0],
            "https://graph.facebook.com/v25.0/123456/photos",
        )
        self.assertEqual(call.kwargs["data"]["message"], "Approved description")
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            "Bearer secret-page-token",
        )
        self.assertNotIn("access_token", call.kwargs["data"])

    def test_photo_details_select_largest_image_for_instagram(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {
            "id": "photo-id",
            "link": "https://facebook.example/photo-id",
            "images": [
                {"width": 320, "height": 400, "source": "https://cdn/small.jpg"},
                {"width": 1080, "height": 1350, "source": "https://cdn/large.jpg"},
            ],
        }
        session = Mock()
        session.get.return_value = response

        details = publish_facebook.get_photo_details(
            session,
            self.config,
            "photo-id",
        )

        self.assertEqual(details["largest_image_url"], "https://cdn/large.jpg")

    def test_uploads_secondary_photo_as_unpublished(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"id": "secondary-photo-id"}
        session = Mock()
        session.post.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "source.jpg"
            image_path.write_bytes(b"jpeg")
            photo_id = publish_facebook.upload_unpublished_photo(
                session, self.config, image_path
            )

        self.assertEqual(photo_id, "secondary-photo-id")
        call = session.post.call_args
        self.assertTrue(call.args[0].endswith("/123456/photos"))
        self.assertEqual(call.kwargs["data"], {"published": "false"})

    def test_multi_photo_post_preserves_attachment_order(self) -> None:
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"id": "page_post_id"}
        session = Mock()
        session.post.return_value = response

        post_id = publish_facebook.publish_multi_photo_post(
            session,
            self.config,
            ["generated-id", "tweet-photo-1", "tweet-photo-2"],
            "Approved description",
        )

        self.assertEqual(post_id, "page_post_id")
        call = session.post.call_args
        self.assertTrue(call.args[0].endswith("/123456/feed"))
        self.assertEqual(
            call.kwargs["data"]["attached_media[0]"],
            '{"media_fbid":"generated-id"}',
        )
        self.assertEqual(
            call.kwargs["data"]["attached_media[1]"],
            '{"media_fbid":"tweet-photo-1"}',
        )
        self.assertEqual(
            call.kwargs["data"]["attached_media[2]"],
            '{"media_fbid":"tweet-photo-2"}',
        )


if __name__ == "__main__":
    unittest.main()
