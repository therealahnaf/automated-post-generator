#!/usr/bin/env python3
"""Validate or publish an ordered image post to the configured Facebook Page."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


GRAPH_ROOT = "https://graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v25.0"
GRAPH_VERSION_PATTERN = re.compile(r"^v\d+\.\d+$")
MAX_POST_IMAGES = 10


@dataclass(frozen=True)
class FacebookConfig:
    page_id: str
    page_token: str
    graph_version: str


def load_config() -> FacebookConfig:
    page_id = os.getenv("FACEBOOK_PAGE_ID", "").strip()
    page_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
    graph_version = os.getenv(
        "FACEBOOK_GRAPH_API_VERSION", DEFAULT_GRAPH_VERSION
    ).strip()
    if not page_id:
        raise RuntimeError("FACEBOOK_PAGE_ID is not set in .env.")
    if not page_token:
        raise RuntimeError("FACEBOOK_PAGE_ACCESS_TOKEN is not set in .env.")
    if not GRAPH_VERSION_PATTERN.fullmatch(graph_version):
        raise RuntimeError(
            "FACEBOOK_GRAPH_API_VERSION must look like 'v25.0'."
        )
    return FacebookConfig(page_id, page_token, graph_version)


def graph_url(config: FacebookConfig, edge: str = "") -> str:
    suffix = f"/{edge.lstrip('/')}" if edge else ""
    return f"{GRAPH_ROOT}/{config.graph_version}/{config.page_id}{suffix}"


def graph_object_url(config: FacebookConfig, object_id: str) -> str:
    return f"{GRAPH_ROOT}/{config.graph_version}/{object_id}"


def parse_graph_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Meta Graph API returned non-JSON HTTP {response.status_code}."
        ) from exc
    if not response.ok or payload.get("error"):
        error = payload.get("error") or {}
        code = error.get("code", response.status_code)
        message = error.get("message", "Unknown Meta Graph API error")
        raise RuntimeError(f"Meta Graph API error {code}: {message}")
    return payload


def bearer_headers(config: FacebookConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {config.page_token}"}


def verify_page(
    session: requests.Session,
    config: FacebookConfig,
) -> dict[str, Any]:
    response = session.get(
        graph_url(config),
        params={"fields": "id,name"},
        headers=bearer_headers(config),
        timeout=(10, 30),
    )
    page = parse_graph_response(response)
    if str(page.get("id")) != config.page_id:
        raise RuntimeError(
            f"Page-token mismatch: Meta returned Page {page.get('id', 'missing')}, "
            f"expected {config.page_id}."
        )
    return page


def publish_photo(
    session: requests.Session,
    config: FacebookConfig,
    image_path: Path,
    message: str,
) -> dict[str, Any]:
    mime_type = image_mime_type(image_path)
    with image_path.open("rb") as image_file:
        response = session.post(
            graph_url(config, "photos"),
            data={"message": message, "published": "true"},
            files={"source": (image_path.name, image_file, mime_type)},
            headers=bearer_headers(config),
            timeout=(10, 180),
        )
    return parse_graph_response(response)


def image_mime_type(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("Facebook image must be a JPEG, PNG, or WebP file.")
    return mime_type


def validate_image_paths(primary: Path, secondary: list[Path]) -> list[Path]:
    images = [primary, *secondary]
    if len(images) > MAX_POST_IMAGES:
        raise ValueError(
            f"A cross-platform post supports at most {MAX_POST_IMAGES} images."
        )
    for image in images:
        if not image.is_file():
            raise FileNotFoundError(f"Image not found: {image}")
        image_mime_type(image)
        try:
            with Image.open(image) as decoded:
                decoded.verify()
            with Image.open(image) as decoded:
                decoded.load()
        except (OSError, SyntaxError, ValueError) as exc:
            raise ValueError(
                f"Facebook image is not a complete decodable image: {image}"
            ) from exc
    return images


def upload_unpublished_photo(
    session: requests.Session,
    config: FacebookConfig,
    image_path: Path,
) -> str:
    mime_type = image_mime_type(image_path)
    with image_path.open("rb") as image_file:
        response = session.post(
            graph_url(config, "photos"),
            data={"published": "false"},
            files={"source": (image_path.name, image_file, mime_type)},
            headers=bearer_headers(config),
            timeout=(10, 180),
        )
    payload = parse_graph_response(response)
    photo_id = str(payload.get("id", ""))
    if not photo_id:
        raise RuntimeError("Facebook returned no ID for an unpublished photo.")
    return photo_id


def publish_multi_photo_post(
    session: requests.Session,
    config: FacebookConfig,
    photo_ids: list[str],
    message: str,
) -> str:
    if len(photo_ids) < 2:
        raise ValueError("A Facebook multi-photo post requires at least two photos.")
    data: dict[str, str] = {"message": message}
    for index, photo_id in enumerate(photo_ids):
        data[f"attached_media[{index}]"] = json.dumps(
            {"media_fbid": photo_id}, separators=(",", ":")
        )
    response = session.post(
        graph_url(config, "feed"),
        data=data,
        headers=bearer_headers(config),
        timeout=(10, 60),
    )
    payload = parse_graph_response(response)
    post_id = str(payload.get("id", ""))
    if not post_id:
        raise RuntimeError("Facebook returned no multi-photo post ID.")
    return post_id


def get_photo_details(
    session: requests.Session,
    config: FacebookConfig,
    photo_id: str,
) -> dict[str, Any]:
    response = session.get(
        graph_object_url(config, photo_id),
        params={"fields": "id,images,link"},
        headers=bearer_headers(config),
        timeout=(10, 30),
    )
    payload = parse_graph_response(response)
    images = payload.get("images") or []
    if images:
        largest = max(
            images,
            key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)),
        )
        payload["largest_image_url"] = largest.get("source")
    return payload


def get_post_details(
    session: requests.Session,
    config: FacebookConfig,
    post_id: str,
) -> dict[str, Any]:
    response = session.get(
        graph_object_url(config, post_id),
        params={"fields": "id,permalink_url"},
        headers=bearer_headers(config),
        timeout=(10, 30),
    )
    return parse_graph_response(response)


def require_publish_confirmation(publish: bool, confirmation: str | None) -> None:
    if publish and confirmation != "yes":
        raise RuntimeError(
            "Publishing requires both --publish and the exact argument --confirm yes."
        )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_message(args: argparse.Namespace) -> str:
    if args.message_file:
        message = args.message_file.read_text(encoding="utf-8")
    else:
        message = args.message or ""
    message = message.strip()
    if not message:
        raise ValueError("The Facebook description cannot be empty.")
    return message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an approved Bits Today Facebook image post. Publishing is "
            "disabled unless --publish and --confirm yes are both supplied."
        )
    )
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument(
        "--secondary-image",
        action="append",
        default=[],
        type=Path,
        help=(
            "Additional local image to publish after --image. Repeat in the "
            "desired carousel order (maximum 10 images total)."
        ),
    )
    message_group = parser.add_mutually_exclusive_group(required=True)
    message_group.add_argument("--message", help="Facebook post description.")
    message_group.add_argument(
        "--message-file",
        type=Path,
        help="UTF-8 file containing the Facebook post description.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Perform the external Facebook publishing action.",
    )
    parser.add_argument(
        "--confirm",
        help="Must be the exact word 'yes' when --publish is supplied.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_publish_confirmation(args.publish, args.confirm)
        images = validate_image_paths(args.image, args.secondary_image)
        message = read_message(args)
        config = load_config()
        with requests.Session() as session:
            page = verify_page(session, config)
            common = {
                "page_id": page["id"],
                "page_name": page["name"],
                "image": str(images[0].resolve()),
                "image_sha256": file_sha256(images[0]),
                "images": [str(image.resolve()) for image in images],
                "image_sha256s": [file_sha256(image) for image in images],
                "image_count": len(images),
                "message_characters": len(message),
            }
            if not args.publish:
                print(json.dumps({"status": "validated_not_published", **common}, indent=2))
                return 0

            if len(images) == 1:
                result = publish_photo(session, config, images[0], message)
                photo_ids = [str(result.get("id", ""))]
                post_id = str(result.get("post_id", ""))
                photo_details = (
                    [get_photo_details(session, config, photo_ids[0])]
                    if photo_ids[0]
                    else []
                )
                permalink = photo_details[0].get("link") if photo_details else None
            else:
                photo_ids = [
                    upload_unpublished_photo(session, config, image)
                    for image in images
                ]
                post_id = publish_multi_photo_post(
                    session, config, photo_ids, message
                )
                photo_details = [
                    get_photo_details(session, config, photo_id)
                    for photo_id in photo_ids
                ]
                permalink = get_post_details(session, config, post_id).get(
                    "permalink_url"
                )

            image_urls = [
                details.get("largest_image_url") for details in photo_details
            ]
            if len(image_urls) != len(images) or not all(image_urls):
                raise RuntimeError(
                    "Facebook published the media but did not return every hosted image URL."
                )
            print(
                json.dumps(
                    {
                        "status": "published",
                        **common,
                        "facebook_photo_id": photo_ids[0],
                        "facebook_photo_ids": photo_ids,
                        "facebook_post_id": post_id,
                        "facebook_permalink": permalink,
                        "facebook_image_url": image_urls[0],
                        "facebook_image_urls": image_urls,
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
