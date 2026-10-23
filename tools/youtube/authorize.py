"""Authorize a YouTube channel without uploading or changing channel content."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


ROOT = Path(__file__).resolve().parents[2]
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
TOKEN_URI = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def read_settings(env_file: Path) -> dict[str, str]:
    """Prefer exported environment settings over the private dotenv file."""
    values = dotenv_values(env_file)
    names = (
        "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN",
        "YOUTUBE_CHANNEL_ID", "YOUTUBE_SCOPES",
    )
    return {name: os.environ.get(name, values.get(name) or "") for name in names}


def identify_channel(credentials: Credentials) -> dict[str, str]:
    """Read the authorized channel, never search for a similarly named channel."""
    with AuthorizedSession(credentials) as session:
        response = session.get(
            CHANNELS_URL, params={"part": "snippet", "mine": "true"}, timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Channel check returned HTTP {response.status_code}. Check that "
                "YouTube Data API v3 is enabled and both permissions were approved."
            )
        items = response.json().get("items", [])
    if len(items) != 1 or not items[0].get("id"):
        raise RuntimeError("Expected exactly one authorized YouTube channel.")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def check_scopes(credentials: Credentials) -> None:
    granted = credentials.granted_scopes
    if granted is not None and not set(SCOPES).issubset(set(granted)):
        raise RuntimeError("Both upload and read-only channel permissions must be approved.")


def save_authorization(env_file: Path, credentials: Credentials, channel_id: str) -> None:
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token; nothing was saved.")
    check_scopes(credentials)
    # dotenv performs atomic file replacement; never write tokens to logs or metadata.
    set_key(str(env_file), "YOUTUBE_REFRESH_TOKEN", credentials.refresh_token)
    set_key(str(env_file), "YOUTUBE_CHANNEL_ID", channel_id)
    set_key(str(env_file), "YOUTUBE_SCOPES", " ".join(SCOPES))
    if os.name != "nt":
        env_file.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--no-browser", action="store_true", help="Print the consent URL for an SSH tunnel.")
    parser.add_argument("--status", action="store_true", help="Refresh saved access and check the channel without browser consent.")
    parser.add_argument("--reauthorize", action="store_true", help="Explicitly replace an existing authorization.")
    parser.add_argument("--expected-channel-id", help="Reject a different channel before saving.")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.timeout <= 0:
        parser.error("Use a valid nonzero port and positive timeout.")
    env_file = args.env_file.resolve()
    settings = read_settings(env_file)
    if not settings["YOUTUBE_CLIENT_ID"] or not settings["YOUTUBE_CLIENT_SECRET"]:
        parser.error("Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in the private .env first.")
    try:
        if args.status:
            if not settings["YOUTUBE_REFRESH_TOKEN"]:
                raise RuntimeError("No saved authorization. Run this helper without --status first.")
            credentials = Credentials(
                None, refresh_token=settings["YOUTUBE_REFRESH_TOKEN"],
                token_uri=TOKEN_URI, client_id=settings["YOUTUBE_CLIENT_ID"],
                client_secret=settings["YOUTUBE_CLIENT_SECRET"],
                scopes=settings["YOUTUBE_SCOPES"].split() or list(SCOPES),
            )
            credentials.refresh(Request())
            check_scopes(credentials)
        else:
            if settings["YOUTUBE_REFRESH_TOKEN"] and not args.reauthorize:
                raise RuntimeError("Authorization already exists. Use --status, or --reauthorize to replace it.")
            flow = InstalledAppFlow.from_client_config(
                {"installed": {
                    "client_id": settings["YOUTUBE_CLIENT_ID"],
                    "client_secret": settings["YOUTUBE_CLIENT_SECRET"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": TOKEN_URI,
                    "redirect_uris": ["http://localhost"],
                }},
                scopes=list(SCOPES), autogenerate_code_verifier=True,
            )
            print("Select TokensForThought in Google's consent screen. No video will be uploaded.", flush=True)
            credentials = flow.run_local_server(
                host="127.0.0.1", port=args.port, open_browser=not args.no_browser,
                timeout_seconds=args.timeout, access_type="offline", prompt="consent",
                success_message="Google authorization received. You may close this tab and return to the terminal.",
            )
            check_scopes(credentials)
        channel = identify_channel(credentials)
        expected = args.expected_channel_id or settings["YOUTUBE_CHANNEL_ID"]
        if expected and channel["id"] != expected:
            raise RuntimeError("The authorized channel does not match the expected channel ID; credentials were not saved.")
        if not args.status:
            save_authorization(env_file, credentials, channel["id"])
        print(json.dumps({
            "authorized": True, "channel": channel,
            "credentials_saved": not args.status, "uploaded": False,
        }, ensure_ascii=True))
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # OAuth exceptions can contain sensitive HTTP details. Do not dump them.
        print(f"Authorization failed ({type(exc).__name__}). Check consent/test-user settings or retry the helper.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
