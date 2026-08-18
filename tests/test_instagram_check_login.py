import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.instagram import check_login


class FakeClient:
    def __init__(self, username: str = "bits_t0day") -> None:
        self.username = username
        self.loaded = []
        self.login_calls = []
        self.dumped = []

    def load_settings(self, path: Path) -> None:
        self.loaded.append(path)

    def login(self, username: str, password: str) -> bool:
        self.login_calls.append((username, password))
        return True

    def login_by_sessionid(self, session_id: str) -> bool:
        self.login_calls.append(("sessionid", session_id))
        return True

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            username=self.username,
            full_name="Bits Today",
            is_private=False,
            is_verified=False,
        )

    def dump_settings(self, path: Path) -> None:
        self.dumped.append(path)
        path.write_text("{}", encoding="utf-8")


class InstagramCheckLoginTests(unittest.TestCase):
    def test_reads_private_credentials_without_logging_them(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_PRIVATE_USERNAME": "bits_t0day",
                "INSTAGRAM_PRIVATE_PASSWORD": "secret",
            },
            clear=True,
        ):
            self.assertEqual(
                check_login.read_credentials(),
                ("bits_t0day", "secret"),
            )

    def test_verifies_identity_and_saves_reusable_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "state" / "session.json"
            client = FakeClient()
            result = check_login.login_and_verify(
                "bits_t0day",
                "secret",
                session_file=session,
                client=client,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["username"], "bits_t0day")
        self.assertTrue(result["session_saved"])
        self.assertEqual(client.login_calls, [("bits_t0day", "secret")])
        self.assertEqual(client.dumped, [session])
        self.assertNotIn("secret", str(result))

    def test_rejects_an_authenticated_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "did not match"):
                check_login.login_and_verify(
                    "bits_t0day",
                    "secret",
                    session_file=Path(directory) / "session.json",
                    client=FakeClient(username="someone_else"),
                )

    def test_session_id_login_verifies_identity_without_exposing_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "state" / "session.json"
            client = FakeClient()
            result = check_login.login_by_session_id_and_verify(
                "private-session-cookie",
                expected_username="bits_t0day",
                session_file=session,
                client=client,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["username"], "bits_t0day")
        self.assertEqual(
            client.login_calls,
            [("sessionid", "private-session-cookie")],
        )
        self.assertNotIn("private-session-cookie", str(result))

    def test_session_id_import_reuses_existing_device_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "state" / "session.json"
            session.parent.mkdir(parents=True)
            session.write_text("{}", encoding="utf-8")
            client = FakeClient()

            check_login.login_by_session_id_and_verify(
                "private-session-cookie",
                expected_username="bits_t0day",
                session_file=session,
                client=client,
            )

        self.assertEqual(client.loaded, [session])
        self.assertEqual(client.dumped, [session])

    def test_classifies_interactive_verification_errors(self) -> None:
        challenge = type("ChallengeRequired", (Exception,), {})()
        two_factor = type("TwoFactorRequired", (Exception,), {})()

        self.assertEqual(
            check_login.classify_error(challenge),
            "challenge_required",
        )
        self.assertEqual(
            check_login.classify_error(two_factor),
            "two_factor_required",
        )
        self.assertEqual(
            check_login.classify_error(AssertionError("Invalid sessionid")),
            "invalid_sessionid",
        )


if __name__ == "__main__":
    unittest.main()
