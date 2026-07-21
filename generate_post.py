#!/usr/bin/env python3
"""Generate a portrait tech-news social post with OpenAI and Pillow."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


CANVAS_SIZE = (1080, 1350)
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1280"
DEFAULT_IMAGE_QUALITY = "medium"
DEFAULT_POST_SOURCE = "Bits Today"
RED = (225, 24, 32, 255)
WHITE = (250, 250, 248, 255)


@dataclass(frozen=True)
class PostMetadata:
    source_text: str
    title: str
    image_prompt: str
    background_source: str
    image_model: str
    image_size: str
    image_quality: str
    created_at: str


def normalize_news_text(value: str) -> str:
    """Normalize pasted news copy while preserving ordinary punctuation."""
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in your shell before running this script."
        )


def make_client() -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The openai package is missing. Run: python -m pip install -r requirements.txt"
        ) from exc
    return OpenAI()


def build_image_prompt(news_text: str, title: str) -> str:
    return f"""Use case: photorealistic-natural
Asset type: vertical editorial background for a technology-news social post
Primary request: Create a believable editorial photograph inspired by the news context below.
News context: {news_text}
Editorial angle: {title}
Scene/backdrop: choose a credible editorial scene that directly fits the current news context, such as courtrooms, government offices, corporate headquarters, data centers, newsrooms, infrastructure, devices, documents, or city settings when relevant
Style/medium: photorealistic documentary news photography, real materials, grounded details, no fantasy elements
Composition/framing: 4:5 portrait; dramatic wide or medium editorial view; keep the upper 35 percent darker and visually calm for a headline; place the strongest story-specific detail in the middle and lower portions
Lighting/mood: serious, high-stakes, cinematic but realistic; controlled contrast with restrained red accents where natural to the scene
Constraints: no people in close-up; no readable signs; no logos; no trademarks; no text; no captions; no borders; no watermark; do not render the headline inside the image
""".strip()


def generate_background(
    client: Any,
    prompt: str,
    model: str,
    size: str,
    quality: str,
) -> bytes:
    result = client.images.generate(
        model=model,
        prompt=prompt,
        size=size,
        quality=quality,
        output_format="png",
    )
    if not result.data or not result.data[0].b64_json:
        raise RuntimeError("OpenAI did not return image data.")
    return base64.b64decode(result.data[0].b64_json)


def find_font(bold: bool, override: Path | None = None) -> str:
    if override:
        if not override.is_file():
            raise FileNotFoundError(f"Font not found: {override}")
        return str(override)

    names = (
        [
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
        if bold
        else [
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    for candidate in names:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("No suitable Arial or DejaVu Sans font was found.")


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def wrap_headline(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fit_headline(
    draw: ImageDraw.ImageDraw,
    title: str,
    font_path: str,
    max_width: int,
    max_height: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in range(76, 43, -2):
        font = ImageFont.truetype(font_path, size=size)
        lines = wrap_headline(draw, title, font, max_width)
        line_height = max(size + 8, draw.textbbox((0, 0), "Ag", font=font)[3] + 8)
        if len(lines) <= 5 and len(lines) * line_height <= max_height:
            return font, lines, line_height
    font = ImageFont.truetype(font_path, size=42)
    lines = wrap_headline(draw, title, font, max_width)
    return font, lines[:6], 50


def build_byline(source: str) -> str:
    """Return the only brand text rendered below the headline."""
    source = normalize_news_text(source).strip(" |")
    if source.casefold() == "bits today desk":
        return DEFAULT_POST_SOURCE
    return source or DEFAULT_POST_SOURCE


def add_scrim(image: Image.Image) -> Image.Image:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = overlay.load()
    width, height = image.size
    top_fade_end = int(height * 0.48)
    for y in range(height):
        if y < top_fade_end:
            alpha = int(225 - (185 * y / top_fade_end))
        else:
            alpha = int(max(0, 42 * (y - height * 0.72) / (height * 0.28)))
        for x in range(width):
            edge = int(24 * abs((x / max(1, width - 1)) - 0.5) * 2)
            pixels[x, y] = (0, 0, 0, min(235, alpha + edge))
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def compose_post(
    background_bytes: bytes,
    title: str,
    source: str,
    post_date: date,
    credit: str,
    font_override: Path | None = None,
) -> Image.Image:
    with Image.open(io.BytesIO(background_bytes)) as generated:
        background = ImageOps.fit(
            generated.convert("RGB"),
            CANVAS_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    canvas = add_scrim(background)
    draw = ImageDraw.Draw(canvas)

    margin = 62
    max_width = CANVAS_SIZE[0] - margin * 2
    bold_path = find_font(True, font_override)
    regular_path = find_font(False, None)
    headline_font, lines, line_height = fit_headline(
        draw, title, bold_path, max_width=max_width, max_height=390
    )

    y = 58
    highlighted_lines = min(2, max(1, len(lines) - 1))
    for index, line in enumerate(lines):
        width = text_width(draw, line, headline_font)
        if index < highlighted_lines:
            draw.rounded_rectangle(
                (margin - 10, y - 3, margin + width + 12, y + line_height - 2),
                radius=3,
                fill=RED,
            )
        draw.text(
            (margin, y),
            line,
            font=headline_font,
            fill=WHITE,
            stroke_width=1,
            stroke_fill=(0, 0, 0, 100),
        )
        y += line_height

    byline_font = ImageFont.truetype(regular_path, size=23)
    byline = build_byline(source)
    draw.text((margin, y + 13), byline, font=byline_font, fill=(235, 235, 232, 235))

    return canvas.convert("RGB")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a 1080x1350 tech-news post with OpenAI and Pillow."
    )
    parser.add_argument("news", help="The source tech-news sentence.")
    parser.add_argument(
        "--headline",
        required=True,
        help="Headline to render. It is not generated by this script.",
    )
    parser.add_argument("--output", type=Path, default=Path("output/post.png"))
    parser.add_argument(
        "--background-input",
        type=Path,
        help="Reuse a local background and skip the OpenAI image API call.",
    )
    parser.add_argument(
        "--source",
        default=os.getenv("POST_SOURCE", DEFAULT_POST_SOURCE),
        help=f"Brand name rendered below the headline (default: POST_SOURCE or '{DEFAULT_POST_SOURCE}').",
    )
    parser.add_argument(
        "--credit",
        default="",
        help="Deprecated compatibility option. Credits are not rendered.",
    )
    parser.add_argument("--date", type=parse_date, default=date.today())
    parser.add_argument("--font", type=Path, help="Optional bold TrueType/OpenType font.")
    parser.add_argument(
        "--image-model", default=os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL)
    )
    parser.add_argument("--image-size", default=DEFAULT_IMAGE_SIZE)
    parser.add_argument(
        "--image-quality",
        choices=("low", "medium", "high", "auto"),
        default=DEFAULT_IMAGE_QUALITY,
    )
    parser.add_argument(
        "--keep-background",
        action="store_true",
        help="Save the generated background beside the finished post.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source_text = normalize_news_text(args.news)
        if not source_text:
            raise ValueError("The news sentence cannot be empty.")
        title = normalize_news_text(args.headline).strip(" \"'“”").rstrip(".")
        if not title:
            raise ValueError("The headline cannot be empty.")
        image_prompt = build_image_prompt(source_text, title)
        if args.background_input:
            if not args.background_input.is_file():
                raise FileNotFoundError(
                    f"Background image not found: {args.background_input}"
                )
            print("Reusing local editorial background...", file=sys.stderr)
            background_bytes = args.background_input.read_bytes()
            background_source = str(args.background_input.resolve())
        else:
            require_api_key()
            client = make_client()
            print("Generating editorial background...", file=sys.stderr)
            background_bytes = generate_background(
                client,
                image_prompt,
                model=args.image_model,
                size=args.image_size,
                quality=args.image_quality,
            )
            background_source = "openai-image-api"

        print("Composing post with Pillow...", file=sys.stderr)
        post = compose_post(
            background_bytes,
            title,
            source=args.source,
            post_date=args.date,
            credit=args.credit,
            font_override=args.font,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        post.save(args.output, format="PNG", optimize=True)

        if args.keep_background:
            background_path = args.output.with_name(f"{args.output.stem}-background.png")
            background_path.write_bytes(background_bytes)

        metadata = PostMetadata(
            source_text=source_text,
            title=title,
            image_prompt=image_prompt,
            background_source=background_source,
            image_model=args.image_model,
            image_size=args.image_size,
            image_quality=args.image_quality,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        metadata_path = args.output.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(asdict(metadata), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(f"Title: {title}")
        print(f"Post: {args.output.resolve()}")
        print(f"Metadata: {metadata_path.resolve()}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
