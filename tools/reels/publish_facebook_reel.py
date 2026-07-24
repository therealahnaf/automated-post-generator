#!/usr/bin/env python3
"""Validate or publish an approved local MP4 as a Facebook Page Reel."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from tools.news.publish_facebook import (
        FacebookConfig,
        bearer_headers,
        graph_object_url,
        graph_url,
        load_config,
        parse_graph_response,
        require_publish_confirmation,
        verify_page,
    )
    from tools.reels.generate_reel import probe_video
except ImportError:
    from ..news.publish_facebook import (
        FacebookConfig,
        bearer_headers,
        graph_object_url,
        graph_url,
        load_config,
        parse_graph_response,
        require_publish_confirmation,
        verify_page,
    )
    from .generate_reel import probe_video


def validate_reel(path: Path) -> tuple[Path, dict[str, Any]]:
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError("Facebook Reel must be an existing MP4 file.")
    info = probe_video(path)
    if info["duration"] < 3.95:
        raise ValueError("Facebook Reel must be at least 4 seconds.")
    if info["duration"] > 60.05:
        raise ValueError("Facebook Reel cannot exceed 60 seconds.")
    if (info["width"], info["height"]) != (1080, 1920):
        raise ValueError("Facebook Reel must be exactly 1080x1920.")
    return path, info


def start_upload(
    session: requests.Session, config: FacebookConfig
) -> tuple[str, str]:
    response = session.post(
        graph_url(config, "video_reels"),
        data={"upload_phase": "start"},
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    payload = parse_graph_response(response)
    video_id, upload_url = str(payload.get("video_id", "")), str(payload.get("upload_url", ""))
    if not video_id or not upload_url.startswith("https://"):
        raise RuntimeError("Facebook did not return a Reel video ID and upload URL.")
    return video_id, upload_url


def upload_binary(
    session: requests.Session,
    config: FacebookConfig,
    upload_url: str,
    video: Path,
) -> None:
    with video.open("rb") as source:
        response = session.post(
            upload_url,
            data=source,
            headers={
                "Authorization": f"OAuth {config.page_token}",
                "offset": "0",
                "file_size": str(video.stat().st_size),
                "Content-Type": "application/octet-stream",
            },
            timeout=(15, 600),
        )
    parse_graph_response(response)


def finish_upload(
    session: requests.Session,
    config: FacebookConfig,
    video_id: str,
    description: str,
) -> dict[str, Any]:
    response = session.post(
        graph_url(config, "video_reels"),
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "description": description,
            "video_state": "PUBLISHED",
        },
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    return parse_graph_response(response)


def video_details(
    session: requests.Session, config: FacebookConfig, video_id: str
) -> dict[str, Any]:
    response = session.get(
        graph_object_url(config, video_id),
        params={"fields": "id,permalink_url,source,status"},
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    return parse_graph_response(response)


def wait_for_published_video(
    session: requests.Session,
    config: FacebookConfig,
    video_id: str,
    *,
    attempts: int = 60,
    interval_seconds: float = 5,
) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(attempts):
        last = video_details(session, config, video_id)
        status = last.get("status") or {}
        publishing = (status.get("publishing_phase") or {}).get("status")
        video_status = str(status.get("video_status") or "").lower()
        if publishing == "complete" or video_status in {"published", "ready"}:
            return last
        if publishing in {"failed", "error"} or video_status in {"error", "failed"}:
            raise RuntimeError(f"Facebook Reel processing failed: {status}")
        if attempt + 1 < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError(
        f"Facebook Reel did not finish processing in time. Last status: {last.get('status')}"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    copy = parser.add_mutually_exclusive_group(required=True)
    copy.add_argument("--description")
    copy.add_argument("--description-file", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_publish_confirmation(args.publish, args.confirm)
        video, info = validate_reel(args.video)
        description = (
            args.description_file.read_text(encoding="utf-8")
            if args.description_file
            else args.description
        ).strip()
        if not description:
            raise ValueError("Facebook Reel description cannot be empty.")
        config = load_config()
        with requests.Session() as session:
            page = verify_page(session, config)
            common = {
                "page_id": page["id"],
                "page_name": page["name"],
                "video": str(video),
                "video_sha256": sha256_file(video),
                "duration": info["duration"],
                "width": info["width"],
                "height": info["height"],
                "description_characters": len(description),
            }
            if not args.publish:
                print(json.dumps({"status": "validated_not_published", **common}, indent=2))
                return 0
            video_id, upload_url = start_upload(session, config)
            upload_binary(session, config, upload_url, video)
            finish = finish_upload(session, config, video_id, description)
            details = wait_for_published_video(session, config, video_id)
            print(
                json.dumps(
                    {
                        "status": "published",
                        **common,
                        "facebook_video_id": video_id,
                        "facebook_permalink": details.get("permalink_url"),
                        "facebook_video_url": details.get("source"),
                        "facebook_finish": finish,
                    },
                    indent=2,
                )
            )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
