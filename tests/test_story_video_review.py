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
from content_api.story_video.schemas import (
    CreateVideo,
    TelegramDecision,
    TelegramTitleReply,
)
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

    def prompt_title(self):
        self.assertEqual(self.decide()["review_status"], "title_pending")
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
            telegram.return_value.ask_title.return_value = 889
            self.assertTrue(process_next(self.reviews))
            youtube.assert_not_called()
        self.assertEqual(self.reviews.get(self.job_id)["state"], "awaiting_title")

    def title_reply(self, **changes):
        return TelegramTitleReply(
            **{
                "chat_id": "-99",
                "sender_id": "7",
                "message_id": 890,
                "reply_to_message_id": 889,
                "text": "My EXACT title: What Comes Next?",
                **changes,
            }
        )

    def submit_title(self, **changes):
        return self.reviews.submit_title(
            self.title_reply(**changes), frozenset({"7"}), "-99"
        )

    def approve_with_title(self):
        self.prompt_title()
        self.assertEqual(self.submit_title()["review_status"], "approved")

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
        self.approve_with_title()

        def publish(package, state, checkpoint):
            self.assertEqual(package.title, "My EXACT title: What Comes Next?")
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

    def test_changed_thumbnail_invalidates_preview_and_requires_new_approval(self):
        self.preview()
        (self.folder / "packaging/thumbnail.png").write_bytes(b"changed thumbnail")
        old_nonce = self.decision().token
        with self.assertRaises(PipelineError) as error:
            self.decide()
        self.assertEqual(error.exception.code, "package_changed")
        self.reviews.retry(self.job_id)
        self.assertNotEqual(self.reviews.get(self.job_id)["nonce"], old_nonce)
        self.assertEqual(self.reviews.get(self.job_id)["state"], "pending")

    def test_changed_video_after_accept_is_not_uploaded(self):
        self.preview()
        self.approve_with_title()
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
                list(executor.map(run, range(2))), ["title_pending", "stale"]
            )

    def test_accept_without_title_never_queues_upload_and_wait_survives_restart(self):
        self.preview()
        self.prompt_title()
        reviews = ReviewRepository(JobRepository(self.root))
        with patch(
            "content_api.story_video.delivery_worker.YouTubePublisher"
        ) as youtube:
            self.assertFalse(process_next(reviews))
            youtube.assert_not_called()
        self.assertIsNone(self.jobs.get(self.job_id)["review"]["title"])
        self.submit_title(text="  A short title  ")
        self.assertEqual(reviews.get(self.job_id)["title"], "A short title")

    def test_title_reply_must_match_prompt_chat_and_original_approver(self):
        self.preview()
        self.prompt_title()
        for changes in (
            {"sender_id": "8"},
            {"chat_id": "7"},
            {"reply_to_message_id": 888},
        ):
            with self.subTest(changes=changes), self.assertRaises(PipelineError):
                self.reviews.submit_title(
                    self.title_reply(**changes), frozenset({"7", "8"}), "-99"
                )
        self.assertEqual(self.reviews.get(self.job_id)["state"], "awaiting_title")

    def test_invalid_title_can_be_retried_without_generating_or_uploading(self):
        self.preview()
        self.prompt_title()
        for text in (
            " ",
            "x" * 101,
            "Two\nLines",
            "<title>",
            "bad\x00text",
            "Two\u2028Lines",
        ):
            with self.subTest(text=text), self.assertRaises(PipelineError) as error:
                self.submit_title(text=text)
            self.assertEqual(error.exception.code, "title_invalid")
            self.assertEqual(self.reviews.get(self.job_id)["state"], "awaiting_title")
        # No AI/word-count requirement or automatic rewriting of user titles.
        self.submit_title(text="Why?")
        self.assertEqual(self.reviews.get(self.job_id)["title"], "Why?")

    def test_cancel_on_title_prompt_prevents_publication(self):
        self.preview()
        self.prompt_title()
        self.assertEqual(
            self.decide("cancel", message_id=889)["review_status"], "cancelled"
        )
        with self.assertRaises(PipelineError):
            self.submit_title()
        self.assertFalse(process_next(self.reviews))

    def test_title_reply_is_idempotent_but_a_second_title_cannot_replace_it(self):
        self.preview()
        self.prompt_title()
        self.assertEqual(self.submit_title()["title"], self.submit_title()["title"])
        self.assertIn("already processed", self.submit_title()["message"])
        with self.assertRaises(PipelineError):
            self.submit_title(message_id=891, text="Changed title")
        self.assertEqual(self.reviews.get(self.job_id)["title_message_id"], 890)

    def test_concurrent_title_replies_choose_only_one_title(self):
        self.preview()
        self.prompt_title()

        def run(index):
            try:
                return self.submit_title(message_id=890 + index, text=f"Title {index}")[
                    "review_status"
                ]
            except PipelineError:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertCountEqual(
                list(executor.map(run, (0, 1))), ["approved", "stale"]
            )

    def test_changed_video_before_title_reply_requires_fresh_preview(self):
        self.preview()
        self.prompt_title()
        (self.folder / "video.mp4").write_bytes(b"changed")
        with self.assertRaises(PipelineError) as error:
            self.submit_title()
        self.assertEqual(error.exception.code, "package_changed")
        self.assertEqual(self.reviews.get(self.job_id)["state"], "preview_failed")

    def test_old_unstarted_generated_title_approval_requires_new_preview(self):
        with self.jobs.connect() as conn:
            conn.execute(
                """UPDATE youtube_reviews SET state='approved',flow_version=1,
                title='An old generated title',approved_by='7' WHERE job_id=?""",
                (self.job_id,),
            )
        row = self.reviews.claim("migration")
        self.assertEqual(row["state"], "delivering")
        self.assertEqual(row["flow_version"], 2)
        self.assertIsNone(row["title"])
        self.assertIsNone(row["approved_by"])

    def test_old_schema_migrates_additively_and_new_jobs_use_manual_title_version(self):
        # Recreate only this temporary fixture's pre-title schema to exercise ALTERs.
        with self.jobs.connect() as conn:
            conn.execute("DROP TABLE youtube_reviews")
            conn.execute("""CREATE TABLE youtube_reviews (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id), state TEXT NOT NULL, nonce TEXT NOT NULL,
                chat_id TEXT, message_id INTEGER, package_hash TEXT, channel_id TEXT,
                privacy TEXT, title TEXT, approved_by TEXT, upload_state TEXT NOT NULL DEFAULT '{}',
                youtube_id TEXT, youtube_url TEXT, last_error TEXT, owner TEXT, lease_until REAL,
                updated_at TEXT NOT NULL)""")
            conn.execute(
                """INSERT INTO youtube_reviews(job_id,state,nonce,title,updated_at)
                VALUES (?,'published','abcdef123456','Original published title','old')""",
                (self.job_id,),
            )
        reviews = ReviewRepository(self.jobs)
        self.assertEqual(reviews.get(self.job_id)["flow_version"], 1)
        self.assertEqual(reviews.get(self.job_id)["title"], "Original published title")
        other = self.jobs.create(CreateVideo(x_url=URL), "new-version")
        self.jobs.claim("new-generation")
        self.jobs.finish(other["id"], "new-generation", duration=3)
        self.assertEqual(reviews.get(other["id"])["flow_version"], 2)
        self.assertEqual(reviews.get(self.job_id)["state"], "published")

    def test_legacy_started_upload_keeps_its_title_and_resume_checkpoint(self):
        self.preview()
        old_title = "A previously approved title"
        (self.folder / "packaging/title.txt").write_text(old_title, encoding="utf-8")
        package = VideoPackage.load(self.folder, "public", "channel", legacy=True)
        saved = {"started": True, "upload_uri": "private-resumable-session"}
        with self.jobs.connect() as conn:
            conn.execute(
                """UPDATE youtube_reviews SET state='publication_failed',flow_version=1,
                title=?,approved_by='7',package_hash=?,upload_state=? WHERE job_id=?""",
                (old_title, package.digest, json.dumps(saved), self.job_id),
            )
        self.reviews.retry(self.job_id)

        def publish(package, state, checkpoint):
            self.assertEqual(package.title, old_title)
            self.assertEqual(state, saved)
            checkpoint({**state, "video_id": VIDEO_ID, "thumbnail_done": True})
            return VIDEO_ID

        with (
            patch(
                "content_api.story_video.delivery_worker.YouTubePublisher"
            ) as youtube,
            patch(
                "content_api.story_video.delivery_worker.TelegramPreview"
            ) as telegram,
        ):
            youtube.return_value.__enter__.return_value.publish.side_effect = publish
            self.assertTrue(process_next(self.reviews))
            telegram.return_value.send.assert_not_called()
            telegram.return_value.ask_title.assert_not_called()
        self.assertEqual(self.reviews.get(self.job_id)["state"], "published")

    def test_title_prompt_lease_expiry_does_not_retry_send_or_upload_automatically(
        self,
    ):
        self.preview()
        self.decide()
        row = self.reviews.claim("prompt-worker")
        self.assertEqual(row["state"], "prompting_title")
        self.assertTrue(self.reviews.heartbeat(self.job_id, "prompt-worker"))
        with self.jobs.connect() as conn:
            conn.execute(
                "UPDATE youtube_reviews SET lease_until=0 WHERE job_id=?",
                (self.job_id,),
            )
        self.assertIsNone(self.reviews.claim("another-worker"))
        self.assertEqual(self.reviews.get(self.job_id)["state"], "title_prompt_failed")

    def test_cancel_during_title_prompt_send_cannot_be_overwritten_by_worker(self):
        self.preview()
        self.decide()
        self.reviews.claim("prompt-worker")
        self.decide("cancel")
        with self.assertRaises(PipelineError):
            self.reviews.update(
                self.job_id,
                "prompt-worker",
                state="awaiting_title",
                title_prompt_id=889,
            )
        self.assertEqual(self.reviews.get(self.job_id)["state"], "cancelled")

    def test_modified_saved_title_invalidates_upload_hash(self):
        self.preview()
        self.approve_with_title()
        with self.jobs.connect() as conn:
            conn.execute(
                "UPDATE youtube_reviews SET title='A different title' WHERE job_id=?",
                (self.job_id,),
            )
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

    def test_title_prompt_delivery_failure_is_recoverable_without_upload(self):
        self.preview()
        self.decide()
        with (
            patch(
                "content_api.story_video.delivery_worker.TelegramPreview"
            ) as telegram,
            patch(
                "content_api.story_video.delivery_worker.YouTubePublisher"
            ) as youtube,
            self.assertLogs("content_api.story_video.delivery_worker", level="ERROR"),
        ):
            telegram.return_value.settings = TelegramSettings(
                "private", "-99", frozenset({"7"})
            )
            telegram.return_value.ask_title.side_effect = PipelineError(
                "telegram_delivery_failed", "failed"
            )
            process_next(self.reviews)
            youtube.assert_not_called()
        self.assertEqual(self.reviews.get(self.job_id)["state"], "title_prompt_failed")
        self.reviews.retry(self.job_id)
        self.assertEqual(self.reviews.get(self.job_id)["state"], "title_pending")

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

    def test_authenticated_title_api_requires_bound_reply_and_returns_exact_title(self):
        self.preview()
        self.prompt_title()
        app.dependency_overrides[get_settings] = lambda: Settings(self.root, "private")
        self.addCleanup(app.dependency_overrides.pop, get_settings)
        with (
            patch("content_api.app.api_key", return_value="a" * 48),
            TestClient(app) as client,
        ):
            headers = {"Authorization": "Bearer " + "a" * 48}
            endpoint = PREFIX + "/title-reply"
            self.assertEqual(
                client.post(endpoint, json=self.title_reply().model_dump()).status_code,
                401,
            )
            self.assertEqual(
                client.post(
                    endpoint,
                    headers=headers,
                    json=self.title_reply(reply_to_message_id=888).model_dump(),
                ).status_code,
                403,
            )
            self.assertEqual(
                client.post(
                    endpoint,
                    headers=headers,
                    json=self.title_reply(text="x" * 101).model_dump(),
                ).status_code,
                422,
            )
            response = client.post(
                endpoint, headers=headers, json=self.title_reply().model_dump()
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["title"], self.title_reply().text)
            self.assertEqual(
                client.get(PREFIX + "/" + self.job_id, headers=headers).json()["title"],
                self.title_reply().text,
            )


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
            self.assertEqual(keyboard["inline_keyboard"][0][0]["text"], "Accept video")
            self.assertNotIn(package.title, str(preview.call.call_args_list))

    def test_title_request_is_a_reply_target_with_cancel_and_no_suggested_title(self):
        preview = TelegramPreview(TelegramSettings("secret", "-99", frozenset({"7"})))
        preview.call = Mock(return_value={"message_id": 889})
        job_id = "00000000-0000-0000-0000-000000000001"
        self.assertEqual(preview.ask_title(job_id, "abcdef123456", 888), 889)
        data = preview.call.call_args.args[1]
        self.assertTrue(data["text"].startswith("YouTube title request · " + job_id))
        self.assertIn("exact title", data["text"])
        keyboard = json.loads(data["reply_markup"])
        self.assertEqual(
            keyboard["inline_keyboard"][0][0]["callback_data"],
            f"tft:{job_id.replace('-', '')}:abcdef123456:c",
        )

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
