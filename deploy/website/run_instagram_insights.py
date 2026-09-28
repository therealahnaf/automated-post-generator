"""Call the private refresh API in pages from the VM's system cron."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values


def main() -> int:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    base = (env.get("CONTENT_API_URL") or "").rstrip("/")
    key = env.get("CONTENT_API_KEY") or ""
    if urlsplit(base).hostname not in {"localhost", "127.0.0.1"} or not key:
        print("Missing loopback content API URL or key.", file=sys.stderr)
        return 2
    endpoint = base + "/api/internal/instagram-insights/refresh"
    after = None
    total, failed = 0, 0
    while True:
        params = {"limit": 25}
        if after:
            params["after"] = after
        try:
            response = requests.post(
                endpoint, params=params, headers={"Authorization": f"Bearer {key}"},
                timeout=900,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"Insight API request failed: {type(exc).__name__}", file=sys.stderr)
            return 1
        total += data["succeeded"]
        failed += data["failed"]
        for item in data["failures"]:
            print(f"Post {item['post_id']}: {item['reason']}", file=sys.stderr)
        after = data["next_after"]
        if not after:
            break
    print(f"Instagram insight refresh: {total} updated, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
