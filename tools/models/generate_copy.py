#!/usr/bin/env python3
"""Generate fixed model-announcement headlines and short carousel copy."""

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

from tools.news import generate_description as news_description
from tools.news import finalize_description as news_finalizer


TEXT_GENERATION_MODEL = "gpt-5.6-luna"
MAX_MODEL_NAME_CHARACTERS = 80
MAX_COMPANY_NAME_CHARACTERS = 100
MAX_SHORT_DESCRIPTION_CHARACTERS = 160
MAX_CARDS = 9
NO_MEDIA_MIN_CARDS = 2
NO_MEDIA_MAX_CARDS = 3

SYSTEM_INSTRUCTIONS = """You are the model-launch copy editor for Bits Today.
Turn the supplied finalized English description into concise, ordered
carousel-card segments. Each segment must communicate one distinct, important
capability, price change, efficiency claim, availability detail, or positioning
statement while collectively covering the description without repetition.
Preserve names, numbers, attribution, and uncertainty. Do not add outside facts,
hype, hashtags, headings, URLs, or markdown. Return only a JSON array of strings.
"""


def normalize_model_name(value: str) -> str:
    name = news_description.normalize_source_text(value).strip(" \"'")
    if not name:
        raise ValueError("Model name cannot be empty.")
    if len(name) > MAX_MODEL_NAME_CHARACTERS:
        raise ValueError(
            f"Model name exceeds {MAX_MODEL_NAME_CHARACTERS} characters."
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


def build_headline(model_name: str) -> str:
    return f"Meet {normalize_model_name(model_name)}"


def downloaded_photo_count(tweet_json: Path) -> int:
    document = json.loads(tweet_json.read_text(encoding="utf-8"))
    items = document.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ValueError(f"No tweet item found in {tweet_json}.")
    photos = items[0].get("downloaded_photos")
    return min(len(photos) if isinstance(photos, list) else 0, MAX_CARDS)


def read_english_description(path: Path) -> str:
    description = news_finalizer.remove_existing_sources(
        path.read_text(encoding="utf-8")
    )
    sections = [
        section.strip()
        for section in re.split(r"\n\s*---\s*\n", description)
        if section.strip()
    ]
    english_sections = [
        section
        for section in sections
        if not news_description.contains_bangla_text(section)
    ]
    if not english_sections:
        raise ValueError(f"No English description section found in {path}.")
    return english_sections[0]


def required_card_range(photo_count: int) -> tuple[int, int]:
    if photo_count < 0 or photo_count > MAX_CARDS:
        raise ValueError(f"Photo count must be between 0 and {MAX_CARDS}.")
    if photo_count:
        return photo_count, photo_count
    return NO_MEDIA_MIN_CARDS, NO_MEDIA_MAX_CARDS


def build_prompt(
    source_text: str,
    model_name: str,
    card_range: tuple[int, int],
) -> str:
    minimum_count, maximum_count = card_range
    if not 1 <= minimum_count <= maximum_count <= MAX_CARDS:
        raise ValueError(f"Card count must be between 1 and {MAX_CARDS}.")
    count_instruction = (
        f"exactly {minimum_count}"
        if minimum_count == maximum_count
        else f"{minimum_count} or {maximum_count}"
    )
    return f"""Model name: {normalize_model_name(model_name)}
Required descriptions: {count_instruction}
Maximum characters per description: {MAX_SHORT_DESCRIPTION_CHARACTERS}

Split the finalized description into {count_instruction} concise segments in
the same narrative order. Use {minimum_count} when the description is concise
and {maximum_count} when it contains enough distinct detail. Do not repeat facts
merely to reach the larger count.

DESCRIPTION START
{source_text.strip()}
DESCRIPTION END"""


def parse_short_descriptions(
    text: str,
    expected_range: tuple[int, int],
) -> list[str]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Model copy response was not a valid JSON array.") from exc
    minimum_count, maximum_count = expected_range
    if (
        not isinstance(payload, list)
        or not minimum_count <= len(payload) <= maximum_count
    ):
        raise RuntimeError(
            f"Expected between {minimum_count} and {maximum_count} short "
            "descriptions from the model."
        )
    descriptions = []
    for value in payload:
        description = news_description.normalize_source_text(str(value))
        if not description:
            raise RuntimeError("Model returned an empty short description.")
        if len(description) > MAX_SHORT_DESCRIPTION_CHARACTERS:
            raise RuntimeError(
                "Model returned a short description longer than "
                f"{MAX_SHORT_DESCRIPTION_CHARACTERS} characters."
            )
        descriptions.append(description)
    return descriptions


def generate_short_descriptions(
    client: Any,
    source_text: str,
    model_name: str,
    card_range: tuple[int, int],
) -> list[str]:
    response = client.responses.create(
        model=TEXT_GENERATION_MODEL,
        input=[
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {
                "role": "user",
                "content": build_prompt(source_text, model_name, card_range),
            },
        ],
        max_output_tokens=700,
        reasoning={"effort": "none"},
    )
    return parse_short_descriptions(
        news_description.extract_response_text(response),
        card_range,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate model-announcement headline and carousel copy."
    )
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument(
        "--description-file",
        type=Path,
        required=True,
        help="Final bilingual description used to create ordered English segments.",
    )
    parser.add_argument("--model-name", required=True)
    parser.add_argument(
        "--company-name",
        required=True,
        help="Company or organization releasing the model.",
    )
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
        model_name = normalize_model_name(args.model_name)
        company_name = normalize_company_name(args.company_name)
        source_text = read_english_description(args.description_file)
        card_range = required_card_range(downloaded_photo_count(args.tweet_json))
        news_description.require_api_key()
        short_descriptions = generate_short_descriptions(
            news_description.make_client(),
            source_text,
            model_name,
            card_range,
        )
        payload = {
            "model_name": model_name,
            "company_name": company_name,
            "headline": build_headline(model_name),
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
