#!/usr/bin/env python3
"""Package a news primary card with ordered story-detail cards."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.models.generate_post import (
    compose_fallback_secondary,
    compose_media_secondary,
)
from tools.news import generate_description as news_description
from tools.news import local_backgrounds
from tools.news.generate_carousel_copy import (
    MAX_SECONDARY_CARDS,
    MAX_SHORT_DESCRIPTION_CHARACTERS,
)
from tools.news.post_language import read_platform_language


@dataclass(frozen=True)
class NewsCarouselMetadata:
    primary_image: str
    secondary_images: list[str]
    source_images: list[str]
    short_descriptions: list[str]
    secondary_mode: str
    background_sources: list[str]
    background_seed: int | None
    platform: str | None
    post_language: str
    created_at: str


MAX_BANGLA_DESCRIPTION_CHARACTERS = 220


def validate_descriptions(values: list[Any], language: str) -> list[str]:
    limit = (
        MAX_BANGLA_DESCRIPTION_CHARACTERS
        if language == "bangla"
        else MAX_SHORT_DESCRIPTION_CHARACTERS
    )
    descriptions: list[str] = []
    for value in values:
        description = news_description.normalize_source_text(str(value))
        if not description:
            raise ValueError("Short descriptions cannot be empty.")
        if len(description) > limit:
            raise ValueError(
                f"{language.title()} short description exceeds {limit} characters."
            )
        descriptions.append(description)
    return descriptions


def resolve_source_images(copy_json: Path, values: list[Any]) -> list[Path]:
    resolved: list[Path] = []
    for value in values:
        stored = Path(str(value))
        candidates = [stored]
        if not stored.is_absolute():
            candidates.insert(0, copy_json.parent / stored)
        match = next((candidate for candidate in candidates if candidate.is_file()), None)
        if match is None:
            raise FileNotFoundError(f"News carousel source image not found: {stored}")
        resolved.append(match.resolve())
    return resolved


def read_copy_file(
    path: Path,
) -> tuple[str, str, list[str], list[Path]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("workflow_type") != "news":
        raise ValueError("Copy JSON is not for the news workflow.")
    mode = str(payload.get("mode", ""))
    if mode not in {"media", "fallback", "none"}:
        raise ValueError(f"Unsupported news carousel mode: {mode or 'missing'}")
    raw_descriptions = payload.get("short_descriptions")
    if not isinstance(raw_descriptions, list):
        raise ValueError("Copy JSON has no short_descriptions array.")
    copy_language = str(payload.get("copy_language", "english"))
    if copy_language not in {"english", "bangla"}:
        raise ValueError(f"Unsupported copy language: {copy_language}")
    descriptions = (
        validate_descriptions(raw_descriptions, copy_language)
        if raw_descriptions
        else []
    )
    raw_sources = payload.get("secondary_source_images", [])
    if not isinstance(raw_sources, list):
        raise ValueError("Copy JSON secondary_source_images must be an array.")
    source_images = resolve_source_images(path, raw_sources)

    if mode == "media" and (
        not source_images or len(source_images) != len(descriptions)
    ):
        raise ValueError(
            "Media news carousels need one short description per source image."
        )
    if mode == "fallback" and (source_images or len(descriptions) != 1):
        raise ValueError("A no-media news carousel needs exactly one summary card.")
    if mode == "none" and (source_images or descriptions):
        raise ValueError("A single-card news package cannot contain detail copy.")
    if len(descriptions) > MAX_SECONDARY_CARDS:
        raise ValueError(f"News carousel cannot exceed {MAX_SECONDARY_CARDS} details.")
    return mode, copy_language, descriptions, source_images


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render and package ordered news carousel detail cards."
    )
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--primary-image", type=Path, required=True)
    parser.add_argument("--copy-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--platform", choices=("facebook", "instagram"))
    parser.add_argument(
        "--background-dir",
        type=Path,
        default=local_backgrounds.DEFAULT_BACKGROUND_DIR,
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
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
        if not args.primary_image.is_file():
            raise FileNotFoundError(f"Primary news image not found: {args.primary_image}")
        mode, copy_language, descriptions, source_images = read_copy_file(
            args.copy_json
        )
        language = (
            read_platform_language(args.tweet_json, args.platform)
            if args.platform
            else copy_language
        )
        if descriptions and copy_language != language:
            raise ValueError(
                f"Copy language {copy_language!r} does not match {language!r}."
            )

        secondary_count = len(descriptions)
        background_seed: int | None = None
        selected_backgrounds: list[Path] = []
        if secondary_count:
            source_text = news_description.read_tweet_text(args.tweet_json)
            background_seed = (
                args.seed
                if args.seed is not None
                else local_backgrounds.stable_seed("news-details", source_text)
            )
            selected_backgrounds = local_backgrounds.select_backgrounds(
                local_backgrounds.list_backgrounds(args.background_dir),
                secondary_count,
                background_seed,
            )

        args.output_dir.mkdir(parents=True, exist_ok=True)
        primary_path = args.output_dir / "01-headline.png"
        if args.primary_image.resolve() != primary_path.resolve():
            shutil.copy2(args.primary_image, primary_path)

        secondary_paths: list[Path] = []
        for offset, description in enumerate(descriptions):
            index = offset + 2
            output_path = args.output_dir / (
                f"{index:02d}-detail-{offset + 1}.png"
            )
            background_bytes = selected_backgrounds[offset].read_bytes()
            if mode == "media":
                image = compose_media_secondary(
                    source_images[offset],
                    description,
                    args.date,
                    background_bytes=background_bytes,
                    show_media_border=False,
                )
            else:
                image = compose_fallback_secondary(
                    background_bytes,
                    description,
                    args.date,
                )
            image.save(output_path, format="PNG", optimize=True)
            secondary_paths.append(output_path)

        metadata = NewsCarouselMetadata(
            primary_image=str(primary_path.resolve()),
            secondary_images=[str(path.resolve()) for path in secondary_paths],
            source_images=[str(path.resolve()) for path in source_images],
            short_descriptions=descriptions,
            secondary_mode=mode,
            background_sources=[str(path.resolve()) for path in selected_backgrounds],
            background_seed=background_seed,
            platform=args.platform,
            post_language=language,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        metadata_path = args.output_dir / "carousel.json"
        metadata_path.write_text(
            json.dumps(asdict(metadata), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(primary_path.resolve())
        for path in secondary_paths:
            print(path.resolve())
        print(metadata_path.resolve())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
