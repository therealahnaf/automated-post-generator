#!/usr/bin/env python3
"""Render Bits Today model-announcement primary and feature cards."""

from __future__ import annotations

import argparse
import io
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TextIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.models.generate_copy import (
    MAX_CARDS,
    MAX_SHORT_DESCRIPTION_CHARACTERS,
    build_headline,
    normalize_model_name,
)
from tools.news import generate_description as news_description
from tools.news import generate_post as news_post


CANVAS_SIZE = news_post.CANVAS_SIZE
BACKGROUND_COLOR = (33, 33, 33, 255)
CARD_MARGIN = 58
MEDIA_TOP = 560
MEDIA_BOTTOM = 1225
PRIMARY_STYLE_CHOICES = (
    "brand-block",
    "launch-label",
    "glass-frame",
    "signal-stack",
    "signal-stack-condensed",
    "signal-stack-editorial",
    "signal-stack-industrial",
)

SIGNAL_FONT_CANDIDATES = {
    "condensed": (
        Path("C:/Windows/Fonts/impact.ttf"),
        Path("/usr/share/fonts/truetype/msttcorefonts/Impact.ttf"),
    ),
    "editorial": (
        Path("C:/Windows/Fonts/georgiab.ttf"),
        Path("/usr/share/fonts/truetype/msttcorefonts/Georgia_Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ),
    "industrial": (
        Path("C:/Windows/Fonts/bahnschrift.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"),
    ),
}


@dataclass(frozen=True)
class ModelPostMetadata:
    model_name: str
    headline: str
    short_descriptions: list[str]
    primary_image: str
    secondary_images: list[str]
    source_images: list[str]
    background_source: str
    primary_style: str
    created_at: str


def read_copy_file(path: Path) -> tuple[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    model_name = normalize_model_name(str(payload.get("model_name", "")))
    expected_headline = build_headline(model_name)
    if payload.get("headline") != expected_headline:
        raise ValueError(f"Copy headline must be exactly: {expected_headline}")
    raw_descriptions = payload.get("short_descriptions")
    if not isinstance(raw_descriptions, list) or not raw_descriptions:
        raise ValueError("Copy file has no short descriptions.")
    return model_name, validate_short_descriptions(raw_descriptions)


def validate_short_descriptions(values: list[Any]) -> list[str]:
    descriptions = []
    for value in values:
        description = news_description.normalize_source_text(str(value))
        if not description:
            raise ValueError("Short descriptions cannot be empty.")
        if len(description) > MAX_SHORT_DESCRIPTION_CHARACTERS:
            raise ValueError(
                "Short description exceeds "
                f"{MAX_SHORT_DESCRIPTION_CHARACTERS} characters."
            )
        descriptions.append(description)
    return descriptions


def read_source_images(tweet_json: Path) -> list[Path]:
    document = json.loads(tweet_json.read_text(encoding="utf-8"))
    items = document.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ValueError(f"No tweet item found in {tweet_json}.")
    photos = items[0].get("downloaded_photos")
    if not isinstance(photos, list):
        return []

    resolved: list[Path] = []
    for photo in photos[:MAX_CARDS]:
        if not isinstance(photo, dict) or not photo.get("local_path"):
            continue
        stored = Path(str(photo["local_path"]))
        candidates = [stored]
        if not stored.is_absolute():
            candidates.insert(0, tweet_json.parent / stored)
        candidates.append(tweet_json.parent / "media" / stored.name)
        for candidate in candidates:
            if candidate.is_file():
                resolved.append(candidate)
                break
    return resolved


def build_background_prompt(source_text: str, model_name: str) -> str:
    return f"""Use case: stylized-concept
Asset type: text-free 4:5 portrait background for a technology model launch
Primary request: Create a premium editorial technology visual inspired by the launch of {model_name}.
Announcement context: {source_text}
Scene/backdrop: abstract but credible AI compute environment, luminous data structures, refined depth, no people required
Style/medium: polished cinematic editorial photography blended with restrained abstract light structures
Composition/framing: 4:5 portrait; keep the central 45 percent calm and uncluttered for a centered title; place detail around the edges and in depth
Lighting/mood: consequential product launch, precise, modern, confident
Color palette: charcoal and black with restrained coral #FF5757 and mint #C2FFE1 accents
Constraints: no text, no letters, no numbers, no logos, no trademarks, no watermark, no border, no UI labels
""".strip()


def open_background(background_bytes: bytes) -> Image.Image:
    with Image.open(io.BytesIO(background_bytes)) as source:
        return ImageOps.fit(
            source.convert("RGB"),
            CANVAS_SIZE,
            method=Image.Resampling.LANCZOS,
        )


def wrap_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    max_lines: int,
    start_size: int,
    minimum_size: int,
) -> tuple[Any, list[str], int]:
    for size in range(start_size, minimum_size - 1, -2):
        font = news_post.load_roboto_font(size=size, bold=True)
        lines = news_post.wrap_headline(draw, text, font, max_width)
        line_height = size + 18
        if len(lines) <= max_lines:
            return font, lines, line_height
    raise ValueError("Text is too long for the model card.")


def load_signal_font(variant: str, size: int) -> ImageFont.FreeTypeFont:
    for path in SIGNAL_FONT_CANDIDATES[variant]:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return news_post.load_roboto_font(size=size, bold=True)


def wrap_signal_name(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    variant: str,
) -> tuple[Any, list[str], int]:
    for size in range(152, 75, -2):
        font = load_signal_font(variant, size)
        lines = news_post.wrap_headline(draw, text, font, 840)
        line_height = size + (10 if variant == "condensed" else 16)
        if len(lines) <= 2:
            return font, lines, line_height
    raise ValueError("Model name is too long for the signal-stack card.")


def draw_centered_blocks(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    center_y: int,
    max_width: int = 930,
) -> None:
    font, lines, line_height = wrap_centered(
        draw,
        text,
        max_width=max_width,
        max_lines=3,
        start_size=104,
        minimum_size=52,
    )
    top = center_y - (len(lines) * line_height) // 2
    colors = [
        (news_post.BRAND_CORAL, news_post.WHITE),
        (news_post.BRAND_MINT, news_post.INK),
    ]
    for index, line in enumerate(lines):
        width = news_post.text_width(draw, line, font)
        x = (CANVAS_SIZE[0] - width) // 2
        background, fill = colors[index % len(colors)]
        draw.rounded_rectangle(
            (x - 18, top - 7, x + width + 18, top + line_height - 3),
            radius=12,
            fill=background,
        )
        draw.text(
            (x, top),
            line,
            font=font,
            fill=fill,
            stroke_width=1,
            stroke_fill=(0, 0, 0, 90),
        )
        top += line_height


def draw_launch_label(
    draw: ImageDraw.ImageDraw,
    model_name: str,
    *,
    center_y: int,
) -> None:
    label_font = news_post.load_roboto_font(size=38, bold=True)
    label = "Meet"
    label_width = news_post.text_width(draw, label, label_font)
    model_font, lines, line_height = wrap_centered(
        draw,
        model_name,
        max_width=920,
        max_lines=2,
        start_size=118,
        minimum_size=62,
    )
    total_height = 62 + len(lines) * line_height + 24
    top = center_y - total_height // 2
    label_x = (CANVAS_SIZE[0] - label_width) // 2
    draw.rounded_rectangle(
        (label_x - 22, top - 8, label_x + label_width + 22, top + 49),
        radius=25,
        fill=news_post.BRAND_CORAL,
    )
    draw.text((label_x, top), label, font=label_font, fill=news_post.WHITE)
    top += 76

    for line in lines:
        width = news_post.text_width(draw, line, model_font)
        x = (CANVAS_SIZE[0] - width) // 2
        draw.text(
            (x, top),
            line,
            font=model_font,
            fill=news_post.WHITE,
            stroke_width=2,
            stroke_fill=(0, 0, 0, 150),
        )
        top += line_height
    rule_y = top + 5
    draw.rounded_rectangle(
        (CANVAS_SIZE[0] // 2 - 130, rule_y, CANVAS_SIZE[0] // 2 + 52, rule_y + 8),
        radius=4,
        fill=news_post.BRAND_CORAL,
    )
    draw.rounded_rectangle(
        (CANVAS_SIZE[0] // 2 + 52, rule_y, CANVAS_SIZE[0] // 2 + 130, rule_y + 8),
        radius=4,
        fill=news_post.BRAND_MINT,
    )


def draw_glass_frame(
    draw: ImageDraw.ImageDraw,
    model_name: str,
    *,
    center_y: int,
) -> None:
    frame = (76, center_y - 225, CANVAS_SIZE[0] - 76, center_y + 225)
    draw.rounded_rectangle(
        frame,
        radius=34,
        fill=(8, 14, 18, 245),
        outline=news_post.BRAND_MINT,
        width=3,
    )
    draw.line(
        (frame[0] + 36, frame[1] + 3, frame[0] + 250, frame[1] + 3),
        fill=news_post.BRAND_CORAL,
        width=8,
    )
    label_font = news_post.load_roboto_font(size=42, bold=True)
    label = "Meet"
    label_width = news_post.text_width(draw, label, label_font)
    draw.text(
        ((CANVAS_SIZE[0] - label_width) // 2, frame[1] + 54),
        label,
        font=label_font,
        fill=news_post.BRAND_CORAL,
    )
    model_font, lines, line_height = wrap_centered(
        draw,
        model_name,
        max_width=820,
        max_lines=2,
        start_size=100,
        minimum_size=56,
    )
    top = center_y - (len(lines) * line_height) // 2 + 35
    for index, line in enumerate(lines):
        width = news_post.text_width(draw, line, model_font)
        fill = news_post.BRAND_MINT if index == len(lines) - 1 else news_post.WHITE
        draw.text(
            ((CANVAS_SIZE[0] - width) // 2, top),
            line,
            font=model_font,
            fill=fill,
        )
        top += line_height


def draw_signal_stack(
    draw: ImageDraw.ImageDraw,
    model_name: str,
    *,
    center_y: int,
    font_variant: str = "industrial",
) -> None:
    model_font, lines, line_height = wrap_signal_name(
        draw,
        model_name,
        variant=font_variant,
    )
    label_font = news_post.load_roboto_font(size=54, bold=True, italic=True)
    total_height = 70 + len(lines) * line_height
    top = center_y - total_height // 2
    if font_variant == "condensed":
        label_width = news_post.text_width(draw, "Meet", label_font)
        draw.text(
            ((CANVAS_SIZE[0] - label_width) // 2, top),
            "Meet",
            font=label_font,
            fill=news_post.BRAND_CORAL,
            stroke_width=1,
            stroke_fill=(0, 0, 0, 120),
        )
        top += 76
        for index, line in enumerate(lines):
            width = news_post.text_width(draw, line, model_font)
            fill = news_post.BRAND_MINT if index == len(lines) - 1 else news_post.WHITE
            draw.text(
                ((CANVAS_SIZE[0] - width) // 2, top),
                line,
                font=model_font,
                fill=fill,
                stroke_width=2,
                stroke_fill=(0, 0, 0, 145),
            )
            top += line_height
        return

    x = 154
    rule_x = 98
    draw.rounded_rectangle(
        (rule_x, top - 12, rule_x + 11, top + total_height + 12),
        radius=5,
        fill=news_post.BRAND_CORAL,
    )
    draw.rounded_rectangle(
        (rule_x, top + total_height - 92, rule_x + 11, top + total_height + 12),
        radius=5,
        fill=news_post.BRAND_MINT,
    )
    draw.text(
        (x, top),
        "Meet",
        font=label_font,
        fill=news_post.BRAND_CORAL,
        stroke_width=1,
        stroke_fill=(0, 0, 0, 120),
    )
    top += 76
    for index, line in enumerate(lines):
        fill = news_post.BRAND_MINT if index == len(lines) - 1 else news_post.WHITE
        draw.text(
            (x, top),
            line,
            font=model_font,
            fill=fill,
            stroke_width=2,
            stroke_fill=(0, 0, 0, 145),
        )
        top += line_height


def draw_short_description(
    draw: ImageDraw.ImageDraw,
    description: str,
    *,
    center_y: int,
    max_width: int = 920,
) -> None:
    font, lines, line_height = wrap_centered(
        draw,
        description,
        max_width=max_width,
        max_lines=4,
        start_size=62,
        minimum_size=38,
    )
    top = center_y - (len(lines) * line_height) // 2
    for index, line in enumerate(lines):
        width = news_post.text_width(draw, line, font)
        x = (CANVAS_SIZE[0] - width) // 2
        fill = news_post.WHITE
        if index == 0:
            fill = news_post.BRAND_MINT
        draw.text(
            (x, top),
            line,
            font=font,
            fill=fill,
            stroke_width=1,
            stroke_fill=(0, 0, 0, 130),
        )
        top += line_height


def paste_compact_logo(canvas: Image.Image) -> None:
    with Image.open(news_post.DEFAULT_BRAND_LOGO) as source:
        logo = source.convert("RGBA")
        alpha_box = logo.getchannel("A").getbbox()
        if alpha_box:
            logo = logo.crop(alpha_box)
        logo.thumbnail((82, 82), Image.Resampling.LANCZOS)
    canvas.alpha_composite(
        logo,
        (canvas.width - 26 - logo.width, canvas.height - 24 - logo.height),
    )


def add_brand_chrome(
    canvas: Image.Image,
    post_date: date,
    *,
    compact: bool = False,
) -> None:
    draw = ImageDraw.Draw(canvas)
    news_post.draw_byline(
        draw,
        "Bits Today",
        post_date,
        CARD_MARGIN,
        1288 if compact else 1280,
        source_color=news_post.BRAND_CORAL,
        detail_color=news_post.BRAND_MINT,
    )
    if compact:
        paste_compact_logo(canvas)
    else:
        news_post.paste_brand_logo(canvas, news_post.DEFAULT_BRAND_LOGO)


def compose_primary(
    background_bytes: bytes,
    model_name: str,
    post_date: date,
    style: str = "signal-stack-condensed",
) -> Image.Image:
    canvas = news_post.add_scrim(open_background(background_bytes))
    if style not in PRIMARY_STYLE_CHOICES:
        raise ValueError(f"Unknown primary style: {style}")
    draw = ImageDraw.Draw(canvas)
    if style == "brand-block":
        draw_centered_blocks(draw, build_headline(model_name), center_y=650)
    else:
        if style.startswith("signal-stack"):
            variant = {
                "signal-stack": "industrial",
                "signal-stack-condensed": "condensed",
                "signal-stack-editorial": "editorial",
                "signal-stack-industrial": "industrial",
            }[style]
            draw_signal_stack(
                draw,
                model_name,
                center_y=650,
                font_variant=variant,
            )
        else:
            {
                "launch-label": draw_launch_label,
                "glass-frame": draw_glass_frame,
            }[style](draw, model_name, center_y=650)
    add_brand_chrome(canvas, post_date)
    return canvas.convert("RGB")


def add_top_gradient(canvas: Image.Image) -> None:
    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    pixels = overlay.load()
    for y in range(0, MEDIA_TOP):
        alpha = int(90 * (1 - y / MEDIA_TOP))
        for x in range(CANVAS_SIZE[0]):
            pixels[x, y] = (0, 0, 0, alpha)
    canvas.alpha_composite(overlay)


def paste_lower_media(canvas: Image.Image, source_path: Path) -> None:
    with Image.open(source_path) as source:
        media = ImageOps.exif_transpose(source).convert("RGBA")
        media.thumbnail(
            (CANVAS_SIZE[0] - CARD_MARGIN * 2, MEDIA_BOTTOM - MEDIA_TOP),
            Image.Resampling.LANCZOS,
        )
    x = (CANVAS_SIZE[0] - media.width) // 2
    y = MEDIA_BOTTOM - media.height
    radius = 28

    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x - 9, y - 7, x + media.width + 9, y + media.height + 10),
        radius=radius + 8,
        fill=(0, 0, 0, 190),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))

    border = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        (x - 4, y - 4, x + media.width + 4, y + media.height + 4),
        radius=radius + 4,
        fill=news_post.BRAND_MINT,
    )
    canvas.alpha_composite(border)
    mask = Image.new("L", media.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, media.width - 1, media.height - 1),
        radius=radius,
        fill=255,
    )
    canvas.paste(media, (x, y), mask)


def compose_media_secondary(
    source_path: Path,
    short_description: str,
    post_date: date,
) -> Image.Image:
    canvas = Image.new("RGBA", CANVAS_SIZE, BACKGROUND_COLOR)
    add_top_gradient(canvas)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((CARD_MARGIN, 54, CARD_MARGIN + 150, 63), fill=news_post.BRAND_CORAL)
    draw.rectangle(
        (CARD_MARGIN + 150, 54, CARD_MARGIN + 330, 63),
        fill=news_post.BRAND_MINT,
    )
    draw_short_description(draw, short_description, center_y=285)
    paste_lower_media(canvas, source_path)
    add_brand_chrome(canvas, post_date, compact=True)
    return canvas.convert("RGB")


def compose_fallback_secondary(
    background_bytes: bytes,
    short_description: str,
    post_date: date,
) -> Image.Image:
    canvas = news_post.add_scrim(open_background(background_bytes))
    draw_short_description(
        ImageDraw.Draw(canvas),
        short_description,
        center_y=650,
    )
    add_brand_chrome(canvas, post_date)
    return canvas.convert("RGB")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a Bits Today model-announcement carousel."
    )
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--copy-json", type=Path)
    parser.add_argument("--model-name")
    parser.add_argument(
        "--short-description",
        action="append",
        default=[],
        help="Source-grounded feature line; repeat once per secondary card.",
    )
    parser.add_argument("--background-input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--date", type=news_post.parse_date, default=date.today())
    parser.add_argument(
        "--primary-style",
        choices=PRIMARY_STYLE_CHOICES,
        default="signal-stack-condensed",
        help="Typography treatment for the centered primary headline.",
    )
    parser.add_argument("--keep-background", action="store_true")
    parser.add_argument(
        "--image-model",
        default=news_post.DEFAULT_IMAGE_MODEL,
    )
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
            if args.model_name or args.short_description:
                raise ValueError(
                    "Use either --copy-json or --model-name with "
                    "--short-description, not both."
                )
            model_name, short_descriptions = read_copy_file(args.copy_json)
        else:
            if not args.model_name:
                raise ValueError("--model-name is required without --copy-json.")
            model_name = normalize_model_name(args.model_name)
            short_descriptions = validate_short_descriptions(args.short_description)
            if not short_descriptions:
                raise ValueError("At least one --short-description is required.")

        source_text = news_description.read_tweet_text(args.tweet_json)
        source_images = read_source_images(args.tweet_json)
        secondary_count = len(source_images) if source_images else len(short_descriptions)
        if source_images and len(short_descriptions) != secondary_count:
            raise ValueError(
                f"Need exactly {secondary_count} short descriptions for "
                f"{secondary_count} secondary cards."
            )
        if not source_images and not 2 <= secondary_count <= 3:
            raise ValueError(
                "A no-media model post requires two or three short descriptions."
            )

        image_prompt = build_background_prompt(source_text, model_name)
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
        slug = "".join(
            character.lower() if character.isalnum() else "-"
            for character in model_name
        ).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        primary_path = args.output_dir / f"01-meet-{slug}.png"
        compose_primary(
            background_bytes,
            model_name,
            args.date,
            style=args.primary_style,
        ).save(
            primary_path,
            format="PNG",
            optimize=True,
        )

        secondary_paths: list[Path] = []
        if source_images:
            for index, (source_path, description) in enumerate(
                zip(source_images, short_descriptions),
                start=2,
            ):
                output_path = args.output_dir / f"{index:02d}-feature-{index - 1}.png"
                compose_media_secondary(source_path, description, args.date).save(
                    output_path,
                    format="PNG",
                    optimize=True,
                )
                secondary_paths.append(output_path)
        else:
            for index, description in enumerate(short_descriptions, start=2):
                output_path = (
                    args.output_dir / f"{index:02d}-summary-{index - 1}.png"
                )
                compose_fallback_secondary(
                    background_bytes,
                    description,
                    args.date,
                ).save(output_path, format="PNG", optimize=True)
                secondary_paths.append(output_path)

        if args.keep_background:
            (args.output_dir / "background.png").write_bytes(background_bytes)

        metadata = ModelPostMetadata(
            model_name=model_name,
            headline=build_headline(model_name),
            short_descriptions=short_descriptions[:secondary_count],
            primary_image=str(primary_path.resolve()),
            secondary_images=[str(path.resolve()) for path in secondary_paths],
            source_images=[str(path.resolve()) for path in source_images],
            background_source=background_source,
            primary_style=args.primary_style,
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


if __name__ == "__main__":
    raise SystemExit(main())
