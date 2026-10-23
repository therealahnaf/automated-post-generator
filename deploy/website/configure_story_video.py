"""Merge only approved video credentials into production's existing private envs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import dotenv_values, set_key

CREDENTIAL_KEYS = (
    "OPENROUTER_API_KEY",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "YOUTUBE_CHANNEL_ID",
    "YOUTUBE_SCOPES",
)
TELEGRAM_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ALLOWED_USER_IDS")


def configure(publisher_env: Path, shared_dir: Path, credentials_file: Path) -> dict:
    incoming = dotenv_values(credentials_file)
    publisher, website = (
        dotenv_values(publisher_env),
        dotenv_values(shared_dir / ".env"),
    )
    if not all(incoming.get(key) for key in CREDENTIAL_KEYS if key != "YOUTUBE_SCOPES"):
        raise ValueError("Required YouTube/OpenRouter credentials are missing.")
    if (
        not publisher.get("TELEGRAM_BOT_TOKEN")
        or not publisher.get("TELEGRAM_CHAT_ID")
        or not publisher.get("CONTENT_API_KEY")
        or publisher.get("CONTENT_API_KEY") != website.get("CONTENT_API_KEY")
    ):
        raise ValueError(
            "Production Telegram or matching content API credentials are missing."
        )
    if str(publisher["TELEGRAM_CHAT_ID"]).startswith("-") and not publisher.get(
        "TELEGRAM_ALLOWED_USER_IDS"
    ):
        raise ValueError("Group chat approvals require TELEGRAM_ALLOWED_USER_IDS.")
    volume = shared_dir / "story-videos"
    volume.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        volume.chmod(0o700)
        os.chown(volume, 10001, 10001)  # Same non-root UID in API and both workers.
    values = {key: incoming.get(key) or "" for key in CREDENTIAL_KEYS}
    targets = {
        publisher_env: {
            **values,
            "STORY_VIDEO_API_URL": "http://127.0.0.1:18501",
            "STORY_VIDEO_OUTPUT_DIR": str(volume),
        },
        shared_dir / ".env": {
            **values,
            **{key: publisher.get(key) or "" for key in TELEGRAM_KEYS},
            **{
                key: publisher.get(key) or default
                for key, default in (
                    ("FXTWITTER_API_BASE", "https://api.fxtwitter.com"),
                    ("VXTWITTER_API_BASE", "https://api.vxtwitter.com"),
                )
            },
        },
    }
    for target, updates in targets.items():
        for key, value in updates.items():
            set_key(str(target), key, value)
        if os.name != "nt":
            target.chmod(0o600)
    return {
        "configured": True,
        "youtube_channel_id": values["YOUTUBE_CHANNEL_ID"],
        "credential_values_hidden": True,
        "media_directory": str(volume),
    }


def main(argv: list[str] | None = None) -> int:
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials-file",
        type=Path,
        required=True,
        help="Private, mode-600 dotenv file transferred over SSH; values are never printed.",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            configure(
                Path("/root/automated-post-generator/.env"),
                Path("/opt/thebitstoday/shared"),
                args.credentials_file,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
