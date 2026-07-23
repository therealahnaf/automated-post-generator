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
from typing import Any, TextIO

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps
from dotenv import load_dotenv

try:
    from .post_language import (
        HEADLINE_HIGHLIGHT_STYLES,
        read_headline_highlight,
        read_post_language,
    )
except ImportError:
    from post_language import (
        HEADLINE_HIGHLIGHT_STYLES,
        read_headline_highlight,
        read_post_language,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


CANVAS_SIZE = (1080, 1350)
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1280"
DEFAULT_IMAGE_QUALITY = "medium"
TEXT_GENERATION_MODEL = "gpt-5.6-luna"
DEFAULT_POST_SOURCE = "Bits Today"
DEFAULT_BRAND_LOGO = PROJECT_ROOT / "bitstodaylogo-trans.png"
ROBOTO_REGULAR = PROJECT_ROOT / "assets" / "fonts" / "Roboto-Variable.ttf"
ROBOTO_ITALIC = PROJECT_ROOT / "assets" / "fonts" / "Roboto-Italic-Variable.ttf"
BRAND_CORAL = (255, 87, 87, 255)
BRAND_MINT = (194, 255, 225, 255)
INK = (12, 17, 21, 255)
WHITE = (250, 250, 248, 255)
STYLE_CHOICES = ("brand-block", "editorial-italic", "split-signal")
FEATURE_IMAGE_MIN_SIZE = (640, 480)
FEATURE_IMAGE_MAX_RENDERED_SIZE = (940, 620)
FEATURE_IMAGE_MIN_RENDERED_SIDE = 240


@dataclass(frozen=True)
class PostMetadata:
    source_text: str
    title: str
    english_title: str
    post_language: str
    headline_highlight: str
    image_prompt: str
    background_source: str
    image_model: str
    image_size: str
    image_quality: str
    style: str
    logo_source: str
    feature_image_source: str | None
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


HEADLINE_TRANSLATION_INSTRUCTIONS = """You are the Bangla headline translator for Bits Today.
Translate the supplied approved English news headline into natural, concise
Bangla suitable for a social-news image. Preserve every name, product name,
organization name, model name, number, unit, attribution, and uncertainty.
Keep names and named products in their original English spelling: never
translate or transliterate them into Bangla script. Do not add facts,
commentary, labels, quotation marks, hashtags, or markdown. Output only the
Bangla headline."""


def contains_bangla_text(value: str) -> bool:
    return bool(re.search(r"[\u0980-\u09ff]", value))


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    fragments: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = (
                content.get("text")
                if isinstance(content, dict)
                else getattr(content, "text", None)
            )
            if isinstance(text, str):
                fragments.append(text)
    if fragments:
        return "\n".join(fragments).strip()
    raise RuntimeError("OpenAI did not return a translated headline.")


def translate_headline_to_bangla(
    client: Any,
    english_headline: str,
    *,
    max_output_tokens: int = 300,
) -> str:
    response = client.responses.create(
        model=TEXT_GENERATION_MODEL,
        input=[
            {"role": "system", "content": HEADLINE_TRANSLATION_INSTRUCTIONS},
            {
                "role": "user",
                "content": (
                    "Translate this approved headline into Bangla. Preserve its "
                    "meaning and keep it compact. Keep all proper names, "
                    "organizations, products, and model names exactly in English "
                    "spelling:\n\n" + english_headline
                ),
            },
        ],
        max_output_tokens=max_output_tokens,
        reasoning={"effort": "none"},
    )
    translated = normalize_news_text(extract_response_text(response)).strip(
        " \"'“”"
    ).rstrip("।.")
    if not contains_bangla_text(translated):
        raise RuntimeError("OpenAI did not return a Bangla headline translation.")
    if len(translated) > 180:
        raise RuntimeError("Translated Bangla headline exceeds 180 characters.")
    return translated


def build_image_prompt(news_text: str, title: str) -> str:
    return f"""Use case: photorealistic-natural
Asset type: vertical editorial background for a technology-news social post
Primary request: Create a believable editorial photograph inspired by the news context below.
News context: {news_text}
Editorial angle: {title}
Scene/backdrop: choose a credible editorial scene that directly fits the current news context, such as courtrooms, government offices, corporate headquarters, data centers, newsrooms, infrastructure, devices, documents, city settings, country flags or emotions of a person (angry, sad, happy) when relevant
Style/medium: photorealistic documentary news photography, real materials, grounded details, no fantasy elements
Composition/framing: 4:5 portrait; dramatic wide or medium editorial view; keep the upper 35 percent darker and visually calm for a headline; place the strongest story-specific detail in the middle and lower portions
Lighting/mood: serious, high-stakes, cinematic but realistic; controlled contrast with restrained red accents where natural to the scene
Constraints: no readable signs; no logos; no trademarks; no text; no captions; no borders; no watermark; do not render the headline inside the image
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


def find_font_variant(variant: str, override: Path | None = None) -> str:
    """Locate a display, serif, italic, or sans font for a style preset."""
    if override and variant in {"bold", "bold-italic", "display"}:
        if not override.is_file():
            raise FileNotFoundError(f"Font not found: {override}")
        return str(override)

    candidates = {
        "regular": [
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ],
        "bold": [
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ],
        "italic": [
            Path(r"C:\Windows\Fonts\ariali.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ],
        "bold-italic": [
            Path(r"C:\Windows\Fonts\arialbi.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
        ],
        "serif-bold": [
            Path(r"C:\Windows\Fonts\georgiab.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
        ],
        "serif-bold-italic": [
            Path(r"C:\Windows\Fonts\georgiaz.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf"),
        ],
        "display": [
            Path(r"C:\Windows\Fonts\bahnschrift.ttf"),
            Path(r"C:\Windows\Fonts\impact.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"),
        ],
    }
    if variant not in candidates:
        raise ValueError(f"Unknown font variant: {variant}")
    for candidate in candidates[variant]:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(f"No suitable font was found for variant '{variant}'.")


def load_font(
    font_path: str | Path,
    *,
    size: int,
    index: int = 0,
    variation_name: str | None = None,
) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(font_path), size=size, index=index)
    if variation_name:
        font.set_variation_by_name(variation_name)
    return font


def load_roboto_font(
    *,
    size: int,
    bold: bool,
    italic: bool = False,
) -> ImageFont.FreeTypeFont:
    path = ROBOTO_ITALIC if italic else ROBOTO_REGULAR
    if not path.is_file():
        raise FileNotFoundError(f"Bundled Roboto font not found: {path}")
    variation = "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
    return load_font(path, size=size, variation_name=variation)


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


def is_english_name_token(token: str) -> bool:
    """Whether a headline token needs the Latin fallback font.

    Bangla display fonts are not guaranteed to include Latin glyphs. Proper
    names deliberately retained in English therefore need explicit fallback.
    """
    return bool(re.fullmatch(r"[A-Za-z0-9]+", token))


def mixed_script_runs(token: str) -> list[tuple[str, bool]]:
    """Split a token into Latin and non-Latin runs for mixed Bangla copy.

    A headline can retain an English name and attach Bangla grammar to it, for
    example ``Conjecture-এর``. Drawing that whole token with the Latin fallback
    loses the Bengali suffix; drawing it wholly with the Bengali font can lose
    the name. Keep each run with the font that supports its script.
    """
    return [
        (run, is_english_name_token(run))
        for run in re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]+", token)
    ]


def mixed_text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    bangla_font: ImageFont.FreeTypeFont,
    latin_font: ImageFont.FreeTypeFont,
) -> int:
    words = text.split()
    if not words:
        return 0
    space_width = text_width(draw, " ", bangla_font)
    width = 0
    for index, word in enumerate(words):
        width += sum(
            text_width(draw, run, latin_font if is_latin else bangla_font)
            for run, is_latin in mixed_script_runs(word)
        )
        if index < len(words) - 1:
            width += space_width
    return width


def wrap_mixed_headline(
    draw: ImageDraw.ImageDraw,
    text: str,
    bangla_font: ImageFont.FreeTypeFont,
    latin_font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        proposal = f"{current} {word}".strip()
        if current and mixed_text_width(draw, proposal, bangla_font, latin_font) > max_width:
            lines.append(current)
            current = word
        else:
            current = proposal
    if current:
        lines.append(current)
    return lines


def draw_mixed_headline_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    bangla_font: ImageFont.FreeTypeFont,
    latin_font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
) -> None:
    x, y = position
    space_width = text_width(draw, " ", bangla_font)
    for word in text.split():
        for run, is_latin in mixed_script_runs(word):
            font = latin_font if is_latin else bangla_font
            draw.text((x, y), run, font=font, fill=fill)
            x += text_width(draw, run, font)
        x += space_width


def fit_headline(
    draw: ImageDraw.ImageDraw,
    title: str,
    font_path: str,
    max_width: int,
    max_height: int,
    font_index: int = 0,
    variation_name: str | None = None,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in range(76, 43, -2):
        font = load_font(
            font_path,
            size=size,
            index=font_index,
            variation_name=variation_name,
        )
        lines = wrap_headline(draw, title, font, max_width)
        line_height = max(size + 8, draw.textbbox((0, 0), "Ag", font=font)[3] + 8)
        if len(lines) <= 5 and len(lines) * line_height <= max_height:
            return font, lines, line_height
    font = load_font(
        font_path,
        size=42,
        index=font_index,
        variation_name=variation_name,
    )
    lines = wrap_headline(draw, title, font, max_width)
    return font, lines[:6], 50


def find_bangla_font(*, bold: bool) -> tuple[str, int]:
    candidates = [
        (Path(r"C:\Windows\Fonts\Nirmala.ttc"), 1 if bold else 0),
        (
            Path("/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf")
            if bold
            else Path("/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf"),
            0,
        ),
        (
            Path("/usr/share/fonts/opentype/noto/NotoSansBengali-Bold.ttf")
            if bold
            else Path("/usr/share/fonts/opentype/noto/NotoSansBengali-Regular.ttf"),
            0,
        ),
    ]
    for path, index in candidates:
        if path.is_file():
            return str(path), index
    raise FileNotFoundError(
        "No Bengali-capable font was found. Install Nirmala UI or Noto Sans Bengali."
    )


def build_byline(source: str) -> str:
    """Return the only brand text rendered below the headline."""
    source = normalize_news_text(source).strip(" |")
    if source.casefold() == "bits today desk":
        return DEFAULT_POST_SOURCE
    return source or DEFAULT_POST_SOURCE


def build_byline_text(source: str, post_date: date) -> str:
    return f"{build_byline(source)} | {post_date.strftime('%d %b %Y')}"


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


def find_first_tweet_photo(tweet_json: Path) -> Path | None:
    """Return the first downloaded photo in persisted source order."""
    payload = json.loads(tweet_json.read_text(encoding="utf-8"))
    for item in payload.get("items", []):
        photos = item.get("downloaded_photos")
        if not isinstance(photos, list):
            photos = [
                media
                for media in item.get("downloaded_media", [])
                if media.get("kind") == "photo"
            ]
        for photo in photos:
            raw_path = photo.get("local_path")
            if not raw_path:
                continue
            candidate = Path(raw_path)
            candidates = [candidate]
            if not candidate.is_absolute():
                candidates.insert(0, tweet_json.parent / candidate)
            candidates.append(tweet_json.parent / "media" / candidate.name)
            for resolved in candidates:
                if resolved.is_file():
                    return resolved
    return None


def feature_photo_meets_minimum(
    feature_image_path: Path,
    minimum_size: tuple[int, int] = FEATURE_IMAGE_MIN_SIZE,
    max_rendered_size: tuple[int, int] = FEATURE_IMAGE_MAX_RENDERED_SIZE,
    minimum_rendered_side: int = FEATURE_IMAGE_MIN_RENDERED_SIDE,
) -> bool:
    """Return whether a photo is large enough and useful in the primary inset."""
    if not feature_image_path.is_file():
        raise FileNotFoundError(f"Feature image not found: {feature_image_path}")
    with Image.open(feature_image_path) as source:
        photo = ImageOps.exif_transpose(source)
        width, height = photo.size
        source_short, source_long = sorted((width, height))
        minimum_short, minimum_long = sorted(minimum_size)
        if source_short < minimum_short or source_long < minimum_long:
            return False
        rendered = photo.copy()
        rendered.thumbnail(max_rendered_size, Image.Resampling.LANCZOS)
    return min(rendered.size) >= minimum_rendered_side


def paste_feature_photo(
    canvas: Image.Image,
    feature_image_path: Path,
    *,
    top: int = 550,
    max_size: tuple[int, int] = FEATURE_IMAGE_MAX_RENDERED_SIZE,
    radius: int = 28,
) -> None:
    """Overlay a complete, uncropped tweet photo in a rounded editorial frame."""
    if not feature_image_path.is_file():
        raise FileNotFoundError(f"Feature image not found: {feature_image_path}")

    with Image.open(feature_image_path) as source:
        photo = ImageOps.exif_transpose(source).convert("RGBA")
        photo.thumbnail(max_size, Image.Resampling.LANCZOS)

    x = (canvas.width - photo.width) // 2
    y = min(top, canvas.height - 160 - photo.height)

    rounded_mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(rounded_mask).rounded_rectangle(
        (0, 0, photo.width - 1, photo.height - 1),
        radius=radius,
        fill=255,
    )
    combined_alpha = ImageChops.multiply(photo.getchannel("A"), rounded_mask)

    shadow_alpha = Image.new("L", canvas.size, 0)
    shadow_alpha.paste(combined_alpha, (x, y + 6))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(18))
    shadow_alpha = shadow_alpha.point(lambda value: value * 190 // 255)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_alpha)
    canvas.alpha_composite(shadow)

    border = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        (x - 5, y - 5, x + photo.width + 5, y + photo.height + 5),
        radius=radius + 5,
        outline=BRAND_MINT,
        width=5,
    )
    canvas.alpha_composite(border)

    photo.putalpha(combined_alpha)
    canvas.alpha_composite(photo, (x, y))


def draw_byline(
    draw: ImageDraw.ImageDraw,
    source: str,
    post_date: date,
    x: int,
    y: int,
    *,
    source_color: tuple[int, int, int, int],
    detail_color: tuple[int, int, int, int],
) -> None:
    bold_font = load_roboto_font(size=24, bold=True)
    regular_font = load_roboto_font(size=24, bold=False)
    brand = build_byline(source)
    separator_and_date = f" | {post_date.strftime('%d %b %Y')}"
    draw.text((x, y), brand, font=bold_font, fill=source_color)
    draw.text(
        (x + text_width(draw, brand, bold_font), y),
        separator_and_date,
        font=regular_font,
        fill=detail_color,
    )


def paste_brand_logo(canvas: Image.Image, logo_path: Path | None) -> None:
    if logo_path is None:
        return
    if not logo_path.is_file():
        raise FileNotFoundError(f"Brand logo not found: {logo_path}")
    with Image.open(logo_path) as logo_source:
        logo = logo_source.convert("RGBA")
        alpha_box = logo.getchannel("A").getbbox()
        if alpha_box:
            logo = logo.crop(alpha_box)
        logo.thumbnail((118, 118), Image.Resampling.LANCZOS)
    margin = 46
    x = canvas.width - margin - logo.width
    y = canvas.height - margin - logo.height
    canvas.alpha_composite(logo, (x, y))


def headline_highlight_colors(
    line_index: int,
    highlight_style: str,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    if highlight_style not in HEADLINE_HIGHLIGHT_STYLES:
        raise ValueError(f"Unknown headline highlight style: {highlight_style}")
    if highlight_style == "cyan" and line_index == 0:
        return BRAND_MINT, INK
    if highlight_style == "red" and line_index == 0:
        return BRAND_CORAL, WHITE
    if highlight_style == "dual" and line_index == 0:
        return BRAND_CORAL, WHITE
    if highlight_style == "dual" and line_index == 1:
        return BRAND_MINT, INK
    return None


def draw_brand_block(
    draw: ImageDraw.ImageDraw,
    title: str,
    source: str,
    post_date: date,
    font_override: Path | None,
    highlight_style: str = "dual",
) -> None:
    margin = 62
    max_width = CANVAS_SIZE[0] - margin * 2
    if contains_bangla_text(title):
        bold_path, bold_index = find_bangla_font(bold=True)
        headline_font, lines, line_height = fit_headline(
            draw,
            title,
            bold_path,
            max_width=max_width,
            max_height=395,
            font_index=bold_index,
        )
        latin_font = load_roboto_font(size=headline_font.size, bold=True)
        lines = wrap_mixed_headline(
            draw, title, headline_font, latin_font, max_width
        )
        while len(lines) > 5 and headline_font.size > 42:
            headline_font = load_font(
                bold_path, size=headline_font.size - 2, index=bold_index
            )
            latin_font = load_roboto_font(size=headline_font.size, bold=True)
            lines = wrap_mixed_headline(
                draw, title, headline_font, latin_font, max_width
            )
        line_height = max(
            headline_font.size + 8,
            draw.textbbox((0, 0), "Ag", font=headline_font)[3] + 8,
        )
        italic_font = headline_font
    else:
        latin_font = None
        if font_override:
            bold_path = find_font_variant("bold", font_override)
            italic_path = find_font_variant("bold-italic", font_override)
            bold_variation = None
            italic_variation = None
        else:
            bold_path = str(ROBOTO_REGULAR)
            italic_path = str(ROBOTO_ITALIC)
            bold_variation = "Bold"
            italic_variation = "Bold Italic"
        headline_font, lines, line_height = fit_headline(
            draw,
            title,
            bold_path,
            max_width=max_width,
            max_height=395,
            variation_name=bold_variation,
        )
        italic_font = load_font(
            italic_path,
            size=headline_font.size,
            variation_name=italic_variation,
        )

    y = 58
    for index, line in enumerate(lines):
        font = italic_font if index == len(lines) - 1 else headline_font
        is_bangla = contains_bangla_text(title)
        width = (
            mixed_text_width(draw, line, headline_font, latin_font)
            if is_bangla and latin_font is not None
            else text_width(draw, line, font)
        )
        highlight = headline_highlight_colors(index, highlight_style)
        if highlight:
            background_color, fill = highlight
            draw.rounded_rectangle(
                (margin - 10, y - 3, margin + width + 14, y + line_height - 2),
                radius=5,
                fill=background_color,
            )
        else:
            fill = BRAND_MINT if index == len(lines) - 1 else WHITE
        if is_bangla and latin_font is not None:
            draw_mixed_headline_text(
                draw, (margin, y), line, headline_font, latin_font, fill
            )
        else:
            draw.text(
                (margin, y),
                line,
                font=font,
                fill=fill,
                stroke_width=1,
                stroke_fill=(0, 0, 0, 105),
            )
        y += line_height

    draw_byline(
        draw,
        source,
        post_date,
        margin,
        y + 14,
        source_color=BRAND_CORAL,
        detail_color=BRAND_MINT,
    )


def draw_editorial_italic(
    draw: ImageDraw.ImageDraw,
    title: str,
    source: str,
    post_date: date,
    font_override: Path | None,
) -> None:
    margin = 76
    text_x = margin + 25
    max_width = CANVAS_SIZE[0] - text_x - 58
    bold_path = find_font_variant("serif-bold", font_override)
    italic_path = find_font_variant("serif-bold-italic", font_override)
    headline_font, lines, line_height = fit_headline(
        draw, title, bold_path, max_width=max_width, max_height=420
    )
    italic_font = ImageFont.truetype(italic_path, size=headline_font.size)

    y = 68
    rule_bottom = y + len(lines) * line_height - 8
    draw.rounded_rectangle(
        (margin, y + 4, margin + 10, rule_bottom),
        radius=5,
        fill=BRAND_CORAL,
    )
    for index, line in enumerate(lines):
        is_last = index == len(lines) - 1
        font = italic_font if is_last else headline_font
        if index == 0:
            fill = BRAND_CORAL
        elif is_last:
            fill = BRAND_MINT
        else:
            fill = WHITE
        draw.text(
            (text_x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=1,
            stroke_fill=(0, 0, 0, 115),
        )
        y += line_height

    underline_y = y + 7
    draw.rectangle((text_x, underline_y, text_x + 132, underline_y + 5), fill=BRAND_MINT)
    draw_byline(
        draw,
        source,
        post_date,
        text_x,
        underline_y + 18,
        source_color=WHITE,
        detail_color=BRAND_MINT,
    )


def mixed_line_width(
    draw: ImageDraw.ImageDraw,
    words: list[str],
    primary_font: ImageFont.FreeTypeFont,
    accent_font: ImageFont.FreeTypeFont,
    accent_index: int,
) -> int:
    width = 0
    for index, word in enumerate(words):
        font = accent_font if index == accent_index else primary_font
        width += text_width(draw, word, font)
        if index + 1 < len(words):
            width += text_width(draw, " ", primary_font)
    return width


def draw_split_signal(
    draw: ImageDraw.ImageDraw,
    title: str,
    source: str,
    post_date: date,
    font_override: Path | None,
) -> None:
    margin = 62
    max_width = CANVAS_SIZE[0] - margin * 2
    display_path = find_font_variant("display", font_override)
    accent_path = find_font_variant("bold-italic", font_override)
    headline_font, lines, line_height = fit_headline(
        draw, title, display_path, max_width=max_width, max_height=405
    )

    draw.rectangle((margin, 54, margin + 108, 64), fill=BRAND_CORAL)
    draw.rectangle((margin + 108, 54, margin + 274, 64), fill=BRAND_MINT)
    y = 82
    for line_index, line in enumerate(lines):
        words = line.split()
        numeric_index = next(
            (
                index
                for index, word in enumerate(words)
                if any(char.isdigit() for char in word)
            ),
            None,
        )
        accent_index = numeric_index if numeric_index is not None else len(words) - 1
        size = headline_font.size
        while size >= 40:
            primary_font = ImageFont.truetype(display_path, size=size)
            accent_font = ImageFont.truetype(accent_path, size=size)
            if mixed_line_width(
                draw, words, primary_font, accent_font, accent_index
            ) <= max_width:
                break
            size -= 2
        accent_color = BRAND_CORAL if line_index % 2 == 0 else BRAND_MINT
        x = margin
        for word_index, word in enumerate(words):
            is_accent = word_index == accent_index
            font = accent_font if is_accent else primary_font
            fill = accent_color if is_accent else WHITE
            draw.text(
                (x, y),
                word,
                font=font,
                fill=fill,
                stroke_width=1,
                stroke_fill=(0, 0, 0, 115),
            )
            x += text_width(draw, word, font)
            if word_index + 1 < len(words):
                x += text_width(draw, " ", primary_font)
        y += line_height

    draw_byline(
        draw,
        source,
        post_date,
        margin,
        y + 15,
        source_color=BRAND_MINT,
        detail_color=WHITE,
    )


def compose_post(
    background_bytes: bytes,
    title: str,
    source: str,
    post_date: date,
    credit: str,
    font_override: Path | None = None,
    style: str = "brand-block",
    logo_path: Path | None = DEFAULT_BRAND_LOGO,
    headline_highlight: str = "dual",
    feature_image_path: Path | None = None,
) -> Image.Image:
    with Image.open(io.BytesIO(background_bytes)) as generated:
        background = ImageOps.fit(
            generated.convert("RGB"),
            CANVAS_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    canvas = add_scrim(background)
    if feature_image_path is not None:
        paste_feature_photo(canvas, feature_image_path)
    draw = ImageDraw.Draw(canvas)

    if style not in STYLE_CHOICES:
        raise ValueError(f"Unknown post style: {style}")
    if style == "brand-block":
        draw_brand_block(
            draw,
            title,
            source,
            post_date,
            font_override,
            headline_highlight,
        )
    else:
        renderer = {
            "editorial-italic": draw_editorial_italic,
            "split-signal": draw_split_signal,
        }[style]
        renderer(draw, title, source, post_date, font_override)
    paste_brand_logo(canvas, logo_path)

    return canvas.convert("RGB")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def configure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a 1080x1350 tech-news post with OpenAI and Pillow."
    )
    parser.add_argument("news", help="The source tech-news sentence.")
    parser.add_argument(
        "--headline",
        required=True,
        help=(
            "Approved English headline. For a Bangla-selected tweet, the script "
            "translates this headline before rendering."
        ),
    )
    parser.add_argument(
        "--tweet-json",
        type=Path,
        help="Read the persisted post_language selected by fetch_tweets.py.",
    )
    parser.add_argument("--output", type=Path, default=Path("output/post.png"))
    parser.add_argument(
        "--background-input",
        type=Path,
        help="Reuse a local background and skip the OpenAI image API call.",
    )
    parser.add_argument(
        "--feature-image",
        type=Path,
        help=(
            "Tweet photo to place uncropped in a rounded frame on the main post. "
            "When omitted, the first downloaded photo in --tweet-json is used."
        ),
    )
    parser.add_argument(
        "--no-feature-image",
        action="store_true",
        help="Do not add a tweet photo to the main post.",
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
        "--style",
        choices=STYLE_CHOICES,
        default="brand-block",
        help="Pillow typography and brand-color preset.",
    )
    parser.add_argument(
        "--logo",
        type=Path,
        default=DEFAULT_BRAND_LOGO,
        help="Transparent brand logo placed in the bottom-right corner.",
    )
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
    configure_utf8(sys.stdout)
    configure_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        source_text = normalize_news_text(args.news)
        if not source_text:
            raise ValueError("The news sentence cannot be empty.")
        title = normalize_news_text(args.headline).strip(" \"'“”").rstrip(".")
        if not title:
            raise ValueError("The headline cannot be empty.")
        image_prompt = build_image_prompt(source_text, title)
        english_title = title
        post_language = (
            read_post_language(args.tweet_json) if args.tweet_json else "english"
        )
        headline_highlight = (
            read_headline_highlight(args.tweet_json) if args.tweet_json else "dual"
        )
        feature_image_path = None
        if not args.no_feature_image:
            candidate = args.feature_image
            if candidate is None and args.tweet_json:
                candidate = find_first_tweet_photo(args.tweet_json)
            if candidate is not None:
                if feature_photo_meets_minimum(candidate):
                    feature_image_path = candidate
                else:
                    print(
                        "First tweet photo is below the 640x480 primary-inset "
                        "minimum; keeping it as secondary media only.",
                        file=sys.stderr,
                    )
        if post_language == "bangla":
            require_api_key()
            print("Translating approved headline to Bangla...", file=sys.stderr)
            title = translate_headline_to_bangla(make_client(), english_title)
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
            style=args.style,
            logo_path=args.logo,
            headline_highlight=headline_highlight,
            feature_image_path=feature_image_path,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        post.save(args.output, format="PNG", optimize=True)

        if args.keep_background:
            background_path = args.output.with_name(f"{args.output.stem}-background.png")
            background_path.write_bytes(background_bytes)

        metadata = PostMetadata(
            source_text=source_text,
            title=title,
            english_title=english_title,
            post_language=post_language,
            headline_highlight=headline_highlight,
            image_prompt=image_prompt,
            background_source=background_source,
            image_model=args.image_model,
            image_size=args.image_size,
            image_quality=args.image_quality,
            style=args.style,
            logo_source=str(args.logo.resolve()),
            feature_image_source=(
                str(feature_image_path.resolve()) if feature_image_path else None
            ),
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        metadata_path = args.output.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(asdict(metadata), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(f"Language: {post_language}")
        print(f"Headline highlight: {headline_highlight}")
        print(f"Title: {title}")
        print(f"Post: {args.output.resolve()}")
        print(f"Metadata: {metadata_path.resolve()}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
