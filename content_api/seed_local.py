"""Load existing frontend archive fixtures into PostgreSQL through the real API.

Explicit local-only demo import, never publishes to social platforms. Does not
claim these fixtures were newly published; records are marked is_demo.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .config import api_key, database_url

ROOT = Path(__file__).resolve().parents[1]
DEMO_BACKGROUNDS = {
    "zai-data-center": ("generated_background", "output/china-zai-data-center-background.png"),
    "gemini-flash": ("generated_background", "output/model-workflow-gemini/background.png"),
    "twitch-ai": ("bundled_background", "assets/fonts/images/bg-1.png"),
    "human-purpose": ("bundled_background", "assets/fonts/images/bg-1.png"),
    "ai-cybersecurity": (
        "generated_background",
        "output/news-polymarket-2081732508941517284/post/01-primary-background.png",
    ),
}


def main():
    from urllib.parse import urlparse

    if urlparse(database_url()).hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Demo seed is restricted to a local database.")
    text = (ROOT / "aggregated-news/src/data/articles.ts").read_text(encoding="utf-8")
    # Existing fixtures are deliberately explicit TS objects with string fields.
    records = re.findall(r"\{\s*id:.*?readingMinutes:.*?\}", text, re.DOTALL)
    for index, record in enumerate(records):

        def field(name, record=record):
            match = re.search(rf"\b{name}:\s*([\"\'])(.*?)\1", record, re.DOTALL)
            if not match:
                raise ValueError(f"Missing fixture field {name}")
            return match.group(2)

        kind = (
            "model"
            if field("id") == "gemini-flash"
            else "informative"
            if field("id") == "human-purpose"
            else "news"
        )
        payload = {
            "external_key": f"local-demo:{field('id')}",
            "title": field("title"),
            "description": field("description"),
            "workflow_type": kind,
            "sources": [{"label": field("sourceName"), "url": field("sourceUrl")}],
            "published_at": (
                datetime(2026, 9, 25, tzinfo=timezone.utc) - timedelta(minutes=index)
            ).isoformat(),
            "is_demo": True,
        }
        path = ROOT / "aggregated-news/public" / field("image").lstrip("/")
        with path.open("rb") as media:
            response = requests.post(
                "http://127.0.0.1:8001/api/posts",
                headers={"Authorization": f"Bearer {api_key()}"},
                data={"document": json.dumps(payload)},
                files=[("files", (path.name, media))],
                timeout=60,
            )
        response.raise_for_status()
        print(field("id"), response.json()["status"])
        background = DEMO_BACKGROUNDS.get(field("id"))
        if background:
            asset_type, relative_path = background
            asset_path = ROOT / relative_path
            if asset_path.is_file():
                with asset_path.open("rb") as asset:
                    asset_response = requests.post(
                        f"http://127.0.0.1:8001/api/posts/{response.json()['post']['id']}/assets",
                        headers={"Authorization": f"Bearer {api_key()}"},
                        data={"document": json.dumps({"assets": [{"asset_type": asset_type}]})},
                        files=[("files", (asset_path.name, asset))],
                        timeout=60,
                    )
                asset_response.raise_for_status()
    video = ROOT / "output/reel-test-unitree/reusable-workflow-test-6s.mp4"
    if video.is_file():
        payload = {
            "external_key": "local-demo:unitree-reel",
            "workflow_type": "reel",
            "title": "Unitree’s AS2-W in motion",
            "description": "A short reel from the existing Unitree AS2-W workflow test.",
            "sources": [
                {
                    "label": "Unitree Robotics",
                    "url": "https://x.com/UnitreeRobotics/status/2080549171661295907",
                }
            ],
            "published_at": "2026-09-24T00:00:00Z",
            "is_demo": True,
        }
        with video.open("rb") as media:
            response = requests.post(
                "http://127.0.0.1:8001/api/posts",
                headers={"Authorization": f"Bearer {api_key()}"},
                data={"document": json.dumps(payload)},
                files=[("files", (video.name, media))],
                timeout=60,
            )
        response.raise_for_status()
        print("unitree-reel", response.json()["status"])


if __name__ == "__main__":
    main()
