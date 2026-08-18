#!/usr/bin/env python3
"""Append ordered, human-readable source labels to a Bits Today description."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO
from urllib.parse import unquote, urlparse


MAX_DESCRIPTION_CHARACTERS = 2200
SOURCES_HEADING = "Sources:"
PUBLISHER_LABELS = {
    "apnews.com": "AP News",
    "arxiv.org": "arXiv",
    "aisi.gov.uk": "UK AI Security Institute",
    "cas.cn": "Chinese Academy of Sciences",
    "bbc.co.uk": "BBC",
    "bbc.com": "BBC",
    "bloomberg.com": "Bloomberg",
    "cnn.com": "CNN",
    "deepmind.google": "Google DeepMind",
    "ft.com": "Financial Times",
    "google.com": "Google",
    "huggingface.co": "Hugging Face",
    "microsoft.com": "Microsoft",
    "nature.com": "Nature",
    "news.cn": "Xinhua",
    "news.ycombinator.com": "Hacker News",
    "nytimes.com": "The New York Times",
    "openai.com": "OpenAI",
    "reuters.com": "Reuters",
    "techcrunch.com": "TechCrunch",
    "theguardian.com": "The Guardian",
    "theverge.com": "The Verge",
    "wired.com": "WIRED",
    "x.ai": "xAI",
    "ycombinator.com": "Y Combinator",
}
GENERIC_DOMAIN_LABELS = {
    "ai": "AI",
    "bbc": "BBC",
    "cnn": "CNN",
    "ibm": "IBM",
    "mit": "MIT",
    "nvidia": "NVIDIA",
}
RESERVED_SOCIAL_PATHS = {
    "",
    "about",
    "compose",
    "explore",
    "home",
    "i",
    "intent",
    "login",
    "notifications",
    "search",
    "share",
    "status",
}


def validate_source_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid source URL: {value}")
    return url


def normalized_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    for prefix in ("www.", "mobile.", "m."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def url_path_parts(url: str) -> list[str]:
    return [
        unquote(part).strip()
        for part in urlparse(url).path.split("/")
        if unquote(part).strip()
    ]


def clean_social_identity(value: str) -> str | None:
    identity = value.strip().lstrip("@")
    if not identity or identity.casefold() in RESERVED_SOCIAL_PATHS:
        return None
    if not all(character.isalnum() or character in "._-" for character in identity):
        return None
    return identity


def social_source_label(url: str) -> str | None:
    host = normalized_host(url)
    parts = url_path_parts(url)

    if host in {"x.com", "twitter.com"}:
        identity = clean_social_identity(parts[0]) if parts else None
        return f"@{identity} on X" if identity else "X"

    if host == "instagram.com":
        identity = clean_social_identity(parts[0]) if parts else None
        return f"@{identity} on Instagram" if identity else "Instagram"

    if host == "threads.net":
        identity = clean_social_identity(parts[0]) if parts else None
        return f"@{identity} on Threads" if identity else "Threads"

    if host == "tiktok.com":
        identity = clean_social_identity(parts[0]) if parts else None
        return f"@{identity} on TikTok" if identity else "TikTok"

    if host == "bsky.app":
        identity = (
            clean_social_identity(parts[1])
            if len(parts) > 1 and parts[0].casefold() == "profile"
            else None
        )
        return f"@{identity} on Bluesky" if identity else "Bluesky"

    if host in {"youtube.com", "youtu.be"}:
        identity = (
            clean_social_identity(parts[0])
            if parts and parts[0].startswith("@")
            else None
        )
        return f"@{identity} on YouTube" if identity else "YouTube"

    if host == "linkedin.com":
        identity = (
            clean_social_identity(parts[1])
            if len(parts) > 1 and parts[0].casefold() in {"company", "in", "school"}
            else None
        )
        return f"{identity} on LinkedIn" if identity else "LinkedIn"

    if host == "reddit.com":
        if len(parts) > 1 and parts[0].casefold() in {"u", "user"}:
            identity = clean_social_identity(parts[1])
            return f"u/{identity} on Reddit" if identity else "Reddit"
        if len(parts) > 1 and parts[0].casefold() == "r":
            identity = clean_social_identity(parts[1])
            return f"r/{identity} on Reddit" if identity else "Reddit"
        return "Reddit"

    if host == "github.com":
        identity = clean_social_identity(parts[0]) if parts else None
        return f"@{identity} on GitHub" if identity else "GitHub"

    return None


def publisher_source_label(url: str) -> str:
    host = normalized_host(url)
    for domain, label in PUBLISHER_LABELS.items():
        if host == domain or host.endswith(f".{domain}"):
            return label
    if host.endswith(".substack.com"):
        publication = host.removesuffix(".substack.com").split(".")[-1]
        return f"{publication.replace('-', ' ').title()} on Substack"

    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in {
        "co.in",
        "co.jp",
        "co.uk",
        "com.au",
        "com.bd",
        "com.br",
        "com.sg",
    }:
        stem = labels[-3]
    elif len(labels) >= 2:
        stem = labels[-2]
    else:
        stem = labels[0]
    if stem in GENERIC_DOMAIN_LABELS:
        return GENERIC_DOMAIN_LABELS[stem]
    return stem.replace("-", " ").replace("_", " ").title()


def source_label_from_url(value: str) -> str:
    """Convert a validated source URL into a caption-safe identity label."""
    url = validate_source_url(value)
    return social_source_label(url) or publisher_source_label(url)


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
    body, separator, _ = description.strip().rpartition(marker)
    if not separator:
        return description.strip()
    return body.strip()


def append_sources(
    description: str,
    source_urls: list[str],
    *,
    max_characters: int = MAX_DESCRIPTION_CHARACTERS,
) -> str:
    body = remove_existing_sources(description)
    if not body:
        raise ValueError("Description cannot be empty.")

    ordered_labels: list[str] = []
    seen_urls: set[str] = set()
    seen_labels: set[str] = set()
    for value in source_urls:
        url = validate_source_url(value)
        url_identity = url.casefold()
        if url_identity in seen_urls:
            continue
        seen_urls.add(url_identity)
        label = source_label_from_url(url)
        label_identity = label.casefold()
        if label_identity in seen_labels:
            continue
        seen_labels.add(label_identity)
        ordered_labels.append(label)
    if not ordered_labels:
        raise ValueError("At least one source URL is required.")

    final = f"{body}\n\n{SOURCES_HEADING}\n" + "\n".join(ordered_labels)
    if len(final) > max_characters:
        raise ValueError(
            f"Description with sources is {len(final)} characters; "
            f"platform maximum is {max_characters}. Shorten the bilingual copy."
        )
    return final


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Append caption-safe X account and research-publisher labels to a "
            "Bits Today description."
        )
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
        help=(
            "Research URL actually used. The URL is validated but only its "
            "publisher label is displayed; repeat in desired source order."
        ),
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
