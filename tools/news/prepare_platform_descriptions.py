#!/usr/bin/env python3
"""Create Facebook and Instagram caption files with independent language order."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from .generate_description import (
        DESCRIPTION_SEPARATOR,
        combine_descriptions,
    )
    from .post_language import read_platform_languages
except ImportError:
    from generate_description import (
        DESCRIPTION_SEPARATOR,
        combine_descriptions,
    )
    from post_language import read_platform_languages


SOURCES_MARKER = "\n\nSources:\n"


def split_finalized_description(description: str) -> tuple[str, str, str]:
    description = description.strip()
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
    english, bangla, sources = split_finalized_description(description)
    ordered = combine_descriptions(
        english,
        bangla,
        primary_language=primary_language,
    )
    if sources:
        ordered = f"{ordered}{SOURCES_MARKER}{sources}"
    return ordered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--description-file", type=Path, required=True)
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        description = args.description_file.read_text(encoding="utf-8")
        languages = read_platform_languages(args.tweet_json)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for platform in ("facebook", "instagram"):
            output = args.output_dir / f"{platform}-description.txt"
            output.write_text(
                order_description(description, languages[platform]) + "\n",
                encoding="utf-8",
            )
            print(output.resolve())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
