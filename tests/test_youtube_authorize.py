import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

from dotenv import dotenv_values
from tools.youtube import authorize


class YouTubeAuthorizeTests(unittest.TestCase):
    def settings(self, **updates):
        values = {
            "YOUTUBE_CLIENT_ID": "test-client", "YOUTUBE_CLIENT_SECRET": "test-secret",
            "YOUTUBE_REFRESH_TOKEN": "", "YOUTUBE_CHANNEL_ID": "", "YOUTUBE_SCOPES": "",
        }
        values.update(updates)
        return values

    def credentials(self):
        return Mock(refresh_token="private-refresh", granted_scopes=list(authorize.SCOPES))

    def test_authorize_saves_only_private_settings_and_never_prints_tokens(self):
        credentials = self.credentials()
        flow = Mock()
        flow.run_local_server.return_value = credentials
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / ".env"
            env_file.write_text("UNRELATED_SETTING=keep\n", encoding="utf-8")
            with patch.object(authorize, "read_settings", return_value=self.settings()), patch.object(
                authorize.InstalledAppFlow, "from_client_config", return_value=flow
            ) as factory, patch.object(authorize, "identify_channel", return_value={"id": "UCtest", "title": "TokensForThought"}), redirect_stdout(output):
                self.assertEqual(authorize.main(["--env-file", str(env_file), "--no-browser"]), 0)
            values = dotenv_values(env_file)
            self.assertEqual(values["UNRELATED_SETTING"], "keep")
            self.assertEqual(values["YOUTUBE_REFRESH_TOKEN"], "private-refresh")
            self.assertEqual(values["YOUTUBE_CHANNEL_ID"], "UCtest")
        self.assertNotIn("private-refresh", output.getvalue())
        self.assertNotIn("test-secret", output.getvalue())
        self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
        options = flow.run_local_server.call_args.kwargs
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertFalse(options["open_browser"])
        self.assertEqual(options["access_type"], "offline")

    def test_channel_mismatch_never_saves(self):
        flow = Mock()
        flow.run_local_server.return_value = self.credentials()
        with patch.object(authorize, "read_settings", return_value=self.settings()), patch.object(
            authorize.InstalledAppFlow, "from_client_config", return_value=flow
        ), patch.object(authorize, "identify_channel", return_value={"id": "UCwrong", "title": "Other"}), patch.object(
            authorize, "save_authorization"
        ) as save, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(authorize.main(["--expected-channel-id", "UCexpected"]), 1)
        save.assert_not_called()

    def test_existing_authorization_does_not_open_browser(self):
        with patch.object(authorize, "read_settings", return_value=self.settings(YOUTUBE_REFRESH_TOKEN="existing")), patch.object(
            authorize.InstalledAppFlow, "from_client_config"
        ) as factory, redirect_stderr(io.StringIO()):
            self.assertEqual(authorize.main([]), 1)
        factory.assert_not_called()

    def test_status_refreshes_without_browser_or_writes(self):
        credentials = self.credentials()
        with patch.object(authorize, "read_settings", return_value=self.settings(YOUTUBE_REFRESH_TOKEN="existing")), patch.object(
            authorize, "Credentials", return_value=credentials
        ), patch.object(authorize, "identify_channel", return_value={"id": "UCtest", "title": "TokensForThought"}), patch.object(
            authorize.InstalledAppFlow, "from_client_config"
        ) as factory, patch.object(authorize, "save_authorization") as save, redirect_stdout(io.StringIO()):
            self.assertEqual(authorize.main(["--status"]), 0)
        credentials.refresh.assert_called_once()
        factory.assert_not_called()
        save.assert_not_called()

    def test_missing_refresh_token_or_partial_consent_never_writes(self):
        for credentials in (
            Mock(refresh_token=None, granted_scopes=list(authorize.SCOPES)),
            Mock(refresh_token="private", granted_scopes=[authorize.SCOPES[0]]),
        ):
            with patch.object(authorize, "set_key") as save:
                with self.assertRaises(RuntimeError):
                    authorize.save_authorization(Path("unused.env"), credentials, "UCtest")
                save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
