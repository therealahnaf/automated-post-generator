#!/usr/bin/env python3
"""Validate or publish an approved public MP4 URL as an Instagram Reel."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from tools.news.publish_instagram import (
        bearer_headers,
        get_media_details,
        get_publishing_limit,
        graph_url,
        load_config,
        parse_graph_response,
        publish_container,
        require_publish_confirmation,
        validate_caption,
        validate_image_url,
        verify_account,
        wait_for_container,
    )
except ImportError:
    from ..news.publish_instagram import (
        bearer_headers,
        get_media_details,
        get_publishing_limit,
        graph_url,
        load_config,
        parse_graph_response,
        publish_container,
        require_publish_confirmation,
        validate_caption,
        validate_image_url,
        verify_account,
        wait_for_container,
    )


def create_reel_container(session, config, video_url: str, caption: str) -> str:
    response = session.post(
        graph_url(config, config.user_id, "media"),
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
        },
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    payload = parse_graph_response(response)
    container_id = str(payload.get("id", ""))
    if not container_id:
        raise RuntimeError("Instagram returned no Reel container ID.")
    return container_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-url", required=True)
    copy = parser.add_mutually_exclusive_group(required=True)
    copy.add_argument("--caption")
    copy.add_argument("--caption-file", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_publish_confirmation(args.publish, args.confirm)
        video_url = validate_image_url(args.video_url)
        caption = validate_caption(
            args.caption_file.read_text(encoding="utf-8")
            if args.caption_file
            else args.caption
        )
        config = load_config()
        with requests.Session() as session:
            account = verify_account(session, config)
            limit = get_publishing_limit(session, config)
            common = {
                "instagram_user_id": account["user_id"],
                "instagram_username": account["username"],
                "video_url": video_url,
                "caption_characters": len(caption),
                "quota_usage": limit.get("quota_usage"),
            }
            if not args.publish:
                print(json.dumps({"status": "validated_not_published", **common}, indent=2))
                return 0
            container_id = create_reel_container(session, config, video_url, caption)
            wait_for_container(session, config, container_id, attempts=60, interval_seconds=5)
            media_id = publish_container(session, config, container_id)
            media = get_media_details(session, config, media_id)
            print(
                json.dumps(
                    {
                        "status": "published",
                        **common,
                        "instagram_container_id": container_id,
                        "instagram_media_id": media_id,
                        "instagram_permalink": media.get("permalink"),
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
