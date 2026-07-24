import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.reels import publish_instagram_reel


class PublishInstagramReelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimpleNamespace(
            user_id="17841400000000000",
            access_token="secret-token",
            graph_version="v25.0",
        )

    def test_stages_content_hashed_video_at_stable_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video_path = root / "approved.mp4"
            video_path.write_bytes(b"approved-video")
            host = publish_instagram_reel.MediaHostConfig(
                directory=root / "public",
                base_url="https://media.example/reels",
            )
            destination, url = publish_instagram_reel.stage_local_reel(
                video_path,
                host,
            )

            self.assertEqual(destination.read_bytes(), b"approved-video")
            self.assertTrue(destination.name.startswith("reel-"))
            self.assertEqual(
                url,
                f"https://media.example/reels/{destination.name}",
            )

    def test_media_host_requires_https(self) -> None:
        with patch.dict(
            os.environ,
            {
                "INSTAGRAM_REEL_MEDIA_DIR": "/tmp/reels",
                "INSTAGRAM_REEL_MEDIA_BASE_URL": "http://media.example/reels",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "public HTTPS"):
                publish_instagram_reel.load_media_host_config()

    def test_verifies_hosted_video_type_and_size(self) -> None:
        response = Mock(
            ok=True,
            status_code=200,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": "14",
            },
        )
        session = Mock()
        session.head.return_value = response

        publish_instagram_reel.verify_hosted_reel(
            session,
            "https://media.example/reel.mp4",
            14,
        )

        session.head.assert_called_once_with(
            "https://media.example/reel.mp4",
            allow_redirects=True,
            timeout=(10, 30),
        )

    def test_processing_error_preserves_container_id(self) -> None:
        with patch.object(
            publish_instagram_reel,
            "wait_for_container",
            side_effect=RuntimeError(
                "Instagram container failed with status ERROR: ERROR"
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "container-789 failed processing",
            ):
                publish_instagram_reel.wait_for_reel_container(
                    Mock(),
                    self.config,
                    "container-789",
                )

    def test_local_video_validation_rejects_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "empty.mp4"
            video_path.touch()
            with self.assertRaisesRegex(ValueError, "cannot be empty"):
                publish_instagram_reel.validate_video_file(video_path)


if __name__ == "__main__":
    unittest.main()
