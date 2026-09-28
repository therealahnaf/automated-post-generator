"""Copy the existing publisher IG token into the private website deployment env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, set_key


def main() -> None:
    source = Path(__file__).resolve().parents[2] / ".env"
    target = Path("/opt/thebitstoday/shared/.env")
    if not source.is_file() or not target.is_file():
        raise SystemExit("Publisher or website environment file is missing.")
    values = dotenv_values(source)
    token = values.get("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        raise SystemExit("Publisher Instagram token is missing.")
    set_key(str(target), "INSTAGRAM_ACCESS_TOKEN", token)
    set_key(str(target), "INSTAGRAM_GRAPH_API_VERSION", values.get("INSTAGRAM_GRAPH_API_VERSION") or "v25.0")
    os.chmod(target, 0o600)
    print("Website Instagram insight configuration updated.")


if __name__ == "__main__":
    main()
