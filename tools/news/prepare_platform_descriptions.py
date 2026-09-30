#!/usr/bin/env python3
"""Create English-only Facebook and Instagram captions and website manifests."""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
from pathlib import Path

try:
    from .generate_description import DESCRIPTION_SEPARATOR
    from .finalize_description import MAX_DESCRIPTION_CHARACTERS
except ImportError:
    from generate_description import DESCRIPTION_SEPARATOR
    from finalize_description import MAX_DESCRIPTION_CHARACTERS


SOURCES_MARKER = "\n\nSources:\n"
SOURCE_LINK_NOTICE = "Visit the link in bio for links to all the sources"


def add_source_link_notice(description: str) -> str:
    """Insert the fixed caption notice once, before sources and hashtags."""
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", description.replace(SOURCE_LINK_NOTICE, ""))
        if paragraph.strip()
    ]
    insertion = next((
        index for index, paragraph in enumerate(paragraphs)
        if paragraph.startswith("Sources:\n")
        or re.fullmatch(r"(?:#[^\s]+\s*)+", paragraph)
    ), len(paragraphs))
    paragraphs.insert(insertion, SOURCE_LINK_NOTICE)
    caption = "\n\n".join(paragraphs)
    if len(caption) > MAX_DESCRIPTION_CHARACTERS:
        raise ValueError(
            f"Caption with source-link notice is {len(caption)} characters; "
            f"platform maximum is {MAX_DESCRIPTION_CHARACTERS}. Shorten the copy."
        )
    return caption


def split_finalized_description(description: str) -> tuple[str, str, str]:
    description = description.replace(SOURCE_LINK_NOTICE, "").strip()
    body, marker, sources = description.rpartition(SOURCES_MARKER)
    if not marker:
        body = description
        sources = ""
    sections = [
        section.strip()
        for section in re.split(
            rf"\n\s*{re.escape(DESCRIPTION_SEPARATOR)}\s*\n",
            body,
        )
        if section.strip()
    ]
    if len(sections) == 1:
        if re.search(r"[\u0980-\u09ff]", sections[0]):
            raise ValueError("Expected an English description, not Bangla-only copy.")
        return sections[0], "", sources.strip()
    if len(sections) != 2:
        raise ValueError("Expected exactly two bilingual description sections.")
    bangla_counts = [
        sum("\u0980" <= character <= "\u09ff" for character in section)
        for section in sections
    ]
    bangla_index = max(range(len(sections)), key=bangla_counts.__getitem__)
    if bangla_counts[bangla_index] == 0:
        raise ValueError("Could not identify one English and one Bangla section.")
    english_index = 1 - bangla_index
    return sections[english_index], sections[bangla_index], sources.strip()


def order_description(description: str, primary_language: str) -> str:
    # Also extract English from older bilingual captions when revising a job.
    english, _, sources = split_finalized_description(description)
    ordered = english
    if sources:
        ordered = f"{ordered}{SOURCES_MARKER}{sources}"
    return add_source_link_notice(ordered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--description-file", type=Path, required=True)
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--english-title", help="Approved English headline for the website manifest.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        description = args.description_file.read_text(encoding="utf-8")
        caption = order_description(description, "english")
        manifest = None
        if args.english_title:
            try:
                from .content_archive import prepare_manifest
            except ImportError:
                from content_archive import prepare_manifest
            english, _, _ = split_finalized_description(description)
            manifest = prepare_manifest(title=args.english_title, english=english,
                                        tweet_json=args.tweet_json, description_file=args.description_file)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for platform in ("facebook", "instagram"):
            output = args.output_dir / f"{platform}-description.txt"
            output.write_text(
                caption + "\n",
                encoding="utf-8",
            )
            print(output.resolve())
            if manifest:
                output.with_suffix('.website.json').write_text(
                    json.dumps({**manifest, 'caption_sha256': hashlib.sha256(output.read_bytes()).hexdigest()},
                               ensure_ascii=False, indent=2), encoding='utf-8')
            elif output.with_suffix('.website.json').exists():
                # Never let a revised caption reuse a stale English manifest.
                output.with_suffix('.website.json').unlink()
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
