import tempfile
import unittest
from pathlib import Path

import telegram_codex_queue as queue


class TelegramCodexQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.connection = queue.connect_database(Path(self.temp_dir.name) / "queue.sqlite3")

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_prompt_has_exact_guarded_automation_instructions(self):
        self.assertEqual(
            queue.build_prompt("https://x.com/example/status/123"),
            "Read AGENTS.md\n\nhttps://x.com/example/status/123\n\n"
            "NO NEED TO SEND PREVIEW. AUTOMATICALLY POST THE GENERATED POST",
        )

    def test_enqueue_is_deduplicated(self):
        # Deliberately insert the newer update first; claiming still follows
        # Telegram update order rather than database insertion order.
        updates = [
            {
                "update_id": 11,
                "message": {
                    "message_id": 2,
                    "date": 1_700_000_001,
                    "chat": {"id": -99},
                    "from": {"id": 7, "is_bot": False},
                    "text": "second",
                },
            },
            {
                "update_id": 10,
                "message": {
                    "message_id": 1,
                    "date": 1_700_000_000,
                    "chat": {"id": -99},
                    "from": {"id": 7, "is_bot": False},
                    "text": "first",
                },
            },
        ]
        self.assertEqual(queue.enqueue_updates(self.connection, updates, "-99", frozenset()), 2)
        self.assertEqual(queue.enqueue_updates(self.connection, updates, "-99", frozenset()), 0)
        self.connection.commit()
        rows = self.connection.execute("SELECT text FROM jobs ORDER BY update_id").fetchall()
        self.assertEqual([row["text"] for row in rows], ["first", "second"])
        self.assertEqual(queue.claim_next_job(self.connection)["text"], "first")

    def test_filters_other_chats_bots_commands_and_disallowed_senders(self):
        template = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": -99},
                "from": {"id": 7, "is_bot": False},
                "text": "publish this",
            },
        }
        wrong_chat = {
            **template,
            "update_id": 2,
            "message": {**template["message"], "chat": {"id": -1}},
        }
        bot = {
            **template,
            "update_id": 3,
            "message": {**template["message"], "from": {"id": 7, "is_bot": True}},
        }
        command = {
            **template,
            "update_id": 4,
            "message": {**template["message"], "text": "/start"},
        }
        self.assertEqual(
            queue.enqueue_updates(
                self.connection, [template, wrong_chat, bot, command], "-99", frozenset({"8"})
            ),
            0,
        )

    def test_failed_job_requires_explicit_retry(self):
        self.connection.execute(
            """
            INSERT INTO jobs(update_id, chat_id, message_id, text, status, created_at)
            VALUES (1, '-99', 1, 'hello', 'failed', '2026-01-01T00:00:00+00:00')
            """
        )
        self.connection.commit()
        self.assertTrue(queue.retry_job(self.connection, 1))
        status = self.connection.execute("SELECT status FROM jobs WHERE id = 1").fetchone()["status"]
        self.assertEqual(status, "queued")
        self.assertFalse(queue.retry_job(self.connection, 1))

    def test_managed_cron_replacement_preserves_other_entries(self):
        existing = "\n".join(
            [
                "0 0 * * * /usr/local/bin/backup",
                queue.CRON_BEGIN,
                "7 * * * * old-command",
                queue.CRON_END,
            ]
        )
        self.assertEqual(
            queue.without_managed_cron_block(existing),
            "0 0 * * * /usr/local/bin/backup",
        )


if __name__ == "__main__":
    unittest.main()
