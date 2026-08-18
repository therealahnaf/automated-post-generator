#!/usr/bin/env python3
"""Verify an Instagram private-API login without printing credentials."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from instagrapi import Client


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION_FILE = (
    PROJECT_ROOT / ".automation" / "instagram-private" / "session.json"
)
USERNAME_VARIABLES = ("INSTAGRAM_PRIVATE_USERNAME", "IG_USERNAME")
PASSWORD_VARIABLES = ("INSTAGRAM_PRIVATE_PASSWORD", "IG_PASSWORD")
SESSION_ID_VARIABLES = ("INSTAGRAM_PRIVATE_SESSIONID", "IG_SESSIONID")
ERROR_STATUSES = {
    "AssertionError": "invalid_sessionid",
    "BadPassword": "bad_password",
    "ChallengeRequired": "challenge_required",
    "CheckpointRequired": "checkpoint_required",
    "LoginRequired": "login_required",
    "PleaseWaitFewMinutes": "rate_limited",
    "TwoFactorRequired": "two_factor_required",
}


def first_environment_value(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def read_credentials() -> tuple[str, str]:
    username = first_environment_value(USERNAME_VARIABLES)
    password = first_environment_value(PASSWORD_VARIABLES)
    missing = []
    if not username:
        missing.append("INSTAGRAM_PRIVATE_USERNAME")
    if not password:
        missing.append("INSTAGRAM_PRIVATE_PASSWORD")
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )
    return username, password


def read_session_id() -> str:
    session_id = first_environment_value(SESSION_ID_VARIABLES)
    if not session_id:
        raise RuntimeError(
            "Missing required environment variable: INSTAGRAM_PRIVATE_SESSIONID"
        )
    return session_id


def safe_account_payload(account: Any, *, session_saved: bool) -> dict[str, Any]:
    return {
        "success": True,
        "status": "authenticated",
        "username": str(getattr(account, "username", "")),
        "full_name": str(getattr(account, "full_name", "")),
        "is_private": bool(getattr(account, "is_private", False)),
        "is_verified": bool(getattr(account, "is_verified", False)),
        "session_saved": session_saved,
    }


def login_and_verify(
    username: str,
    password: str,
    *,
    session_file: Path = DEFAULT_SESSION_FILE,
    save_session: bool = True,
    client: Client | None = None,
) -> dict[str, Any]:
    instagram = client or Client()
    if session_file.is_file():
        instagram.load_settings(session_file)

    if not instagram.login(username, password):
        raise RuntimeError("Instagram rejected the login without an error response.")

    account = instagram.account_info()
    authenticated_username = str(getattr(account, "username", ""))
    if authenticated_username.casefold() != username.casefold():
        raise RuntimeError("Authenticated account did not match requested username.")

    session_saved = False
    if save_session:
        session_file.parent.mkdir(parents=True, exist_ok=True)
        instagram.dump_settings(session_file)
        try:
            session_file.chmod(0o600)
        except OSError:
            pass
        session_saved = True

    return safe_account_payload(account, session_saved=session_saved)


def login_by_session_id_and_verify(
    session_id: str,
    *,
    expected_username: str | None = None,
    session_file: Path = DEFAULT_SESSION_FILE,
    save_session: bool = True,
    client: Client | None = None,
) -> dict[str, Any]:
    instagram = client or Client()
    if session_file.is_file():
        instagram.load_settings(session_file)
    if not instagram.login_by_sessionid(session_id):
        raise RuntimeError(
            "Instagram rejected the session ID without an error response."
        )

    account = instagram.account_info()
    authenticated_username = str(getattr(account, "username", ""))
    if (
        expected_username
        and authenticated_username.casefold() != expected_username.casefold()
    ):
        raise RuntimeError("Authenticated account did not match requested username.")

    session_saved = False
    if save_session:
        session_file.parent.mkdir(parents=True, exist_ok=True)
        instagram.dump_settings(session_file)
        try:
            session_file.chmod(0o600)
        except OSError:
            pass
        session_saved = True

    return safe_account_payload(account, session_saved=session_saved)


def classify_error(error: Exception) -> str:
    return ERROR_STATUSES.get(type(error).__name__, "login_failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "Optional ignored dotenv file. Credentials must use "
            "INSTAGRAM_PRIVATE_USERNAME and INSTAGRAM_PRIVATE_PASSWORD."
        ),
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        default=DEFAULT_SESSION_FILE,
        help=(
            "Ignored Instagrapi session settings file "
            f"(default: {DEFAULT_SESSION_FILE})."
        ),
    )
    parser.add_argument(
        "--no-save-session",
        action="store_true",
        help="Verify the login without saving reusable session settings.",
    )
    parser.add_argument(
        "--sessionid-login",
        action="store_true",
        help=(
            "Use Client.login_by_sessionid() with "
            "INSTAGRAM_PRIVATE_SESSIONID instead of password login."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        if args.env_file:
            if not args.env_file.is_file():
                raise FileNotFoundError(f"Environment file not found: {args.env_file}")
            load_dotenv(args.env_file, override=False)
        if args.sessionid_login:
            session_id = read_session_id()
            expected_username = first_environment_value(USERNAME_VARIABLES)
            result = login_by_session_id_and_verify(
                session_id,
                expected_username=expected_username,
                session_file=args.session_file,
                save_session=not args.no_save_session,
            )
        else:
            username, password = read_credentials()
            result = login_and_verify(
                username,
                password,
                session_file=args.session_file,
                save_session=not args.no_save_session,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "success": False,
                    "status": classify_error(error),
                    "error_type": type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
