#!/usr/bin/env python3
"""Render Bits Today product-release primary and feature cards."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TextIO

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.models import generate_copy as model_copy
from tools.models import generate_post as model_post
from tools.news import generate_description as news_description
from tools.news import generate_post as news_post
from tools.products.generate_copy import (
    MAX_INTRO_HEADLINE_CHARACTERS,
    build_headline,
    normalize_company_name,
    normalize_intro_headline,
    normalize_product_name,
)


CANVAS_SIZE = news_post.CANVAS_SIZE


@dataclass(frozen=True)
class ProductPostMetadata:
    product_name: str
    company_name: str
    headline: str
    intro_headline: str
    short_descriptions: list[str]
    primary_image: str
    secondary_images: list[str]
    source_images: list[str]
    background_source: str
    primary_style: str
    created_at: str


def read_copy_file(path: Path) -> tuple[str, str, str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    product_name = normalize_product_name(str(payload.get("product_name", "")))
    company_name = normalize_company_name(str(payload.get("company_name", "")))
    expected_headline = build_headline(product_name)
    if payload.get("headline") != expected_headline:
        raise ValueError(f"Copy headline must be exactly: {expected_headline}")
    intro_headline = normalize_intro_headline(
        str(payload.get("intro_headline", ""))
    )
    raw_descriptions = payload.get("short_descriptions")
    if not isinstance(raw_descriptions, list) or not raw_descriptions:
        raise ValueError("Copy file has no short descriptions.")
    return (
        product_name,
        company_name,
        intro_headline,
        model_post.validate_short_descriptions(raw_descriptions),
    )


def build_background_prompt(
    source_text: str,
    product_name: str,
    intro_headline: str,
) -> str:
    return f"""Use case: stylized-concept
