"""Queue the independent Tokens For Thought YouTube branch for interactive use."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.thoughts.youtube_companion import api_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("x_url")
    parser.add_argument(
        "--idempotency-key",
        required=True,
        help="Reuse this non-secret key when retrying the same request.",
    )
    parser.add_argument(
        "--reply-to", type=int, help="Optional Telegram source message ID."
    )
    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        url, headers = api_settings()
        response = requests.post(
            url,
            headers={**headers, "Idempotency-Key": args.idempotency_key},
            json={
                "x_url": args.x_url,
                "send_telegram_preview": True,
                "telegram_reply_to_message_id": args.reply_to,
            },
            timeout=(3, 15),
        )
        if response.status_code != 202:
            raise ValueError
        result = response.json()
        print(
            json.dumps(
                {
                    "job_id": result["id"],
                    "status": result["status"],
                    "status_url": result["status_url"],
                }
            )
        )
        return 0
    except (requests.RequestException, RuntimeError, ValueError, KeyError):
        print(
            "YouTube companion was not confirmed. Check API/worker configuration; retry with the same idempotency key.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
