#!/usr/bin/env python3
"""Translate model, product, or informative carousel copy in one model call."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.news import generate_description


TEXT_GENERATION_MODEL = "gpt-5.6-luna"
SYSTEM_INSTRUCTIONS = """Translate the supplied ordered carousel strings into
concise, natural Bangla. Preserve names, model/product names, companies,
numbers, units, and factual uncertainty. Do not add or remove facts. Return
only a JSON array with exactly one translated string for each input string, in
the same order."""


def copy_strings(payload: dict[str, Any]) -> tuple[list[str], bool]:
    if "paragraphs" in payload or "hook" in payload:
        title = str(payload.get("series_title", "")).strip()
        hook = str(payload.get("hook", "")).strip()
        paragraphs = payload.get("paragraphs")
        if (
            not title
            or not hook
            or not isinstance(paragraphs, list)
            or not paragraphs
        ):
            raise ValueError(
                "Informative copy JSON needs series_title, hook, and paragraphs."
            )
        values = [title, hook, *(str(value).strip() for value in paragraphs)]
        if any(not value for value in values):
            raise ValueError("Carousel copy strings cannot be empty.")
        return values, False

    descriptions = payload.get("short_descriptions")
    if not isinstance(descriptions, list) or not descriptions:
        raise ValueError("Copy JSON has no short_descriptions.")
    is_product = "product_name" in payload
    values = [str(value).strip() for value in descriptions]
    if is_product:
        intro = str(payload.get("intro_headline", "")).strip()
        if not intro:
            raise ValueError("Product copy JSON has no intro_headline.")
        values.insert(0, intro)
    if any(not value for value in values):
        raise ValueError("Carousel copy strings cannot be empty.")
    return values, is_product


def build_prompt(values: list[str]) -> str:
    return (
        "Translate this JSON array to Bangla while preserving its length and "
        "order:\n\n" + json.dumps(values, ensure_ascii=False)
    )


def parse_translations(text: str, expected_count: int) -> list[str]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Bangla carousel translation was not valid JSON.") from exc
    if not isinstance(payload, list) or len(payload) != expected_count:
        raise RuntimeError(
            f"Expected exactly {expected_count} translated carousel strings."
        )
    translations = [
        generate_description.normalize_source_text(str(value))
        for value in payload
    ]
    if any(
        not value or not generate_description.contains_bangla_text(value)
        for value in translations
    ):
        raise RuntimeError("Every translated carousel string must contain Bangla.")
    return translations


def translate_copy(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    values, is_product = copy_strings(payload)
    is_informative = "paragraphs" in payload and "hook" in payload
    response = client.responses.create(
        model=TEXT_GENERATION_MODEL,
        input=[
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": build_prompt(values)},
        ],
        max_output_tokens=2200 if is_informative else 900,
        reasoning={"effort": "none"},
    )
    translations = parse_translations(
        generate_description.extract_response_text(response),
        len(values),
    )
    translated = dict(payload)
    if is_informative:
        translated["series_title"] = translations[0]
        translated["hook"] = translations[1]
        translated["paragraphs"] = translations[2:]
    elif is_product:
        translated["intro_headline"] = translations[0]
        translations = translations[1:]
        translated["short_descriptions"] = translations
    else:
        translated["short_descriptions"] = translations
    translated["copy_language"] = "bangla"
    return translated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.copy_json.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Copy JSON must contain an object.")
        generate_description.require_api_key()
        translated = translate_copy(generate_description.make_client(), payload)
        translated["source_copy"] = str(args.copy_json.resolve())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(translated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(args.output.resolve())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
