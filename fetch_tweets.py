#!/usr/bin/env python3
"""Fetch public X/Twitter posts without X's official API or paid services.

The default backend is FxTwitter, the public deployment of the MIT-licensed
FxEmbed project. The request and JSON parsing use only Python's standard
library; no account, API key, or Apify subscription is required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


DEFAULT_API_BASE = "https://api.fxtwitter.com"
DEFAULT_FULL_TEXT_API_BASE = "https://api.vxtwitter.com"
OPEN_SOURCE_PROJECT = "https://github.com/FxEmbed/FxEmbed"
IMPLEMENTATION_REFERENCE = "https://github.com/ythx-101/x-tweet-fetcher"
STATUS_PATH = re.compile(r"^/([^/]+)/status/(\d+)(?:/.*)?$")


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


def fetch_tweets(
    urls: list[str],
    *,
    api_base: str = DEFAULT_API_BASE,
    full_text_api_base: str = DEFAULT_FULL_TEXT_API_BASE,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch one or more public status URLs using the free open-source backend."""
    return {
        "provider": "fxtwitter",
        "provider_api": api_base.rstrip("/"),
        "full_text_provider_api": full_text_api_base.rstrip("/"),
        "open_source_project": OPEN_SOURCE_PROJECT,
        "implementation_reference": IMPLEMENTATION_REFERENCE,
        "official_x_api_used": False,
        "items": [
            fetch_tweet(
                url,
                api_base=api_base,
                full_text_api_base=full_text_api_base,
                timeout=timeout,
            )
            for url in urls
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch public X/Twitter posts through the free, open-source "
            "FxTwitter backend and emit JSON."
        )
    )
    parser.add_argument("urls", nargs="+", help="One or more X/Twitter status URLs.")
    parser.add_argument("--output", type=Path, help="Write JSON to this file.")
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
        urls = [normalize_tweet_url(url) for url in args.urls]
        result = fetch_tweets(
            urls,
            api_base=args.api_base,
            full_text_api_base=args.full_text_api_base,
            timeout=args.timeout,
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
