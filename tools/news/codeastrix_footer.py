#!/usr/bin/env python3
"""Shared Codeastrix sponsor footer for Bits Today image and reel renders."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO = PROJECT_ROOT / "advertisement" / "codeastrix" / "assets" / "logo-dark.png"
DEFAULT_FONT = PROJECT_ROOT / "assets" / "fonts" / "Roboto-Variable.ttf"
REFERENCE_WIDTH = 1080
REFERENCE_FOOTER_HEIGHT = 158
STATEMENT = "Practice LeetCode problems in a live AI interview — for free."
DETAIL = "Visit www.codeastrix.com to learn more."

BACKGROUND = (7, 9, 13, 255)
WHITE = (250, 250, 248, 255)
BLUE_LIGHT = (150, 178, 216, 255)


def scaled(value: int, width: int, *, minimum: int = 1) -> int:
    return max(minimum, round(value * width / REFERENCE_WIDTH))


def footer_height(width: int = REFERENCE_WIDTH) -> int:
    return scaled(REFERENCE_FOOTER_HEIGHT, width)


def footer_top(size: tuple[int, int]) -> int:
    return size[1] - footer_height(size[0])


@lru_cache(maxsize=64)
def load_font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    if not DEFAULT_FONT.is_file():
        raise FileNotFoundError(f"Codeastrix footer font not found: {DEFAULT_FONT}")
    font = ImageFont.truetype(str(DEFAULT_FONT), size=size)
    try:
        font.set_variation_by_name(weight)
    except (AttributeError, OSError, ValueError):
        pass
    return font


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def fit_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    start_size: int,
    minimum_size: int,
    weight: str,
) -> ImageFont.FreeTypeFont:
    for size in range(start_size, minimum_size - 1, -1):
        font = load_font(size, weight)
        if text_width(draw, text, font) <= max_width:
            return font
    raise ValueError("Codeastrix footer statement cannot fit the available width.")


@lru_cache(maxsize=8)
def load_logo(path: str, maximum_side: int) -> Image.Image:
    logo_path = Path(path)
    if not logo_path.is_file():
        raise FileNotFoundError(f"Codeastrix logo not found: {logo_path}")
    with Image.open(logo_path) as source:
        logo = source.convert("RGBA")
    alpha_box = logo.getchannel("A").getbbox()
    if not alpha_box:
        raise ValueError(f"Codeastrix logo is fully transparent: {logo_path}")
    logo = logo.crop(alpha_box)
    logo.thumbnail((maximum_side, maximum_side), Image.Resampling.LANCZOS)
    return logo


def draw_footer(
    canvas: Image.Image,
    *,
    logo_path: Path = DEFAULT_LOGO,
) -> None:
    """Draw the approved footer in place at the bottom of an RGBA canvas."""
    if canvas.width < 320:
        raise ValueError("Codeastrix footer requires a canvas at least 320px wide.")
    height = footer_height(canvas.width)
    if canvas.height <= height:
        raise ValueError("Canvas is too short for the Codeastrix footer.")
    top = canvas.height - height
    scale_width = canvas.width

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, top, canvas.width, canvas.height), fill=BACKGROUND)

    grid = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    step = scaled(20, scale_width, minimum=8)
    offset = scaled(14, scale_width, minimum=5)
    radius = scaled(1, scale_width)
    for dot_y in range(top + offset, canvas.height, step):
        for dot_x in range(offset, canvas.width, step):
            grid_draw.ellipse(
                (
                    dot_x - radius,
                    dot_y - radius,
                    dot_x + radius,
                    dot_y + radius,
                ),
                fill=(255, 255, 255, 62),
            )
    canvas.alpha_composite(grid)
    draw = ImageDraw.Draw(canvas)
    draw.line(
        (0, top + scaled(1, scale_width), canvas.width, top + scaled(1, scale_width)),
        fill=BLUE_LIGHT,
        width=scaled(3, scale_width),
    )

    logo = load_logo(str(logo_path.resolve()), scaled(108, scale_width)).copy()
    logo_center_x = scaled(82, scale_width)
    logo_x = logo_center_x - logo.width // 2
    logo_y = top + (height - logo.height) // 2
    canvas.alpha_composite(logo, (logo_x, logo_y))

    text_x = scaled(174, scale_width)
    right_margin = scaled(32, scale_width)
    statement_font = fit_single_line(
        draw,
        STATEMENT,
        max_width=canvas.width - text_x - right_margin,
        start_size=scaled(34, scale_width, minimum=10),
        minimum_size=scaled(24, scale_width, minimum=8),
        weight="Bold",
    )
    detail_font = load_font(scaled(23, scale_width, minimum=8), "Regular")
    draw.text(
        (text_x, top + scaled(36, scale_width)),
        STATEMENT,
        font=statement_font,
        fill=WHITE,
    )
    draw.text(
        (text_x, top + scaled(94, scale_width)),
        DETAIL,
        font=detail_font,
        fill=BLUE_LIGHT,
    )


def apply_footer(
    image: Image.Image,
    *,
    logo_path: Path = DEFAULT_LOGO,
) -> Image.Image:
    canvas = image.convert("RGBA")
    draw_footer(canvas, logo_path=logo_path)
    return canvas


def make_footer_layer(
    size: tuple[int, int],
    *,
    logo_path: Path = DEFAULT_LOGO,
) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_footer(layer, logo_path=logo_path)
    return layer
