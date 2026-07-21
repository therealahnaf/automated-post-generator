#!/usr/bin/env python3
"""Validate or publish an ordered image post to the configured Instagram account."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


GRAPH_ROOT = "https://graph.instagram.com"
DEFAULT_GRAPH_VERSION = "v25.0"
GRAPH_VERSION_PATTERN = re.compile(r"^v\d+\.\d+$")
MAX_CAPTION_CHARACTERS = 2200
MAX_CAROUSEL_ITEMS = 10


@dataclass(frozen=True)
class InstagramConfig:
    user_id: str
    access_token: str
    graph_version: str


def load_config() -> InstagramConfig:
    user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    graph_version = os.getenv(
        "INSTAGRAM_GRAPH_API_VERSION", DEFAULT_GRAPH_VERSION
    ).strip()
    if not user_id:
        raise RuntimeError("INSTAGRAM_USER_ID is not set in .env.")
    if not access_token:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is not set in .env.")
    if not GRAPH_VERSION_PATTERN.fullmatch(graph_version):
        raise RuntimeError(
            "INSTAGRAM_GRAPH_API_VERSION must look like 'v25.0'."
        )
    return InstagramConfig(user_id, access_token, graph_version)


def graph_url(config: InstagramConfig, object_id: str, edge: str = "") -> str:
    suffix = f"/{edge.lstrip('/')}" if edge else ""
    return f"{GRAPH_ROOT}/{config.graph_version}/{object_id}{suffix}"


def bearer_headers(config: InstagramConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {config.access_token}"}


def parse_graph_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Instagram API returned non-JSON HTTP {response.status_code}."
        ) from exc
    if not response.ok or payload.get("error"):
        error = payload.get("error") or {}
        code = error.get("code", response.status_code)
        message = error.get("message", "Unknown Instagram API error")
        raise RuntimeError(f"Instagram API error {code}: {message}")
    return payload


def verify_account(
    session: requests.Session,
    config: InstagramConfig,
) -> dict[str, Any]:
    response = session.get(
        graph_url(config, config.user_id),
        params={"fields": "user_id,username,account_type"},
        headers=bearer_headers(config),
        timeout=(10, 30),
    )
    account = parse_graph_response(response)
    if str(account.get("user_id")) != config.user_id:
        raise RuntimeError(
            "Instagram-token mismatch: API returned user "
            f"{account.get('user_id', 'missing')}, expected {config.user_id}."
        )
    if account.get("account_type") not in {"BUSINESS", "MEDIA_CREATOR"}:
        raise RuntimeError(
            "Instagram content publishing requires a Business or Creator account."
        )
    return account


def get_publishing_limit(
    session: requests.Session,
    config: InstagramConfig,
) -> dict[str, Any]:
    response = session.get(
        graph_url(config, config.user_id, "content_publishing_limit"),
        params={"fields": "config,quota_usage"},
        headers=bearer_headers(config),
        timeout=(10, 30),
    )
    payload = parse_graph_response(response)
    entries = payload.get("data") or []
    return entries[0] if entries else {}


def validate_image_url(image_url: str) -> str:
    parsed = urlparse(image_url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Instagram requires a publicly reachable HTTPS image URL.")
    return image_url.strip()


def validate_image_urls(primary: str, secondary: list[str]) -> list[str]:
    image_urls = [validate_image_url(primary)]
    image_urls.extend(validate_image_url(url) for url in secondary)
    if len(image_urls) > MAX_CAROUSEL_ITEMS:
        raise ValueError(
            f"Instagram supports at most {MAX_CAROUSEL_ITEMS} carousel images."
        )
    return image_urls


def validate_caption(caption: str) -> str:
    caption = caption.strip()
    if not caption:
        raise ValueError("The Instagram caption cannot be empty.")
    if len(caption) > MAX_CAPTION_CHARACTERS:
        raise ValueError(
            f"Instagram caption is {len(caption)} characters; "
            f"maximum is {MAX_CAPTION_CHARACTERS}."
        )
    return caption


def create_image_container(
    session: requests.Session,
    config: InstagramConfig,
    image_url: str,
    caption: str,
) -> str:
    response = session.post(
        graph_url(config, config.user_id, "media"),
        data={"image_url": image_url, "caption": caption},
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    payload = parse_graph_response(response)
    container_id = str(payload.get("id", ""))
    if not container_id:
        raise RuntimeError("Instagram returned no media container ID.")
    return container_id


def create_carousel_item_container(
    session: requests.Session,
    config: InstagramConfig,
    image_url: str,
) -> str:
    response = session.post(
        graph_url(config, config.user_id, "media"),
        data={"image_url": image_url, "is_carousel_item": "true"},
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    payload = parse_graph_response(response)
    container_id = str(payload.get("id", ""))
    if not container_id:
        raise RuntimeError("Instagram returned no carousel item container ID.")
    return container_id


def create_carousel_container(
    session: requests.Session,
    config: InstagramConfig,
    child_ids: list[str],
    caption: str,
) -> str:
    if not 2 <= len(child_ids) <= MAX_CAROUSEL_ITEMS:
        raise ValueError(
            f"An Instagram carousel requires 2 to {MAX_CAROUSEL_ITEMS} items."
        )
    response = session.post(
        graph_url(config, config.user_id, "media"),
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
        },
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    payload = parse_graph_response(response)
    container_id = str(payload.get("id", ""))
    if not container_id:
        raise RuntimeError("Instagram returned no carousel container ID.")
    return container_id


def wait_for_container(
    session: requests.Session,
    config: InstagramConfig,
    container_id: str,
    *,
    attempts: int = 30,
    interval_seconds: float = 2.0,
) -> dict[str, Any]:
    terminal_errors = {"ERROR", "EXPIRED"}
    for attempt in range(attempts):
        response = session.get(
            graph_url(config, container_id),
            params={"fields": "status_code,status"},
            headers=bearer_headers(config),
            timeout=(10, 30),
        )
        status = parse_graph_response(response)
        status_code = status.get("status_code")
        if status_code == "FINISHED":
            return status
        if status_code in terminal_errors:
            raise RuntimeError(
                f"Instagram container failed with status {status_code}: "
                f"{status.get('status', 'no details')}"
            )
        if attempt + 1 < attempts:
            time.sleep(interval_seconds)
    raise RuntimeError("Instagram media container did not finish processing in time.")


def publish_container(
    session: requests.Session,
    config: InstagramConfig,
    container_id: str,
) -> str:
    response = session.post(
        graph_url(config, config.user_id, "media_publish"),
        data={"creation_id": container_id},
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    payload = parse_graph_response(response)
    media_id = str(payload.get("id", ""))
    if not media_id:
        raise RuntimeError("Instagram returned no published media ID.")
    return media_id


def get_media_details(
    session: requests.Session,
    config: InstagramConfig,
    media_id: str,
) -> dict[str, Any]:
    response = session.get(
        graph_url(config, media_id),
        params={"fields": "id,permalink,media_type,timestamp"},
        headers=bearer_headers(config),
        timeout=(10, 30),
    )
    return parse_graph_response(response)


def require_publish_confirmation(publish: bool, confirmation: str | None) -> None:
    if publish and confirmation != "yes":
        raise RuntimeError(
            "Publishing requires both --publish and the exact argument --confirm yes."
        )


def read_caption(args: argparse.Namespace) -> str:
    if args.caption_file:
        return validate_caption(args.caption_file.read_text(encoding="utf-8"))
    return validate_caption(args.caption or "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an approved Bits Today Instagram image post. Publishing "
            "is disabled unless --publish and --confirm yes are both supplied."
        )
    )
    parser.add_argument(
        "--image-url",
        required=True,
        help="Public HTTPS URL for the generated main image.",
    )
    parser.add_argument(
        "--secondary-image-url",
        action="append",
        default=[],
        help=(
            "Additional public HTTPS image URL. Repeat in the desired carousel "
            "order (maximum 10 images total)."
        ),
    )
    caption_group = parser.add_mutually_exclusive_group(required=True)
    caption_group.add_argument("--caption", help="Instagram caption.")
    caption_group.add_argument(
        "--caption-file",
        type=Path,
        help="UTF-8 file containing the Instagram caption.",
    )
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--confirm",
        help="Must be the exact word 'yes' when --publish is supplied.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_publish_confirmation(args.publish, args.confirm)
        image_urls = validate_image_urls(
            args.image_url, args.secondary_image_url
        )
        caption = read_caption(args)
        config = load_config()
        with requests.Session() as session:
            account = verify_account(session, config)
            limit = get_publishing_limit(session, config)
            common = {
                "instagram_user_id": account["user_id"],
                "instagram_username": account["username"],
                "account_type": account["account_type"],
                "image_urls": image_urls,
                "image_count": len(image_urls),
                "caption_characters": len(caption),
                "quota_total": (limit.get("config") or {}).get("quota_total"),
                "quota_usage": limit.get("quota_usage"),
            }
            if not args.publish:
                print(json.dumps({"status": "validated_not_published", **common}, indent=2))
                return 0

            if len(image_urls) == 1:
                container_id = create_image_container(
                    session, config, image_urls[0], caption
                )
                carousel_item_ids: list[str] = []
            else:
                carousel_item_ids = []
                for image_url in image_urls:
                    child_id = create_carousel_item_container(
                        session, config, image_url
                    )
                    wait_for_container(session, config, child_id)
                    carousel_item_ids.append(child_id)
                container_id = create_carousel_container(
                    session, config, carousel_item_ids, caption
                )
            wait_for_container(session, config, container_id)
            media_id = publish_container(session, config, container_id)
            media = get_media_details(session, config, media_id)
            print(
                json.dumps(
                    {
                        "status": "published",
                        **common,
                        "instagram_container_id": container_id,
                        "instagram_carousel_item_ids": carousel_item_ids,
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
