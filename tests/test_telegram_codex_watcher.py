import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.news import telegram_codex_watcher as watcher


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
        self.assertIn(
            "TELEGRAM REQUEST START\n"
            "https://x.com/example/status/1\n"
            "TELEGRAM REQUEST END",
            prompt,
        )
        self.assertNotIn("classify it once", prompt)
        self.assertIn("Do not publish in\nthis turn", prompt)
        self.assertNotIn(
            "NO NEED TO SEND PREVIEW. AUTOMATICALLY POST THE GENERATED POST",
            prompt,
        )

    def test_manual_workflow_prompt_is_authoritative(self) -> None:
        prompt = watcher.build_initial_prompt(
            3, "https://x.com/example/status/1", "reel"
        )
        self.assertIn("manually selected workflow_type `reel`", prompt)
        self.assertIn("do not reclassify", prompt)

    def test_pending_selection_is_not_claimed(self) -> None:
        message = watcher.TelegramMessage(
            update_id=50,
            message_id=60,
            chat_id="-99",
            sender_id="7",
            text="https://x.com/example/status/1",
            reply_to_message_id=None,
            received_at=watcher.utc_now(),
        )
        job_id = watcher.enqueue_request(self.connection, message)
        self.assertIsNone(watcher.claim_next_job(self.connection))
        self.assertTrue(watcher.select_workflow(self.connection, job_id, "news"))
        claimed = watcher.claim_next_job(self.connection)
        self.assertEqual(claimed["id"], job_id)
        self.assertEqual(claimed["workflow_type"], "news")

    def test_workflow_keyboard_has_all_choices_and_cancel(self) -> None:
        keyboard = watcher.workflow_keyboard(42)
        values = [
            button["callback_data"]
            for row in keyboard["inline_keyboard"]
            for button in row
        ]
        self.assertEqual(
            values,
            [
                "workflow:42:news",
                "workflow:42:model",
                "workflow:42:product",
                "workflow:42:reel",
                "workflow:42:auto",
                "workflow:42:cancel",
            ],
        )

    def test_callback_selection_persists_and_edits_dashboard(self) -> None:
        message = watcher.TelegramMessage(
            update_id=70,
            message_id=80,
            chat_id="-99",
            sender_id="7",
            text="https://x.com/example/status/2",
            reply_to_message_id=None,
            received_at=watcher.utc_now(),
        )
        job_id = watcher.enqueue_request(self.connection, message)
        watcher.set_progress_message(self.connection, job_id, 81)
        update = {
            "update_id": 71,
            "callback_query": {
                "id": "callback-1",
                "data": f"workflow:{job_id}:model",
                "from": {"id": 7, "is_bot": False},
                "message": {"message_id": 81, "chat": {"id": -99}},
            },
        }
        responses = [
            {"ok": True, "result": True},
            {"ok": True, "result": {"message_id": 81}},
        ]
        session = Mock()
        session.post.side_effect = [
            Mock(ok=True, status_code=200, json=Mock(return_value=item))
            for item in responses
        ]
        watcher.handle_update(session, self.config, self.connection, update)
        job = self.connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        self.assertEqual(job["workflow_type"], "model")
        self.assertTrue(session.post.call_args_list[0].args[0].endswith("/answerCallbackQuery"))
        self.assertTrue(session.post.call_args_list[1].args[0].endswith("/editMessageText"))
        rendered = watcher.render_progress(self.connection, job_id)
        self.assertIn("Model Release", rendered)
        self.assertIn("Workflow selected", rendered)

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

    def test_schema_migration_backfills_existing_jobs_as_auto(self) -> None:
        legacy_path = self.root / "legacy.sqlite3"
        legacy = sqlite3.connect(legacy_path)
        legacy.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_update_id INTEGER NOT NULL UNIQUE,
                chat_id TEXT NOT NULL,
                source_message_id INTEGER NOT NULL,
                sender_id TEXT,
                request_text TEXT NOT NULL,
                status TEXT NOT NULL,
                session_id TEXT,
                turn_count INTEGER NOT NULL DEFAULT 0,
                received_at TEXT,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                last_error TEXT,
                final_output TEXT,
                UNIQUE(chat_id, source_message_id)
            );
            INSERT INTO jobs(
                source_update_id, chat_id, source_message_id, request_text,
                status, updated_at
            ) VALUES (1, '-99', 2, 'legacy request', 'queued', 'now');
            """
        )
        legacy.commit()
        legacy.close()
        migrated = watcher.connect_database(legacy_path)
        try:
            row = migrated.execute("SELECT * FROM jobs WHERE id = 1").fetchone()
            self.assertEqual(row["workflow_type"], "auto")
            self.assertIn("progress_message_id", row.keys())
        finally:
            migrated.close()

    def test_extracts_session_id_from_jsonl(self) -> None:
        output = self.root / "codex.jsonl"
        output.write_text(
            '{"type":"turn.started"}\n'
            '{"type":"thread.started","thread_id":"session-42"}\n',
            encoding="utf-8",
        )
        self.assertEqual(watcher.parse_session_id(output), "session-42")

    def test_codex_command_prefix_explicitly_pins_model_and_effort(self) -> None:
        self.assertEqual(
            watcher.codex_command_prefix(self.config),
            [
                str(self.config.codex_bin),
                "--search",
                "--ask-for-approval",
                "never",
                "--model",
                "gpt-5.6-terra",
                "--config",
                'model_reasoning_effort="medium"',
            ],
        )

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
