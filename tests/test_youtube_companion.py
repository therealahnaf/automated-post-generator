"""Watcher integration does not mix carousel and YouTube approvals."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import requests

from tools.news import telegram_codex_watcher as watcher
from tools.thoughts import youtube_companion as companion


class CompanionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.conn = watcher.connect_database(root / "watcher.sqlite3")
        self.addCleanup(self.conn.close)
        self.config = watcher.Config(
            "secret",
            "-99",
            frozenset({"7"}),
            root,
            root,
            root / "watcher.sqlite3",
            root / "codex",
        )
        environment = patch.dict(
            os.environ,
            {
                "STORY_VIDEO_API_URL": "http://127.0.0.1:8001",
                "CONTENT_API_KEY": "private-api-key",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.uuid = uuid4()

    def queue(self, workflow="informative", update_id=10):
        message = watcher.TelegramMessage(
            update_id,
            update_id + 100,
            "-99",
            "7",
            "https://x.com/example/status/123",
            None,
            watcher.utc_now(),
        )
        job_id = watcher.enqueue_request(self.conn, message)
        watcher.select_workflow(self.conn, job_id, workflow)
        return watcher.claim_next_job(self.conn)

    def test_only_informative_claim_enqueues_companion_once(self):
        job = self.queue()
        companion.enqueue(self.conn, job)
        self.conn.commit()
        self.queue("news", update_id=11)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM youtube_companions").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM jobs WHERE id=?", (job["id"],)
            ).fetchone()[0],
            "generating",
        )

    def test_request_timeout_reuses_same_idempotency_key_and_does_not_change_carousel(
        self,
    ):
        job = self.queue()
        session = Mock()
        session.post.side_effect = requests.Timeout("sensitive-token")
        result = companion.flush_one(self.conn, session)
        self.assertFalse(result["submitted"])
        first_key = session.post.call_args.kwargs["headers"]["Idempotency-Key"]
        self.conn.execute("UPDATE youtube_companions SET next_attempt=0")
        self.conn.commit()
        session.post.side_effect = None
        session.post.return_value = Mock(status_code=202)
        session.post.return_value.json.return_value = {"id": str(self.uuid)}
        result = companion.flush_one(self.conn, session)
        self.assertTrue(result["submitted"])
        self.assertEqual(
            session.post.call_args.kwargs["headers"]["Idempotency-Key"], first_key
        )
        self.assertTrue(session.post.call_args.kwargs["json"]["send_telegram_preview"])
        self.assertEqual(session.post.call_args.kwargs["json"]["speech_speed"], 1.3)
        self.assertIsNone(companion.flush_one(self.conn, session))
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()
        self.assertEqual(row["status"], "generating")
        self.assertIsNone(row["session_id"])

    def callback(self, *, sender="7", action="a"):
        return {
            "update_id": 500,
            "callback_query": {
                "id": "callback",
                "from": {"id": int(sender)},
                "message": {"message_id": 888, "chat": {"id": -99}},
                "data": f"tft:{self.uuid.hex}:abcdef123456:{action}",
            },
        }

    def test_youtube_accept_is_not_forwarded_to_codex_or_carousel_approval(self):
        job = self.queue()
        with (
            patch.object(
                companion,
                "forward_decision",
                return_value={
                    "review_status": "approved",
                    "message": "Full video queued for YouTube only.",
                },
            ) as decision,
            patch.object(watcher, "answer_callback"),
            patch.object(watcher, "edit_text"),
            patch.object(watcher, "process_resume") as resume,
        ):
            watcher.handle_update(Mock(), self.config, self.conn, self.callback())
            watcher.handle_update(Mock(), self.config, self.conn, self.callback())
        decision.assert_called_once()
        resume.assert_not_called()
        row = self.conn.execute(
            "SELECT status FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()
        self.assertEqual(row["status"], "generating")
        self.assertEqual(
            self.conn.execute(
                "SELECT kind FROM telegram_events WHERE update_id=500"
            ).fetchone()[0],
            "youtube_approved",
        )

    def test_wrong_actor_is_not_forwarded(self):
        with patch.object(companion, "forward_decision") as decision:
            watcher.handle_update(
                Mock(), self.config, self.conn, self.callback(sender="8")
            )
        decision.assert_not_called()

    def test_forwarding_uses_matching_preview_nonce_actor_and_message(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200)
        session.post.return_value.json.return_value = {
            "review_status": "cancelled",
            "message": "Cancelled",
        }
        callback = watcher.parse_callback(self.callback(action="c"), self.config)
        result = companion.forward_decision(session, callback)
        self.assertEqual(result["review_status"], "cancelled")
        args = session.post.call_args
        self.assertEqual(
            args.args[0],
            f"http://127.0.0.1:8001/api/internal/story-videos/{self.uuid}/decision",
        )
        self.assertEqual(
            args.kwargs["json"],
            {
                "action": "cancel",
                "token": "abcdef123456",
                "chat_id": "-99",
                "message_id": 888,
                "sender_id": "7",
            },
        )

    def test_plain_http_remote_host_is_rejected(self):
        with (
            patch.dict(os.environ, {"STORY_VIDEO_API_URL": "http://remote.example"}),
            self.assertRaises(RuntimeError),
        ):
            companion.api_settings()


if __name__ == "__main__":
    unittest.main()
