#!/usr/bin/env python3
"""Validate or publish an approved local MP4 or public URL as an Instagram Reel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov"}

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


def validate_video_file(video_path: Path) -> Path:
    if not video_path.is_file():
        raise FileNotFoundError(f"Instagram Reel video not found: {video_path}")
    if video_path.suffix.lower() not in SUPPORTED_VIDEO_SUFFIXES:
        raise ValueError("Instagram Reel video must be an MP4 or MOV file.")
    if video_path.stat().st_size < 1:
        raise ValueError("Instagram Reel video cannot be empty.")
    return video_path.resolve()


def create_url_reel_container(
    session, config, video_url: str, caption: str
) -> str:
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


@dataclass(frozen=True)
class MediaHostConfig:
    directory: Path
    base_url: str


def load_media_host_config() -> MediaHostConfig:
    raw_directory = os.getenv("INSTAGRAM_REEL_MEDIA_DIR", "").strip()
    raw_base_url = os.getenv("INSTAGRAM_REEL_MEDIA_BASE_URL", "").strip()
    if not raw_directory:
        raise RuntimeError("INSTAGRAM_REEL_MEDIA_DIR is not set in .env.")
    if not raw_base_url:
        raise RuntimeError("INSTAGRAM_REEL_MEDIA_BASE_URL is not set in .env.")
    parsed = urlparse(raw_base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(
            "INSTAGRAM_REEL_MEDIA_BASE_URL must be a public HTTPS URL."
        )
    if parsed.query or parsed.fragment:
        raise RuntimeError(
            "INSTAGRAM_REEL_MEDIA_BASE_URL cannot contain a query or fragment."
        )
    return MediaHostConfig(
        directory=Path(raw_directory).expanduser().resolve(),
        base_url=raw_base_url.rstrip("/"),
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_local_reel(video_path: Path, media_host: MediaHostConfig) -> tuple[Path, str]:
    media_host.directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    media_host.directory.chmod(0o755)
    destination = media_host.directory / (
        f"reel-{file_sha256(video_path)}{video_path.suffix.lower()}"
    )
    if not destination.is_file() or destination.stat().st_size != video_path.stat().st_size:
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        shutil.copyfile(video_path, temporary)
        temporary.chmod(0o644)
        temporary.replace(destination)
    destination.chmod(0o644)
    return destination, f"{media_host.base_url}/{destination.name}"


def verify_hosted_reel(
    session,
    video_url: str,
    expected_size: int,
) -> None:
    response = session.head(video_url, allow_redirects=True, timeout=(10, 30))
    if not response.ok:
        raise RuntimeError(
            f"Hosted Instagram Reel returned HTTP {response.status_code}."
        )
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type not in {"video/mp4", "video/quicktime"}:
        raise RuntimeError(
            f"Hosted Instagram Reel returned unexpected Content-Type {content_type!r}."
        )
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) != expected_size:
        raise RuntimeError(
            "Hosted Instagram Reel size does not match the approved local file."
        )


def wait_for_reel_container(session, config, container_id: str) -> dict:
    try:
        return wait_for_container(
            session,
            config,
            container_id,
            attempts=60,
            interval_seconds=5,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Instagram Reel container {container_id} failed processing: {exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--video",
        type=Path,
        help=(
            "Approved local MP4/MOV. Staged at the configured stable HTTPS "
            "media host for Instagram to fetch."
        ),
    )
    source.add_argument(
        "--video-url",
        help="Public HTTPS MP4/MOV URL retained for compatibility.",
    )
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
        video_path = validate_video_file(args.video) if args.video else None
        video_url = validate_image_url(args.video_url) if args.video_url else None
        media_host = load_media_host_config() if video_path is not None else None
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
                "upload_mode": (
                    "stable_https" if video_path is not None else "public_url"
                ),
                "video": str(video_path) if video_path is not None else None,
                "video_url": video_url,
                "caption_characters": len(caption),
                "quota_usage": limit.get("quota_usage"),
            }
            if not args.publish:
                print(json.dumps({"status": "validated_not_published", **common}, indent=2))
                return 0
            if video_path is not None:
                _, video_url = stage_local_reel(video_path, media_host)
                verify_hosted_reel(
                    session,
                    video_url,
                    video_path.stat().st_size,
                )
                common["video_url"] = video_url
            container_id = create_url_reel_container(
                session,
                config,
                video_url,
                caption,
            )
            wait_for_reel_container(session, config, container_id)
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
