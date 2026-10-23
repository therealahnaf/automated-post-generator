"""Independent approval and upload recovery tests; all external writes mocked."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from content_api.app import app
from content_api.story_video.config import Settings
from content_api.story_video.delivery_worker import process_next
from content_api.story_video.errors import PipelineError
from content_api.story_video.repository import PREFIX, JobRepository
from content_api.story_video.review import ReviewRepository, VideoPackage
from content_api.story_video.router import get_settings
from content_api.story_video.schemas import CreateVideo, TelegramDecision
from content_api.story_video.telegram import TelegramPreview, TelegramSettings
from content_api.story_video.youtube import YouTubePublisher, YouTubeSettings

URL = "https://x.com/example/status/123"
VIDEO_ID = "abcde123456"
ENV = {
    "TELEGRAM_BOT_TOKEN": "private-telegram",
    "TELEGRAM_CHAT_ID": "-99",
    "TELEGRAM_ALLOWED_USER_IDS": "7",
    "YOUTUBE_CLIENT_ID": "client",
    "YOUTUBE_CLIENT_SECRET": "secret",
    "YOUTUBE_REFRESH_TOKEN": "refresh",
    "YOUTUBE_CHANNEL_ID": "channel",
}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.jobs = JobRepository(self.root)
        self.reviews = ReviewRepository(self.jobs)
        self.job = self.jobs.create(CreateVideo(x_url=URL), "key")
        self.job_id = self.job["id"]
        self.jobs.claim("generation")
        self.folder = self.root / self.job_id
        (self.folder / "packaging").mkdir(parents=True)
        for name in ("video.mp4", "preview.mp4", "packaging/thumbnail.png"):
            (self.folder / name).write_bytes(b"fixture")
        (self.folder / "packaging/title.txt").write_text(
            "The Freedom We Create May Outgrow Our Control", encoding="utf-8"
        )
        (self.folder / "description.txt").write_text(
            "A philosophical reflection.\nSource: " + URL, encoding="utf-8"
        )
        self.jobs.finish(self.job_id, "generation", duration=45)
        environment = patch.dict(os.environ, ENV)
        environment.start()
        self.addCleanup(environment.stop)

    def preview(self):
        with (
            patch(
                "content_api.story_video.delivery_worker.TelegramPreview"
            ) as telegram,
            patch(
                "content_api.story_video.delivery_worker.YouTubePublisher"
            ) as youtube,
        ):
            telegram.return_value.settings = TelegramSettings(
                "private", "-99", frozenset({"7"})
            )
            telegram.return_value.send.return_value = 888
            youtube.return_value.__enter__.return_value.channel.return_value = {
                "id": "channel",
                "title": "Tokens For Thought",
            }
            self.assertTrue(process_next(self.reviews))
            youtube.return_value.__enter__.return_value.publish.assert_not_called()
            self.assertEqual(
                telegram.return_value.send.call_args.args[2].directory, self.folder
            )
        self.assertEqual(self.reviews.get(self.job_id)["state"], "awaiting_approval")

    def decision(self, action="accept", **changes):
        row = self.reviews.get(self.job_id)
        return TelegramDecision(
            **{
                "action": action,
                "token": row["nonce"],
                "chat_id": "-99",
                "sender_id": "7",
                "message_id": 888,
                **changes,
            }
        )

    def decide(self, action="accept", **changes):
        return self.reviews.decide(
            self.job_id, self.decision(action, **changes), frozenset({"7"}), "-99"
        )

    def test_successful_generation_queues_preview_but_legacy_and_opt_out_do_not(self):
        self.assertEqual(self.reviews.get(self.job_id)["state"], "pending")
        for n, request in enumerate(
            (
                CreateVideo(x_url=URL, send_telegram_preview=False),
                CreateVideo(x_url=URL),
            )
        ):
            job = self.jobs.create(request, "other-" + str(n))
            self.jobs.claim("worker")
            if n == 1:
                with self.jobs.connect() as conn:
                    old = request.model_dump()
                    old.pop("send_telegram_preview")
                    conn.execute(
                        "UPDATE jobs SET request_json=? WHERE id=?",
                        (json.dumps(old), job["id"]),
                    )
            self.jobs.finish(job["id"], "worker", duration=2)
            self.assertIsNone(self.reviews.get(job["id"]))

    def test_preview_then_cancel_never_uploads_or_changes_generation(self):
        self.preview()
        result = self.decide("cancel")
        self.assertEqual(result["review_status"], "cancelled")
        with patch(
            "content_api.story_video.delivery_worker.YouTubePublisher"
        ) as youtube:
            self.assertFalse(process_next(self.reviews))
            youtube.assert_not_called()
        self.assertEqual(self.jobs.get(self.job_id)["status"], "completed")
        with self.assertRaises(PipelineError):
            self.reviews.retry(self.job_id)

    def test_accept_publishes_full_video_once_and_exposes_only_public_receipt(self):
        self.preview()
        self.decide()

        def publish(package, state, checkpoint):
            self.assertEqual(package.directory / "video.mp4", self.folder / "video.mp4")
            checkpoint(
                {
                    "video_id": VIDEO_ID,
                    "upload_uri": "private-upload-session",
                    "thumbnail_done": True,
                }
            )
            return VIDEO_ID

        with (
            patch(
                "content_api.story_video.delivery_worker.YouTubePublisher"
            ) as youtube,
            patch("content_api.story_video.delivery_worker.TelegramPreview"),
        ):
            youtube.return_value.__enter__.return_value.publish.side_effect = publish
            self.assertTrue(process_next(self.reviews))
            self.assertFalse(process_next(self.reviews))
            youtube.return_value.__enter__.return_value.publish.assert_called_once()
        row = self.jobs.get(self.job_id)
        self.assertEqual(row["review"]["state"], "published")
        self.assertNotIn("private-upload-session", json.dumps(row))
        with self.assertRaises(PipelineError):
            self.decide()

    def test_wrong_actor_chat_message_nonce_cannot_approve(self):
        self.preview()
        for changes in (
            {"sender_id": "other"},
            {"chat_id": "other"},
            {"message_id": 999},
            {"token": "000000000000"},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(PipelineError) as error,
            ):
                self.decide(**changes)
            self.assertEqual(error.exception.code, "approval_forbidden")
        self.assertEqual(self.reviews.get(self.job_id)["state"], "awaiting_approval")

    def test_changed_title_invalidates_preview_and_requires_new_approval(self):
        self.preview()
        (self.folder / "packaging/title.txt").write_text(
            "An Entirely Different Philosophical Statement", encoding="utf-8"
        )
        old_nonce = self.decision().token
        with self.assertRaises(PipelineError) as error:
            self.decide()
        self.assertEqual(error.exception.code, "package_changed")
        self.reviews.retry(self.job_id)
        self.assertNotEqual(self.reviews.get(self.job_id)["nonce"], old_nonce)
        self.assertEqual(self.reviews.get(self.job_id)["state"], "pending")

    def test_changed_video_after_accept_is_not_uploaded(self):
        self.preview()
        self.decide()
        (self.folder / "video.mp4").write_bytes(b"changed")
        with (
            patch(
                "content_api.story_video.delivery_worker.YouTubePublisher"
            ) as youtube,
            patch("content_api.story_video.delivery_worker.TelegramPreview"),
            self.assertLogs("content_api.story_video.delivery_worker", level="ERROR"),
        ):
            process_next(self.reviews)
            youtube.assert_not_called()
        self.assertEqual(self.reviews.get(self.job_id)["last_error"], "package_changed")

    def test_two_accept_clicks_only_grant_one_approval(self):
        self.preview()
        decision = self.decision()

        def run(_):
            try:
                return self.reviews.decide(
                    self.job_id, decision, frozenset({"7"}), "-99"
                )["review_status"]
            except PipelineError:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(
                list(executor.map(run, range(2))), ["approved", "stale"]
            )

    def test_failed_telegram_send_does_not_activate_buttons(self):
        with (
            patch(
                "content_api.story_video.delivery_worker.YouTubePublisher"
            ) as youtube,
            patch(
                "content_api.story_video.delivery_worker.TelegramPreview"
            ) as telegram,
            self.assertLogs("content_api.story_video.delivery_worker", level="ERROR"),
        ):
            youtube.return_value.__enter__.return_value.channel.return_value = {
                "id": "channel",
                "title": "Tokens For Thought",
            }
            telegram.return_value.send.side_effect = PipelineError(
                "telegram_delivery_failed", "failed"
            )
            process_next(self.reviews)
        self.assertEqual(self.reviews.get(self.job_id)["state"], "preview_failed")
        with self.assertRaises(PipelineError):
            self.decide()

    def test_two_delivery_workers_cannot_claim_one_review_and_expiry_does_not_republish(
        self,
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(self.reviews.claim, ("one", "two")))
        self.assertEqual(sum(row is not None for row in claims), 1)
        with self.jobs.connect() as conn:
            conn.execute("UPDATE youtube_reviews SET lease_until=0")
        self.assertIsNone(self.reviews.claim("three"))
        self.assertEqual(self.reviews.get(self.job_id)["state"], "preview_failed")

    def test_authenticated_api_decision_and_new_artifacts(self):
        self.preview()
        app.dependency_overrides[get_settings] = lambda: Settings(self.root, "private")
        self.addCleanup(app.dependency_overrides.pop, get_settings)
        with (
            patch("content_api.app.api_key", return_value="a" * 48),
            TestClient(app) as client,
        ):
            base = PREFIX + "/" + self.job_id
            headers = {"Authorization": "Bearer " + "a" * 48}
            for suffix in ("/thumbnail", "/preview"):
                self.assertEqual(client.get(base + suffix).status_code, 401)
                self.assertEqual(
                    client.get(base + suffix, headers=headers).status_code, 200
                )
            self.assertEqual(
                client.post(
                    base + "/decision", json=self.decision().model_dump()
                ).status_code,
                401,
            )
            result = client.post(
                base + "/decision",
                json=self.decision("cancel").model_dump(),
                headers=headers,
            )
            self.assertEqual(result.json()["review_status"], "cancelled")
            self.assertEqual(
                client.post(base + "/retry-delivery", headers=headers).status_code, 409
            )
            status = client.get(base, headers=headers).json()
            self.assertEqual(status["review_status"], "cancelled")
            self.assertNotIn("nonce", status)


class UploadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        (self.folder / "packaging").mkdir()
        (self.folder / "packaging/thumbnail.png").write_bytes(b"thumbnail")
        (self.folder / "video.mp4").write_bytes(b"full video")
        self.package = VideoPackage(
            self.folder,
            "An Open Philosophical Statement",
            "Description",
            "public",
            "channel",
            "hash",
        )
        self.publisher = YouTubePublisher(
            YouTubeSettings("client", "secret", "refresh", "channel")
        )
        self.publisher.session.close()
        self.publisher.session = Mock()
        self.publisher.channel = Mock(return_value={"id": "channel"})
        self.uri = (
            "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=private"
        )

    def response(self, status, *, headers=None, data=None):
        result = Mock(status_code=status, headers=headers or {})
        result.json.return_value = data or {}
        return result

    def test_upload_checkpoints_video_id_before_thumbnail(self):
        self.publisher.session.post.side_effect = [
            self.response(200, headers={"Location": self.uri}),
            self.response(200),
        ]
        self.publisher.session.put.side_effect = [
            self.response(308),
            self.response(201, data={"id": VIDEO_ID}),
        ]
        checkpoints = []
        result = self.publisher.publish(
            self.package, {}, lambda state: checkpoints.append(dict(state))
        )
        self.assertEqual(result, VIDEO_ID)
        self.assertTrue(checkpoints[0]["started"])
        self.assertIn("upload_uri", checkpoints[1])
        self.assertEqual(checkpoints[-2]["video_id"], VIDEO_ID)
        self.assertNotIn("thumbnail_done", checkpoints[-2])
        self.assertTrue(checkpoints[-1]["thumbnail_done"])
        content = self.publisher.session.put.call_args_list[-1]
        self.assertEqual(content.kwargs["data"], b"full video")
        self.assertEqual(content.kwargs["headers"]["Content-Range"], "bytes 0-9/10")
        body = self.publisher.session.post.call_args_list[0].kwargs["json"]
        self.assertEqual(body["status"]["privacyStatus"], "public")

    def test_timeout_after_upload_resumes_same_session_without_duplicate(self):
        self.publisher.session.put.return_value = self.response(
            200, data={"id": VIDEO_ID}
        )
        self.publisher.session.post.return_value = self.response(200)
        result = self.publisher.publish(
            self.package, {"started": True, "upload_uri": self.uri}, Mock()
        )
        self.assertEqual(result, VIDEO_ID)
        self.publisher.session.put.assert_called_once()
        self.publisher.session.post.assert_called_once()
        self.assertIn("thumbnails/set", self.publisher.session.post.call_args.args[0])

    def test_existing_video_only_retries_thumbnail(self):
        self.publisher.session.post.return_value = self.response(403)
        state = {"video_id": VIDEO_ID}
        with self.assertRaises(PipelineError) as error:
            self.publisher.publish(self.package, state, Mock())
        self.assertEqual(error.exception.code, "youtube_thumbnail_failed")
        self.publisher.session.put.assert_not_called()
        self.assertIn("thumbnails/set", self.publisher.session.post.call_args.args[0])

    def test_expired_session_never_creates_new_upload(self):
        self.publisher.session.put.return_value = self.response(404)
        with self.assertRaises(PipelineError):
            self.publisher.publish(
                self.package, {"started": True, "upload_uri": self.uri}, Mock()
            )
        self.publisher.session.post.assert_not_called()

    def test_missing_session_checkpoint_fails_closed(self):
        with self.assertRaises(PipelineError) as error:
            self.publisher.publish(self.package, {"started": True}, Mock())
        self.assertEqual(error.exception.code, "youtube_upload_uncertain")
        self.publisher.session.post.assert_not_called()

    def test_upload_session_host_cannot_exfiltrate_authorization(self):
        for uri in (
            "https://evil.example/upload/youtube/v3/videos",
            "http://www.googleapis.com/upload/youtube/v3/videos",
        ):
            with self.subTest(uri=uri), self.assertRaises(PipelineError):
                self.publisher.publish(self.package, {"upload_uri": uri}, Mock())
        self.publisher.session.put.assert_not_called()


class TelegramTests(unittest.TestCase):
    def test_preview_sends_thumbnail_30_second_file_and_short_independent_callbacks(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "packaging").mkdir()
            (root / "packaging/thumbnail.png").write_bytes(b"image")
            (root / "preview.mp4").write_bytes(b"preview")
            package = VideoPackage(
                root,
                "An Open Statement",
                "The complete description.",
                "public",
                "channel",
                "hash",
            )
            preview = TelegramPreview(
                TelegramSettings("secret", "-99", frozenset({"7"}))
            )
            preview.call = Mock(return_value={"message_id": 888})
            result = preview.send(
                "00000000-0000-0000-0000-000000000001",
                "abcdef123456",
                package,
                "Tokens For Thought",
                10,
            )
            self.assertEqual(result, 888)
            self.assertEqual(
                [call.args[0] for call in preview.call.call_args_list],
                ["sendPhoto", "sendVideo", "sendMessage", "sendMessage"],
            )
            keyboard = json.loads(preview.call.call_args.args[1]["reply_markup"])
            for button in keyboard["inline_keyboard"][0]:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)
                self.assertTrue(button["callback_data"].startswith("tft:"))

    def test_group_needs_allowed_users_and_private_chat_can_use_owner(self):
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_BOT_TOKEN": "secret",
                    "TELEGRAM_CHAT_ID": "-99",
                    "TELEGRAM_ALLOWED_USER_IDS": "",
                },
            ),
            self.assertRaises(PipelineError),
        ):
            TelegramSettings.from_env()
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "secret",
                "TELEGRAM_CHAT_ID": "7",
                "TELEGRAM_ALLOWED_USER_IDS": "",
            },
        ):
            self.assertEqual(
                TelegramSettings.from_env().allowed_users, frozenset({"7"})
            )


if __name__ == "__main__":
    unittest.main()