Asset type: text-free 4:5 portrait background for a technology product launch
Primary request: Create a premium editorial technology visual inspired by the launch of {product_name}.
Product function: {intro_headline}
Announcement context: {source_text}
Scene/backdrop: a credible environment, material, device context, or abstract system that evokes the product's actual purpose without inventing a user interface
Style/medium: polished cinematic editorial photography with refined depth and restrained abstract light structures
Composition/framing: 4:5 portrait; keep the central 55 percent calm and uncluttered for a centered title stack; place detail around the edges and in depth
Lighting/mood: useful, modern, consequential, confident
Color palette: charcoal and black with restrained coral #FF5757 and mint #C2FFE1 accents
Constraints: no text, no letters, no numbers, no logos, no trademarks, no watermark, no border, no invented UI
""".strip()


def fit_kicker(draw: ImageDraw.ImageDraw):
    text = "You Should Know About"
    for size in range(66, 43, -2):
        font = news_post.load_roboto_font(size=size, bold=True, italic=True)
        if news_post.text_width(draw, text, font) <= 920:
            return font, text
    raise ValueError("Product kicker could not fit the primary card.")


def wrap_intro(
    draw: ImageDraw.ImageDraw,
    intro_headline: str,
) -> tuple[Any, list[str], int]:
    intro_headline = normalize_intro_headline(intro_headline)
    for size in range(50, 31, -2):
        font = news_post.load_roboto_font(size=size, bold=True)
        lines = news_post.wrap_headline(draw, intro_headline, font, 880)
        if len(lines) <= 2:
            return font, lines, size + 16
    raise ValueError("Product intro headline is too long for the primary card.")


def compose_primary(
    background_bytes: bytes,
    product_name: str,
    company_name: str,
    intro_headline: str,
    post_date: date,
) -> Image.Image:
    product_name = normalize_product_name(product_name)
    company_name = normalize_company_name(company_name)
    canvas = news_post.add_scrim(model_post.open_background(background_bytes))
    draw = ImageDraw.Draw(canvas)
    kicker_font, kicker = fit_kicker(draw)
    product_font, product_lines, product_line_height = model_post.wrap_signal_name(
        draw,
        product_name,
        variant="condensed",
    )
    intro_font, intro_lines, intro_line_height = wrap_intro(draw, intro_headline)
    company_font, company_credit = model_post.fit_company_credit(draw, company_name)

    kicker_height = kicker_font.size + 18
    total_height = (
        kicker_height
        + 34
        + len(product_lines) * product_line_height
        + 38
        + len(intro_lines) * intro_line_height
        + 34
        + company_font.size
    )
    top = 650 - total_height // 2

    kicker_width = news_post.text_width(draw, kicker, kicker_font)
    draw.text(
        ((CANVAS_SIZE[0] - kicker_width) // 2, top),
        kicker,
        font=kicker_font,
        fill=news_post.BRAND_CORAL,
        stroke_width=1,
        stroke_fill=(0, 0, 0, 120),
    )
    top += kicker_height + 34

    for index, line in enumerate(product_lines):
        width = news_post.text_width(draw, line, product_font)
        fill = (
            news_post.BRAND_MINT
            if len(product_lines) > 1 and index == len(product_lines) - 1
            else news_post.WHITE
        )
        draw.text(
            ((CANVAS_SIZE[0] - width) // 2, top),
            line,
            font=product_font,
            fill=fill,
            stroke_width=2,
            stroke_fill=(0, 0, 0, 145),
        )
        top += product_line_height
    top += 38

    for line in intro_lines:
        width = news_post.text_width(draw, line, intro_font)
        draw.text(
            ((CANVAS_SIZE[0] - width) // 2, top),
            line,
            font=intro_font,
            fill=news_post.BRAND_MINT,
            stroke_width=1,
            stroke_fill=(0, 0, 0, 130),
        )
        top += intro_line_height
    top += 34

    company_width = news_post.text_width(draw, company_credit, company_font)
    draw.text(
        ((CANVAS_SIZE[0] - company_width) // 2, top),
        company_credit,
        font=company_font,
        fill=news_post.BRAND_CORAL,
        stroke_width=1,
        stroke_fill=(0, 0, 0, 130),
    )
    model_post.add_brand_chrome(canvas, post_date)
    return canvas.convert("RGB")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a Bits Today product-release carousel."
    )
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--copy-json", type=Path)
    parser.add_argument("--product-name")
    parser.add_argument("--company-name")
    parser.add_argument("--intro-headline")
    parser.add_argument(
        "--short-description",
        action="append",
        default=[],
        help="Source-grounded feature line; repeat once per secondary card.",
    )
    parser.add_argument("--background-input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date", type=news_post.parse_date, default=date.today())
    parser.add_argument("--keep-background", action="store_true")
    parser.add_argument("--image-model", default=news_post.DEFAULT_IMAGE_MODEL)
    parser.add_argument("--image-size", default=news_post.DEFAULT_IMAGE_SIZE)
    parser.add_argument(
        "--image-quality",
        choices=("low", "medium", "high", "auto"),
        default=news_post.DEFAULT_IMAGE_QUALITY,
    )
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
        if args.copy_json:
            if (
                args.product_name
                or args.company_name
                or args.intro_headline
                or args.short_description
            ):
                raise ValueError(
                    "Use either --copy-json or direct product copy arguments, not both."
                )
            (
                product_name,
                company_name,
                intro_headline,
                short_descriptions,
            ) = read_copy_file(args.copy_json)
        else:
            if not args.product_name or not args.company_name or not args.intro_headline:
                raise ValueError(
                    "--product-name, --company-name, and --intro-headline are "
                    "required without --copy-json."
                )
            product_name = normalize_product_name(args.product_name)
            company_name = normalize_company_name(args.company_name)
            intro_headline = normalize_intro_headline(args.intro_headline)
            short_descriptions = model_post.validate_short_descriptions(
                args.short_description
            )
            if not short_descriptions:
                raise ValueError("At least one --short-description is required.")

        source_text = news_description.read_tweet_text(args.tweet_json)
        source_images = model_post.read_source_images(args.tweet_json)
        secondary_count = (
            len(source_images) if source_images else len(short_descriptions)
        )
        if source_images and len(short_descriptions) != secondary_count:
            raise ValueError(
                f"Need exactly {secondary_count} short descriptions for "
                f"{secondary_count} secondary cards."
            )
        if not source_images and not 2 <= secondary_count <= 3:
            raise ValueError(
                "A no-media product post requires two or three short descriptions."
            )

        image_prompt = build_background_prompt(
            source_text,
            product_name,
            intro_headline,
        )
        if args.background_input:
            if not args.background_input.is_file():
                raise FileNotFoundError(
                    f"Background image not found: {args.background_input}"
                )
            background_bytes = args.background_input.read_bytes()
            background_source = str(args.background_input.resolve())
        else:
            news_post.require_api_key()
            background_bytes = news_post.generate_background(
                news_post.make_client(),
                image_prompt,
                model=args.image_model,
                size=args.image_size,
                quality=args.image_quality,
            )
            background_source = "openai-image-api"

        args.output_dir.mkdir(parents=True, exist_ok=True)
        slug = re_slug(product_name)
        primary_path = args.output_dir / f"01-product-{slug}.png"
        compose_primary(
            background_bytes,
            product_name,
            company_name,
            intro_headline,
            args.date,
        ).save(primary_path, format="PNG", optimize=True)

        secondary_paths: list[Path] = []
        if source_images:
            for index, (source_path, description) in enumerate(
                zip(source_images, short_descriptions),
                start=2,
            ):
                output_path = args.output_dir / f"{index:02d}-feature-{index - 1}.png"
                model_post.compose_media_secondary(
                    source_path,
                    description,
                    args.date,
                ).save(output_path, format="PNG", optimize=True)
                secondary_paths.append(output_path)
        else:
            for index, description in enumerate(short_descriptions, start=2):
                output_path = args.output_dir / f"{index:02d}-summary-{index - 1}.png"
                model_post.compose_fallback_secondary(
                    background_bytes,
                    description,
                    args.date,
                ).save(output_path, format="PNG", optimize=True)
                secondary_paths.append(output_path)

        if args.keep_background:
            (args.output_dir / "background.png").write_bytes(background_bytes)

        metadata = ProductPostMetadata(
            product_name=product_name,
            company_name=company_name,
            headline=build_headline(product_name),
            intro_headline=intro_headline,
            short_descriptions=short_descriptions[:secondary_count],
            primary_image=str(primary_path.resolve()),
            secondary_images=[str(path.resolve()) for path in secondary_paths],
            source_images=[str(path.resolve()) for path in source_images],
            background_source=background_source,
            primary_style="product-knowledge-stack",
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        metadata_path = args.output_dir / "post.json"
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


def re_slug(value: str) -> str:
    slug = "".join(
        character.lower() if character.isalnum() else "-" for character in value
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "product"


if __name__ == "__main__":
    raise SystemExit(main())
