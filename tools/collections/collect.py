#!/usr/bin/env python3
"""Collect one X post into a collection: fetch it with its photos and videos now, then record it.

  python tools/collections/collect.py --url <x status url> (--collection <id> | --new "<name>")

Prints JSON: {"collection_id", "collection", "item_id", "count", "photos", "videos"}.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collections import store  # noqa: E402
from tools.reels import generate_reel  # noqa: E402

STATUS_ID = re.compile(r"/status(?:es)?/(\d+)")


def download_videos(status: dict, folder: Path) -> list[Path]:
    """Every video in the post and its quoted post, best quality up to about 5 Mbit/s."""
    saved = []
    for source_index, source in enumerate((status, status.get("quote") or {})):
        media = source.get("media") or {}
        videos = media.get("videos") or [item for item in media.get("all") or []
                                         if isinstance(item, dict) and item.get("type") in ("video", "gif", "animated_gif")]
        for index, video in enumerate(videos):
            candidates = generate_reel.video_candidates({"media": {"videos": [video]}})
            if not candidates:
                continue
            preferred = [item for item in candidates if int(item["bitrate"]) <= 5_000_000] or candidates
            best = max(preferred, key=lambda item: int(item["bitrate"]))
            name = f"{'quote-' if source_index else ''}{source.get('id', 'post')}-video-{index + 1}.mp4"
            saved.append(generate_reel.download_video(best["url"], folder / "videos" / name))
    return saved


def fetch(url: str, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "media").mkdir(exist_ok=True)
    result = subprocess.run([sys.executable, "tools/news/fetch_tweets.py", url, "--facebook-language", "english",
                             "--instagram-language", "english", "--output", str(folder / "tweet.json"),
                             "--media-dir", str(folder / "media")],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError((result.stderr.strip() or result.stdout.strip())[-400:] or "Could not fetch the post.")
    tweet = json.loads((folder / "tweet.json").read_text(encoding="utf-8-sig"))
    tweet["workflow_type"] = "collection"
    (folder / "tweet.json").write_text(json.dumps(tweet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return (tweet.get("items") or [{}])[0]


def collect(url: str, *, collection_id: int | None = None, new_name: str | None = None) -> dict:
    match = STATUS_ID.search(url)
    if not match:
        raise ValueError("That is not an X post link.")
    connection = store.connect()
    collection = store.get(connection, collection_id) if collection_id else None
    if collection and store.has_tweet(connection, collection.id, match.group(1)):
        raise ValueError(f"That post is already in {collection.name}.")
    staging = Path(tempfile.mkdtemp(prefix="collect-", dir=store.root()))
    status = fetch(url, staging)
    videos = download_videos(status, staging)
    if collection is None:
        collection = store.create(connection, new_name or "Untitled")
    item = store.add(connection, collection.id, str(status.get("id") or match.group(1)), url, staging)
    return {"collection_id": collection.id, "collection": collection.name, "item_id": item.id,
            "count": store.count(connection, collection.id), "photos": len(status.get("downloaded_photos") or []),
            "videos": len(videos)}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--collection", type=int)
    target.add_argument("--new")
    args = parser.parse_args(argv)
    try:
        result = collect(args.url, collection_id=args.collection, new_name=args.new)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
