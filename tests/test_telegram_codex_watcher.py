import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import telegram_codex_watcher as watcher


class TelegramCodexWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.connection = watcher.connect_database(self.root / "watcher.sqlite3")
        self.config = watcher.Config(
            telegram_token="secret",
            telegram_chat_id="-99",
            allowed_user_ids=frozenset({"7"}),
            repo_dir=self.root,
            state_dir=self.root / "state",
            database_path=self.root / "watcher.sqlite3",
            codex_bin=self.root / "codex",
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def insert_job(self, *, status: str = "awaiting_approval") -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO jobs(
                source_update_id, chat_id, source_message_id, sender_id,
                request_text, status, session_id, turn_count, updated_at
            ) VALUES (10, '-99', 20, '7', 'request', ?, 'session-1', 1, ?)
            """,
            (status, watcher.utc_now()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def test_initial_prompt_requires_preview_and_not_automatic_publish(self) -> None:
        prompt = watcher.build_initial_prompt(3, "https://x.com/example/status/1")
        self.assertTrue(prompt.startswith("Read AGENTS.md\n\n"))
        self.assertIn("Do not publish in\nthis turn", prompt)
        self.assertNotIn(
            "NO NEED TO SEND PREVIEW. AUTOMATICALLY POST THE GENERATED POST",
            prompt,
        )

    def test_revision_prompt_cannot_be_mistaken_for_approval(self) -> None:
        prompt = watcher.build_revision_prompt("Make the headline shorter")
        self.assertIn("not approval", prompt)
        self.assertIn("Do not publish", prompt)
        self.assertIn("Make the headline shorter", prompt)

    def test_blank_optional_paths_use_watcher_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "secret",
                "TELEGRAM_CHAT_ID": "-99",
                "CODEX_WATCHER_REPO": "",
                "CODEX_WATCHER_STATE_DIR": "",
                "CODEX_WATCHER_DB": "",
            },
            clear=False,
        ):
            config = watcher.load_config()
        self.assertEqual(config.repo_dir, watcher.REPO_DIR)
        self.assertEqual(config.state_dir, watcher.REPO_DIR / ".automation/watcher")
        self.assertEqual(config.database_path, config.state_dir / "watcher.sqlite3")

    def test_parse_message_requires_configured_chat_and_sender(self) -> None:
        update = {
            "update_id": 30,
            "message": {
                "message_id": 40,
                "date": 1_700_000_000,
                "chat": {"id": -99},
                "from": {"id": 7, "is_bot": False},
                "text": " yes ",
                "reply_to_message": {"message_id": 25},
            },
        }
        message = watcher.parse_message(update, self.config)
        self.assertEqual(message.text, "yes")
        self.assertEqual(message.reply_to_message_id, 25)
        update["message"]["from"]["id"] = 8
        self.assertIsNone(watcher.parse_message(update, self.config))

    def test_preview_registration_invalidates_the_previous_package(self) -> None:
        job_id = self.insert_job()
        first = self.root / "first.json"
        second = self.root / "second.json"
        first.write_text(
            json.dumps({"photo_message_ids": [101], "description_message_ids": [102]}),
            encoding="utf-8",
        )
        second.write_text(
            json.dumps({"photo_message_ids": [201], "description_message_ids": [202]}),
            encoding="utf-8",
        )
        watcher.register_preview(self.connection, job_id, 1, first)
        watcher.register_preview(self.connection, job_id, 2, second)

        old_job, old_active = watcher.preview_job_for_reply(self.connection, 101)
        new_job, new_active = watcher.preview_job_for_reply(self.connection, 202)
        self.assertEqual(old_job["id"], job_id)
        self.assertFalse(old_active)
        self.assertEqual(new_job["id"], job_id)
        self.assertTrue(new_active)

    def test_migrates_offset_from_preserved_cron_queue(self) -> None:
        automation = self.root / ".automation"
        automation.mkdir()
        old_database = automation / "telegram_codex_queue.sqlite3"
        old_connection = sqlite3.connect(old_database)
        old_connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
        old_connection.execute(
            "INSERT INTO settings(key, value) VALUES ('telegram_offset', '1234')"
        )
        old_connection.commit()
        old_connection.close()

        self.assertEqual(watcher.migrate_queue_offset(self.config, self.connection), 1234)
        self.assertEqual(watcher.get_setting(self.connection, "telegram_offset"), "1234")

    def test_extracts_session_id_from_jsonl(self) -> None:
        output = self.root / "codex.jsonl"
        output.write_text(
            '{"type":"turn.started"}\n'
            '{"type":"thread.started","thread_id":"session-42"}\n',
            encoding="utf-8",
        )
        self.assertEqual(watcher.parse_session_id(output), "session-42")

    def test_managed_cron_removal_preserves_other_entries(self) -> None:
        crontab = "\n".join(
            [
                "0 3 * * * /usr/local/bin/backup",
                watcher.CRON_BEGIN,
                "7 * * * * old-watcher",
                watcher.CRON_END,
            ]
        )
        self.assertEqual(
            watcher.without_managed_cron_block(crontab),
            "0 3 * * * /usr/local/bin/backup",
        )


if __name__ == "__main__":
    unittest.main()
