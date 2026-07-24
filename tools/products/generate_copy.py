#!/usr/bin/env python3
"""Generate the product intro headline and ordered carousel feature copy."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.models import generate_copy as model_copy
from tools.news import generate_description as news_description


TEXT_GENERATION_MODEL = "gpt-5.6-luna"
MAX_PRODUCT_NAME_CHARACTERS = 80
MAX_COMPANY_NAME_CHARACTERS = 100
MAX_INTRO_HEADLINE_CHARACTERS = 100
MAX_SHORT_DESCRIPTION_CHARACTERS = model_copy.MAX_SHORT_DESCRIPTION_CHARACTERS
MAX_CARDS = model_copy.MAX_CARDS

SYSTEM_INSTRUCTIONS = """You are the product-launch copy editor for Bits Today.
Return a JSON object with exactly two keys:
1. "intro_headline": one concrete 5-12 word sentence fragment explaining what
   the product does or the primary outcome it enables.
2. "short_descriptions": an ordered JSON array of concise carousel segments.

The intro headline must help a reader understand the product without reading
the caption. Do not repeat the product or company name in it. Each carousel
segment must communicate one distinct feature, use case, availability detail,
price, limitation, or positioning statement. Collectively cover the finalized
description without repetition. Preserve names, numbers, attribution, and
uncertainty. Do not add outside facts, hype, hashtags, headings, URLs, or
markdown. Return only the JSON object."""


def normalize_product_name(value: str) -> str:
    name = news_description.normalize_source_text(value).strip(" \"'")
    if not name:
        raise ValueError("Product name cannot be empty.")
    if len(name) > MAX_PRODUCT_NAME_CHARACTERS:
        raise ValueError(
            f"Product name exceeds {MAX_PRODUCT_NAME_CHARACTERS} characters."
        )
    return name


def normalize_company_name(value: str) -> str:
    name = news_description.normalize_source_text(value).strip(" \"'")
    if not name:
        raise ValueError("Company name cannot be empty.")
    if len(name) > MAX_COMPANY_NAME_CHARACTERS:
        raise ValueError(
            f"Company name exceeds {MAX_COMPANY_NAME_CHARACTERS} characters."
        )
    return name


def build_headline(product_name: str) -> str:
    return f"You Should Know About {normalize_product_name(product_name)}"


def normalize_intro_headline(value: str) -> str:
    headline = news_description.normalize_source_text(value).strip(" \"'")
    if not headline:
        raise ValueError("Product intro headline cannot be empty.")
    if len(headline) > MAX_INTRO_HEADLINE_CHARACTERS:
        raise ValueError(
            f"Product intro headline exceeds {MAX_INTRO_HEADLINE_CHARACTERS} characters."
        )
    return headline


def build_prompt(
    source_text: str,
    product_name: str,
    company_name: str,
    card_range: tuple[int, int],
) -> str:
    minimum_count, maximum_count = card_range
    count_instruction = (
        f"exactly {minimum_count}"
        if minimum_count == maximum_count
        else f"{minimum_count} or {maximum_count}"
    )
    return f"""Product name: {normalize_product_name(product_name)}
Company name: {normalize_company_name(company_name)}
Required short descriptions: {count_instruction}
Maximum intro headline characters: {MAX_INTRO_HEADLINE_CHARACTERS}
Maximum characters per short description: {MAX_SHORT_DESCRIPTION_CHARACTERS}

Write a functional intro headline and split the finalized description into
{count_instruction} concise segments in narrative order. Use {minimum_count}
segments when the description is concise and {maximum_count} only when it has
enough distinct detail.

DESCRIPTION START
{source_text.strip()}
DESCRIPTION END"""


def parse_product_copy(
    text: str,
    expected_range: tuple[int, int],
) -> tuple[str, list[str]]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Product copy response was not valid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "intro_headline",
        "short_descriptions",
    }:
        raise RuntimeError(
            "Product copy response must contain only intro_headline and "
            "short_descriptions."
        )
    intro_headline = normalize_intro_headline(str(payload["intro_headline"]))
    descriptions = model_copy.parse_short_descriptions(
        json.dumps(payload["short_descriptions"], ensure_ascii=False),
        expected_range,
    )
    return intro_headline, descriptions


def generate_product_copy(
    client: Any,
    source_text: str,
    product_name: str,
    company_name: str,
    card_range: tuple[int, int],
) -> tuple[str, list[str]]:
    response = client.responses.create(
        model=TEXT_GENERATION_MODEL,
        input=[
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {
                "role": "user",
                "content": build_prompt(
                    source_text,
                    product_name,
                    company_name,
                    card_range,
                ),
            },
        ],
        max_output_tokens=850,
        reasoning={"effort": "none"},
    )
    return parse_product_copy(
        news_description.extract_response_text(response),
        card_range,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate product-release intro and carousel copy."
    )
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--description-file", type=Path, required=True)
    parser.add_argument("--product-name", required=True)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def configure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    configure_utf8(sys.stdout)
    configure_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        product_name = normalize_product_name(args.product_name)
        company_name = normalize_company_name(args.company_name)
        source_text = model_copy.read_english_description(args.description_file)
        card_range = model_copy.required_card_range(
            model_copy.downloaded_photo_count(args.tweet_json)
        )
        news_description.require_api_key()
        intro_headline, short_descriptions = generate_product_copy(
            news_description.make_client(),
            source_text,
            product_name,
            company_name,
            card_range,
        )
        payload = {
            "product_name": product_name,
            "company_name": company_name,
            "headline": build_headline(product_name),
            "intro_headline": intro_headline,
            "short_descriptions": short_descriptions,
            "source_tweet_json": str(args.tweet_json.resolve()),
            "source_description": str(args.description_file.resolve()),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(args.output.resolve())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
