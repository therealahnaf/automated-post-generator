"""Safe configuration merge and deployment guards; no remote access or secrets."""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

from deploy.website.configure_story_video import configure
from deploy.website.deploy_release import require_idle_story_workers


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.shared = self.root / "shared"
        self.shared.mkdir()
        self.publisher = self.root / "publisher.env"
        self.publisher.write_text(
            "CONTENT_API_KEY=production-key\nTELEGRAM_BOT_TOKEN=production-bot\nTELEGRAM_CHAT_ID=-99\nTELEGRAM_ALLOWED_USER_IDS=7\nINSTAGRAM_ACCESS_TOKEN=keep-production\n",
            encoding="utf-8",
        )
        (self.shared / ".env").write_text(
            "CONTENT_API_KEY=production-key\nPOSTGRES_PASSWORD=keep-database\nR2_SECRET_ACCESS_KEY=keep-storage\n",
            encoding="utf-8",
        )
        self.incoming = self.root / "incoming.env"
        self.incoming.write_text(
            "OPENROUTER_API_KEY=router\nYOUTUBE_CLIENT_ID=client\nYOUTUBE_CLIENT_SECRET=secret\nYOUTUBE_REFRESH_TOKEN=refresh\nYOUTUBE_CHANNEL_ID=channel\n",
            encoding="utf-8",
        )

    def configure(self):
        with patch.object(os, "chown", create=True) as owner:
            result = configure(self.publisher, self.shared, self.incoming)
        if os.name != "nt":
            owner.assert_called_once_with(self.shared / "story-videos", 10001, 10001)
        return result

    def test_merge_preserves_production_credentials_and_updates_only_video_keys(self):
        result = self.configure()
        publisher = dotenv_values(self.publisher)
        website = dotenv_values(self.shared / ".env")
        self.assertEqual(publisher["INSTAGRAM_ACCESS_TOKEN"], "keep-production")
        self.assertEqual(website["POSTGRES_PASSWORD"], "keep-database")
        self.assertEqual(website["R2_SECRET_ACCESS_KEY"], "keep-storage")
        self.assertEqual(website["TELEGRAM_BOT_TOKEN"], "production-bot")
        self.assertEqual(website["YOUTUBE_REFRESH_TOKEN"], "refresh")
        self.assertEqual(publisher["STORY_VIDEO_API_URL"], "http://127.0.0.1:18501")
        self.assertNotIn("refresh", str(result))

    def test_mismatching_api_keys_abort_before_writing(self):
        (self.shared / ".env").write_text(
            "CONTENT_API_KEY=other-key\n", encoding="utf-8"
        )
        original = self.publisher.read_bytes()
        with self.assertRaises(ValueError):
            self.configure()
        self.assertEqual(self.publisher.read_bytes(), original)

    def test_deployment_refuses_active_paid_work_and_approved_uploads(self):
        folder = self.shared / "story-videos"
        folder.mkdir()
        with closing(sqlite3.connect(folder / "jobs.sqlite3")) as conn:
            conn.execute("CREATE TABLE jobs(status TEXT)")
            conn.execute("CREATE TABLE youtube_reviews(state TEXT)")
            conn.execute("INSERT INTO jobs VALUES ('running')")
            conn.commit()
            with self.assertRaises(RuntimeError):
                require_idle_story_workers(self.root)
            conn.execute("UPDATE jobs SET status='completed'")
            conn.execute("INSERT INTO youtube_reviews VALUES ('publishing')")
            conn.commit()
            with self.assertRaises(RuntimeError):
                require_idle_story_workers(self.root)
            conn.execute("UPDATE youtube_reviews SET state='prompting_title'")
            conn.commit()
            with self.assertRaises(RuntimeError):
                require_idle_story_workers(self.root)
            conn.execute("UPDATE youtube_reviews SET state='awaiting_approval'")
            conn.commit()
            require_idle_story_workers(self.root)


if __name__ == "__main__":
    unittest.main()
