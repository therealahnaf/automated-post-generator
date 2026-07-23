#!/usr/bin/env python3
"""Append an ordered, deduplicated source list to a Bits Today news description."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse


MAX_DESCRIPTION_CHARACTERS = 2200
SOURCES_HEADING = "Sources:"


def validate_source_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid source URL: {value}")
    return url


def read_tweet_source_urls(tweet_json: Path) -> list[str]:
    document = json.loads(tweet_json.read_text(encoding="utf-8"))
    urls = document.get("requested_urls")
    if not isinstance(urls, list) or not urls:
        items = document.get("items")
        urls = [
            item.get("url")
            for item in items or []
            if isinstance(item, dict) and item.get("url")
        ]
    return [validate_source_url(str(url)) for url in urls or []]


def remove_existing_sources(description: str) -> str:
    marker = f"\n\n{SOURCES_HEADING}\n"
    body, separator, source_text = description.strip().rpartition(marker)
    if not separator:
        return description.strip()
    source_lines = [line.strip() for line in source_text.splitlines() if line.strip()]
    if source_lines and all(
        urlparse(line).scheme in {"http", "https"} and urlparse(line).netloc
        for line in source_lines
    ):
        return body.strip()
    return description.strip()


def append_sources(
    description: str,
    source_urls: list[str],
    *,
    max_characters: int = MAX_DESCRIPTION_CHARACTERS,
) -> str:
    body = remove_existing_sources(description)
    if not body:
        raise ValueError("Description cannot be empty.")

    ordered_urls: list[str] = []
    seen: set[str] = set()
    for value in source_urls:
        url = validate_source_url(value)
        identity = url.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        ordered_urls.append(url)
    if not ordered_urls:
        raise ValueError("At least one source URL is required.")

    final = f"{body}\n\n{SOURCES_HEADING}\n" + "\n".join(ordered_urls)
    if len(final) > max_characters:
        raise ValueError(
            f"Description with sources is {len(final)} characters; "
            f"platform maximum is {max_characters}. Shorten the bilingual copy."
        )
    return final


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append X and research URLs to a Bits Today description."
    )
    parser.add_argument(
        "--description-file",
        type=Path,
        required=True,
        help="UTF-8 bilingual description to finalize.",
    )
    parser.add_argument(
        "--tweet-json",
        type=Path,
        help="Add requested X URLs from fetch_tweets.py output first.",
    )
    parser.add_argument(
        "--source-url",
        action="append",
        default=[],
        help="Research URL actually used; repeat in desired source order.",
    )
    parser.add_argument("--output", type=Path, help="Write the finalized description.")
    return parser


def configure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    configure_utf8(sys.stdout)
    configure_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        description = args.description_file.read_text(encoding="utf-8")
        source_urls = (
            read_tweet_source_urls(args.tweet_json) if args.tweet_json else []
        )
        source_urls.extend(args.source_url)
        finalized = append_sources(description, source_urls)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(finalized + "\n", encoding="utf-8")
            print(args.output.resolve())
        else:
            print(finalized)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
