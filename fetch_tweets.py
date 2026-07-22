#!/usr/bin/env python3
"""Fetch public X/Twitter posts without X's official API or paid services.

The default backend is FxTwitter, the public deployment of the MIT-licensed
FxEmbed project. The request and JSON parsing use only Python's standard
backend; no account, API key, or Apify subscription is required. Pillow is used
to validate any attached photos before they enter the publishing workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image
from dotenv import load_dotenv

from post_language import (
    AUTO_HIGHLIGHT_STYLE,
    AUTO_LANGUAGE,
    HEADLINE_HIGHLIGHT_STYLES,
    POST_LANGUAGES,
    choose_headline_highlight,
    choose_post_language,
)


load_dotenv(Path(__file__).with_name(".env"))


DEFAULT_API_BASE = "https://api.fxtwitter.com"
DEFAULT_FULL_TEXT_API_BASE = "https://api.vxtwitter.com"
OPEN_SOURCE_PROJECT = "https://github.com/FxEmbed/FxEmbed"
IMPLEMENTATION_REFERENCE = "https://github.com/ythx-101/x-tweet-fetcher"
STATUS_PATH = re.compile(r"^/([^/]+)/status/(\d+)(?:/.*)?$")
MAX_TWEET_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_FORMAT_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MAX_SECONDARY_IMAGES = 9
DEFAULT_VIDEO_FRAME_SECONDS = 0.5


def status_parts(value: str) -> tuple[str, str]:
    """Return ``(handle, status_id)`` for a public X/Twitter status URL."""
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    }:
        raise ValueError(f"Not an X/Twitter URL: {value}")
    match = STATUS_PATH.match(parsed.path)
    if not match:
        raise ValueError(f"Not an X/Twitter status URL: {value}")
    return match.groups()


def normalize_tweet_url(value: str) -> str:
    """Return a canonical x.com status URL or raise a useful error."""
    handle, status_id = status_parts(value)
    return f"https://x.com/{handle}/status/{status_id}"


def fxtwitter_endpoint(url: str, api_base: str = DEFAULT_API_BASE) -> str:
    """Build the FxTwitter endpoint for a single public status URL."""
    handle, status_id = status_parts(url)
    return f"{api_base.rstrip('/')}/{handle}/status/{status_id}"


def fxtwitter_thread_endpoint(url: str, api_base: str = DEFAULT_API_BASE) -> str:
    """Build the FxTwitter v2 endpoint for a same-author thread."""
    _, status_id = status_parts(url)
    return f"{api_base.rstrip('/')}/2/thread/{status_id}"


def fetch_json(endpoint: str, *, timeout: float) -> Any:
    request = Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": "ctrl-ai-open-source-tweet-fetcher/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_binary(endpoint: str, *, timeout: float) -> tuple[bytes, str]:
    request = Request(
        endpoint,
        headers={
            "Accept": "image/jpeg,image/png,image/webp",
            "User-Agent": "ctrl-ai-open-source-tweet-fetcher/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_TWEET_IMAGE_BYTES + 1)
        content_type = response.headers.get_content_type()
    if len(payload) > MAX_TWEET_IMAGE_BYTES:
        raise RuntimeError(
            f"Tweet image exceeds {MAX_TWEET_IMAGE_BYTES // (1024 * 1024)} MB."
        )
    return payload, content_type


def extract_photo_media(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    media = tweet.get("media")
    if not isinstance(media, dict):
        return []
    photos = media.get("photos")
    if not isinstance(photos, list):
        photos = [
            item
            for item in media.get("all") or []
            if isinstance(item, dict) and item.get("type") == "photo"
        ]
    normalized = []
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        url = str(photo.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        normalized.append(photo)
    return normalized


def download_tweet_photos(
    tweet: dict[str, Any],
    media_dir: Path,
    *,
    timeout: float,
) -> list[dict[str, Any]]:
    tweet_id = str(tweet.get("id", "")).strip()
    if not tweet_id:
        raise ValueError("Cannot download media for a tweet without an ID.")
    photos = extract_photo_media(tweet)
    media_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, Any]] = []
    for position, photo in enumerate(photos, start=1):
        source_url = str(photo["url"])
        payload, content_type = fetch_binary(source_url, timeout=timeout)
        try:
            with Image.open(io.BytesIO(payload)) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
                image.verify()
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Tweet photo {position} did not contain a valid supported image."
            ) from exc
        suffix = IMAGE_FORMAT_SUFFIXES.get(image_format)
        if not suffix:
            raise RuntimeError(
                f"Tweet photo {position} uses unsupported format {image_format or 'unknown'}."
            )
        destination = media_dir / f"{tweet_id}-photo-{position}{suffix}"
        destination.write_bytes(payload)
        downloaded.append(
            {
                "position": position,
                "source_url": source_url,
                "local_path": str(destination.resolve()),
                "content_type": content_type,
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    tweet["downloaded_photos"] = downloaded
    return downloaded


def extract_video_media(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    """Return valid video media objects in API source order."""
    media = tweet.get("media")
    if not isinstance(media, dict):
        return []
    videos = media.get("videos")
    if not isinstance(videos, list):
        videos = [
            item
            for item in media.get("all") or []
            if isinstance(item, dict) and item.get("type") in {"video", "gif"}
        ]
    return [video for video in videos if isinstance(video, dict)]


def best_video_url(video: dict[str, Any]) -> str:
    """Choose the highest-bitrate HTTPS MP4 variant from a video object."""
    candidates: list[tuple[int, str]] = []
    for variant in video.get("formats") or video.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        url = str(variant.get("url", "")).strip()
        parsed = urlparse(url)
        container = str(
            variant.get("container") or variant.get("content_type") or ""
        ).lower()
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        if "mp4" not in container and ".mp4" not in parsed.path.lower():
            continue
        try:
            bitrate = int(variant.get("bitrate") or variant.get("bit_rate") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        candidates.append((bitrate, url))
    direct_url = str(video.get("url", "")).strip()
    direct_parsed = urlparse(direct_url)
    if (
        direct_parsed.scheme == "https"
        and direct_parsed.netloc
        and ".mp4" in direct_parsed.path.lower()
    ):
        candidates.append((0, direct_url))
    if not candidates:
        raise RuntimeError("Tweet video has no downloadable HTTPS MP4 variant.")
    return max(candidates, key=lambda item: item[0])[1]


def extract_video_frame(
    video: dict[str, Any],
    destination: Path,
    *,
    frame_seconds: float = DEFAULT_VIDEO_FRAME_SECONDS,
) -> dict[str, Any]:
    """Extract a JPEG opening frame from the best video variant with FFmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to extract tweet video opening frames.")
    source_url = best_video_url(video)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{frame_seconds:g}",
        "-i",
        source_url,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-y",
        str(destination),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"Could not extract tweet video frame: {detail}") from exc
    try:
        with Image.open(destination) as image:
            width, height = image.size
            image.verify()
    except (OSError, ValueError) as exc:
        raise RuntimeError("FFmpeg did not produce a valid tweet video frame.") from exc
    payload = destination.read_bytes()
    return {
        "kind": "video_frame",
        "source_url": source_url,
        "local_path": str(destination.resolve()),
        "content_type": "image/jpeg",
        "width": width,
        "height": height,
        "frame_seconds": frame_seconds,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def quoted_tweet(tweet: dict[str, Any]) -> dict[str, Any] | None:
    """Return the nested quoted status across supported FxTwitter schemas."""
    for key in ("quote", "quoted_tweet", "quotedTweet"):
        value = tweet.get(key)
        if isinstance(value, dict):
            return value
    return None


def ordered_content_statuses(tweet: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return thread statuses and their nested quotes in deterministic order."""
    thread = tweet.get("thread")
    statuses = thread if isinstance(thread, list) and thread else [tweet]
    ordered: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()

    def append_status(origin: str, status: dict[str, Any], depth: int = 0) -> None:
        status_id = str(status.get("id", "")).strip()
        identity = status_id or f"object-{id(status)}"
        if identity in seen:
            return
        seen.add(identity)
        ordered.append((origin, status))
        quote = quoted_tweet(status)
        if quote is not None and depth < 3:
            append_status("quote", quote, depth + 1)

    for index, status in enumerate(statuses):
        if isinstance(status, dict):
            append_status("main" if index == 0 else "thread", status)
    return ordered


def download_tweet_media(
    tweet: dict[str, Any],
    media_dir: Path,
    *,
    timeout: float,
    frame_seconds: float = DEFAULT_VIDEO_FRAME_SECONDS,
    limit: int = MAX_SECONDARY_IMAGES,
) -> list[dict[str, Any]]:
    """Download thread/quote photos and video frames in deterministic order."""
    if limit < 0:
        raise ValueError("Media limit cannot be negative.")
    media_dir.mkdir(parents=True, exist_ok=True)
    ordered_statuses = ordered_content_statuses(tweet)
    total_media = sum(
        len(extract_photo_media(status)) + len(extract_video_media(status))
        for _, status in ordered_statuses
    )
    downloaded: list[dict[str, Any]] = []
    downloaded_photos: list[dict[str, Any]] = []
    for origin, status in ordered_statuses:
        if len(downloaded) >= limit:
            break
        status_id = str(status.get("id", "")).strip()
        if not status_id:
            continue
        for photo_index, photo in enumerate(extract_photo_media(status), start=1):
            if len(downloaded) >= limit:
                break
            source_url = str(photo["url"])
            payload, content_type = fetch_binary(source_url, timeout=timeout)
            try:
                with Image.open(io.BytesIO(payload)) as image:
                    image_format = str(image.format or "").upper()
                    width, height = image.size
                    image.verify()
            except (OSError, ValueError) as exc:
                raise RuntimeError("Tweet media did not contain a valid image.") from exc
            suffix = IMAGE_FORMAT_SUFFIXES.get(image_format)
            if not suffix:
                raise RuntimeError(
                    f"Tweet photo uses unsupported format {image_format or 'unknown'}."
                )
            destination = media_dir / f"{status_id}-photo-{photo_index}{suffix}"
            destination.write_bytes(payload)
            item = {
                "kind": "photo",
                "origin": origin,
                "source_status_id": status_id,
                "position": len(downloaded) + 1,
                "source_url": source_url,
                "local_path": str(destination.resolve()),
                "content_type": content_type,
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            downloaded.append(item)
            downloaded_photos.append(item)
        for video_index, video in enumerate(extract_video_media(status), start=1):
            if len(downloaded) >= limit:
                break
            destination = media_dir / f"{status_id}-video-{video_index}-frame.jpg"
            item = extract_video_frame(
                video,
                destination,
                frame_seconds=frame_seconds,
            )
            item.update(
                {
                    "origin": origin,
                    "source_status_id": status_id,
                    "position": len(downloaded) + 1,
                }
            )
            downloaded.append(item)
    tweet["downloaded_media"] = downloaded
    tweet["downloaded_photos"] = downloaded_photos
    tweet["media_truncated"] = total_media > len(downloaded)
    return downloaded


def looks_possibly_truncated(text: str) -> bool:
    """Return true when a public embed API may have returned a long-post preview."""
    text = text.rstrip()
    if not text:
        return False
    if text.endswith(("…", "...")):
        return True
    return len(text) >= 260 and text[-1] not in ".!?)]}\"'"


def fetch_full_text_candidate(
    url: str,
    *,
    api_base: str,
    timeout: float,
) -> str:
    """Fetch a VxTwitter-compatible response and return validated tweet text."""
    canonical_url = normalize_tweet_url(url)
    _, expected_id = status_parts(canonical_url)
    endpoint = fxtwitter_endpoint(canonical_url, api_base)
    payload = fetch_json(endpoint, timeout=timeout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Full-text fallback returned invalid JSON for {canonical_url}.")

    returned_id = str(payload.get("tweetID", ""))
    if returned_id != expected_id:
        raise RuntimeError(
            f"Full-text fallback returned tweet ID {returned_id or 'missing'}; "
            f"expected {expected_id}."
        )

    text = str(payload.get("text", "")).strip()
    if not text:
        raise RuntimeError(f"Full-text fallback returned empty text for {canonical_url}.")
    return text


def recover_full_text(
    tweet: dict[str, Any],
    canonical_url: str,
    *,
    api_base: str,
    timeout: float,
) -> dict[str, Any]:
    """Replace preview text with longer validated text when a fallback has it."""
    current_text = str(tweet.get("text", "")).strip()
    tweet["text_source"] = "fxtwitter"
    if not looks_possibly_truncated(current_text):
        tweet["full_text_recovery"] = {"attempted": False}
        return tweet

    try:
        candidate_text = fetch_full_text_candidate(
            canonical_url,
            api_base=api_base,
            timeout=timeout,
        )
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        tweet["full_text_recovery"] = {
            "attempted": True,
            "succeeded": False,
            "provider_api": api_base.rstrip("/"),
            "error": str(exc),
        }
        return tweet

    recovery = {
        "attempted": True,
        "succeeded": False,
        "provider_api": api_base.rstrip("/"),
    }
    if len(candidate_text) > len(current_text) and candidate_text.startswith(current_text):
        tweet["text_before_recovery"] = current_text
        tweet["text"] = candidate_text
        raw_text = tweet.get("raw_text")
        if isinstance(raw_text, dict):
            raw_text["text"] = candidate_text
            raw_text["display_text_range"] = [0, len(candidate_text)]
        tweet["text_source"] = "vxtwitter"
        recovery["succeeded"] = True
        recovery["added_characters"] = len(candidate_text) - len(current_text)
    tweet["full_text_recovery"] = recovery
    return tweet


def fetch_tweet(
    url: str,
    *,
    api_base: str = DEFAULT_API_BASE,
    full_text_api_base: str = DEFAULT_FULL_TEXT_API_BASE,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch and validate one tweet from the open-source FxTwitter backend."""
    canonical_url = normalize_tweet_url(url)
    _, expected_id = status_parts(canonical_url)
    endpoint = fxtwitter_endpoint(canonical_url, api_base)
    try:
        payload = fetch_json(endpoint, timeout=timeout)
    except HTTPError as exc:
        raise RuntimeError(
            f"FxTwitter returned HTTP {exc.code} for {canonical_url}."
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"FxTwitter request failed for {canonical_url}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"FxTwitter returned invalid JSON for {canonical_url}.")
    if payload.get("code") != 200 or not isinstance(payload.get("tweet"), dict):
        raise RuntimeError(
            f"FxTwitter returned no tweet for {canonical_url}: "
            f"{payload.get('message', 'unknown error')}"
        )

    tweet = payload["tweet"]
    returned_id = str(tweet.get("id", ""))
    if returned_id != expected_id:
        raise RuntimeError(
            f"FxTwitter returned tweet ID {returned_id or 'missing'}; "
            f"expected {expected_id}."
        )
    if not str(tweet.get("text", "")).strip():
        raise RuntimeError(f"FxTwitter returned an empty tweet for {canonical_url}.")
    return recover_full_text(
        tweet,
        canonical_url,
        api_base=full_text_api_base,
        timeout=timeout,
    )


def fetch_thread(
    url: str,
    *,
    api_base: str = DEFAULT_API_BASE,
    full_text_api_base: str = DEFAULT_FULL_TEXT_API_BASE,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch one status plus its complete same-author thread via FxTwitter v2."""
    del full_text_api_base  # FxTwitter v2 returns the complete text for thread items.
    canonical_url = normalize_tweet_url(url)
    _, expected_id = status_parts(canonical_url)
    endpoint = fxtwitter_thread_endpoint(canonical_url, api_base)
    try:
        payload = fetch_json(endpoint, timeout=timeout)
    except HTTPError as exc:
        raise RuntimeError(
            f"FxTwitter returned HTTP {exc.code} for {canonical_url}."
        ) from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"FxTwitter request failed for {canonical_url}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"FxTwitter returned invalid JSON for {canonical_url}.")
    status = payload.get("status")
    if payload.get("code") != 200 or not isinstance(status, dict):
        raise RuntimeError(
            f"FxTwitter returned no status for {canonical_url}: "
            f"{payload.get('message', 'unknown error')}"
        )
    returned_id = str(status.get("id", "")).strip()
    if returned_id != expected_id:
        raise RuntimeError(
            f"FxTwitter returned tweet ID {returned_id or 'missing'}; "
            f"expected {expected_id}."
        )
    if not str(status.get("text", "")).strip():
        raise RuntimeError(f"FxTwitter returned an empty tweet for {canonical_url}.")

    normalized_thread: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    raw_thread = payload.get("thread")
    root_thread_item = dict(status)
    root_thread_item.pop("thread", None)
    candidates = [root_thread_item, *(raw_thread if isinstance(raw_thread, list) else [])]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id", "")).strip()
        if not candidate_id or candidate_id in seen_ids:
            continue
        if not str(candidate.get("text", "")).strip():
            continue
        seen_ids.add(candidate_id)
        candidate["text_source"] = "fxtwitter_v2"
        normalized_thread.append(candidate)
    status["thread"] = normalized_thread
    status["thread_count"] = len(normalized_thread)
    status["text_source"] = "fxtwitter_v2"
    status["full_text_recovery"] = {"attempted": False, "reason": "fxtwitter_v2"}
    return status


def fetch_tweets(
    urls: list[str],
    *,
    api_base: str = DEFAULT_API_BASE,
    full_text_api_base: str = DEFAULT_FULL_TEXT_API_BASE,
    timeout: float = 30.0,
    media_dir: Path | None = None,
    video_frame_seconds: float = DEFAULT_VIDEO_FRAME_SECONDS,
    post_language: str = AUTO_LANGUAGE,
    headline_highlight: str = AUTO_HIGHLIGHT_STYLE,
) -> dict[str, Any]:
    """Fetch one or more public status URLs using the free open-source backend."""
    selected_language = choose_post_language(post_language)
    selected_highlight = choose_headline_highlight(headline_highlight)
    items = [
        fetch_thread(
            url,
            api_base=api_base,
            full_text_api_base=full_text_api_base,
            timeout=timeout,
        )
        for url in urls
    ]
    if media_dir is not None:
        for item in items:
            download_tweet_media(
                item,
                media_dir,
                timeout=timeout,
                frame_seconds=video_frame_seconds,
            )
    return {
        "post_language": selected_language,
        "headline_highlight": selected_highlight,
        "provider": "fxtwitter",
        "provider_api": f"{api_base.rstrip('/')}/2",
        "full_text_provider_api": full_text_api_base.rstrip("/"),
        "open_source_project": OPEN_SOURCE_PROJECT,
        "implementation_reference": IMPLEMENTATION_REFERENCE,
        "official_x_api_used": False,
        "items": items,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch public X/Twitter posts through the free, open-source "
            "FxTwitter backend and emit JSON."
        )
    )
    parser.add_argument("urls", nargs="+", help="One or more X/Twitter status URLs.")
    parser.add_argument(
        "--language",
        choices=(AUTO_LANGUAGE, *POST_LANGUAGES),
        default=AUTO_LANGUAGE,
        help=(
            "Primary post language. The default, auto, randomly chooses English "
            "or Bangla once and saves it in the output JSON."
        ),
    )
    parser.add_argument(
        "--highlight-style",
        choices=(AUTO_HIGHLIGHT_STYLE, *HEADLINE_HIGHLIGHT_STYLES),
        default=AUTO_HIGHLIGHT_STYLE,
        help=(
            "Headline block treatment. The default randomly selects one cyan "
            "line, one red line, or the current red-plus-cyan two-line style."
        ),
    )
    parser.add_argument("--output", type=Path, help="Write JSON to this file.")
    parser.add_argument(
        "--media-dir",
        type=Path,
        help=(
            "Download thread and quoted-post photos plus video opening frames "
            "into this directory in source order."
        ),
    )
    parser.add_argument(
        "--video-frame-seconds",
        type=float,
        default=DEFAULT_VIDEO_FRAME_SECONDS,
        help="Timestamp used for video opening frames (default: 0.5 seconds).",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("FXTWITTER_API_BASE", DEFAULT_API_BASE),
        help=(
            "FxTwitter-compatible API base URL. Set this to a self-hosted "
            f"FxEmbed deployment (default: {DEFAULT_API_BASE})."
        ),
    )
    parser.add_argument(
        "--full-text-api-base",
        default=os.getenv("VXTWITTER_API_BASE", DEFAULT_FULL_TEXT_API_BASE),
        help=(
            "VxTwitter-compatible API base used to recover longer text when "
            "FxTwitter returns a possible long-post preview "
            f"(default: {DEFAULT_FULL_TEXT_API_BASE})."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Request timeout in seconds (default: 30).",
    )
    return parser


def configure_utf8(stream: TextIO) -> None:
    """Avoid Windows console failures on emoji and invisible Unicode text."""
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_utf8(sys.stdout)
    configure_utf8(sys.stderr)
    try:
        if args.timeout <= 0:
            raise ValueError("--timeout must be greater than zero.")
        if args.video_frame_seconds < 0:
            raise ValueError("--video-frame-seconds cannot be negative.")
        urls = [normalize_tweet_url(url) for url in args.urls]
        result = fetch_tweets(
            urls,
            api_base=args.api_base,
            full_text_api_base=args.full_text_api_base,
            timeout=args.timeout,
            media_dir=args.media_dir,
            video_frame_seconds=args.video_frame_seconds,
            post_language=args.language,
            headline_highlight=args.highlight_style,
        )
        document = {
            "requested_urls": urls,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **result,
        }

        rendered = json.dumps(document, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
            print(args.output.resolve())
        else:
            print(rendered)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
