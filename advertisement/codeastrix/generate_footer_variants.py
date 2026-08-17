#!/usr/bin/env python3
"""Render three Codeastrix footer concepts over an existing social post."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO = Path(__file__).resolve().parent / "assets" / "logo-dark.png"
DEFAULT_FONT = PROJECT_ROOT / "assets" / "fonts" / "Roboto-Variable.ttf"
CANVAS_SIZE = (1080, 1350)
FOOTER_HEIGHT = 192
CTA = "Practice LeetCode problems with a live AI interviewer."

INK = (14, 18, 25, 255)
MIDNIGHT = (15, 21, 31, 255)
BLUE = (82, 119, 174, 255)
BLUE_DARK = (42, 69, 109, 255)
BLUE_LIGHT = (150, 178, 216, 255)
CREAM = (243, 239, 230, 255)
WHITE = (250, 250, 248, 255)
MUTED = (197, 207, 220, 255)


def load_font(size: int, *, weight: str = "Regular") -> ImageFont.FreeTypeFont:
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
        font = load_font(size, weight=weight)
        if text_width(draw, text, font) <= max_width:
            return font
    return load_font(minimum_size, weight=weight)


def draw_tracking_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    tracking: int,
) -> None:
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += text_width(draw, character, font) + tracking


def prepare_logo(path: Path, size: int) -> Image.Image:
    with Image.open(path) as source:
        logo = source.convert("RGBA")
    alpha_box = logo.getchannel("A").getbbox()
    if alpha_box:
        logo = logo.crop(alpha_box)
    logo.thumbnail((size, size), Image.Resampling.LANCZOS)
    return logo


def base_canvas(post_path: Path) -> Image.Image:
    with Image.open(post_path) as source:
        post = source.convert("RGBA")
    if post.size != CANVAS_SIZE:
        raise ValueError(
            f"Example post must be {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}, got "
            f"{post.width}x{post.height}."
        )
    return post


def paste_logo(canvas: Image.Image, logo: Image.Image, center: tuple[int, int]) -> None:
    x = center[0] - logo.width // 2
    y = center[1] - logo.height // 2
    canvas.alpha_composite(logo, (x, y))


def render_midnight(post: Image.Image, logo: Image.Image) -> Image.Image:
    """Dark command-bar treatment with a compact live-session CTA."""
    canvas = post.copy()
    draw = ImageDraw.Draw(canvas)
    top = CANVAS_SIZE[1] - FOOTER_HEIGHT
    draw.rectangle((0, top, CANVAS_SIZE[0], CANVAS_SIZE[1]), fill=MIDNIGHT)
    draw.rectangle((0, top, CANVAS_SIZE[0], top + 6), fill=BLUE_LIGHT)
    draw.rectangle((26, top + 24, 154, top + 166), fill=(25, 33, 47, 255))
    paste_logo(canvas, logo, (90, top + 95))

    label_font = load_font(18, weight="Bold")
    headline_font = load_font(32, weight="Bold")
    button_font = load_font(20, weight="Bold")
    draw_tracking_text(
        draw,
        (181, top + 27),
        "CODEASTRIX  •  LIVE PRACTICE",
        label_font,
        BLUE_LIGHT,
        1,
    )
    draw.text((181, top + 66), CTA, font=headline_font, fill=WHITE)
    draw.text(
        (181, top + 115),
        "Think aloud. Get challenged. Improve.",
        font=load_font(22, weight="Regular"),
        fill=MUTED,
    )

    button = (840, top + 111, 1045, top + 163)
    draw.rounded_rectangle(button, radius=26, fill=BLUE)
    button_text = "START A MOCK  >"
    button_x = button[0] + (button[2] - button[0] - text_width(draw, button_text, button_font)) // 2
    draw.text((button_x, top + 126), button_text, font=button_font, fill=WHITE)
    return canvas


def render_editorial(post: Image.Image, logo: Image.Image) -> Image.Image:
    """Minimal black technical grid with a clipped blue signal panel."""
    canvas = post.copy()
    draw = ImageDraw.Draw(canvas)
    footer_height = 158
    top = CANVAS_SIZE[1] - footer_height
    draw.rectangle((0, top, CANVAS_SIZE[0], CANVAS_SIZE[1]), fill=(7, 9, 13, 255))
    grid = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    for dot_y in range(top + 14, CANVAS_SIZE[1], 20):
        for dot_x in range(14, CANVAS_SIZE[0], 20):
            grid_draw.ellipse(
                (dot_x - 1, dot_y - 1, dot_x + 1, dot_y + 1),
                fill=(255, 255, 255, 62),
            )
    canvas.alpha_composite(grid)
    draw = ImageDraw.Draw(canvas)
    draw.line((0, top + 1, CANVAS_SIZE[0], top + 1), fill=BLUE_LIGHT, width=3)
    paste_logo(canvas, logo, (82, top + footer_height // 2))

    statement = "Practice LeetCode problems in a live AI interview — for free."
    headline_font = fit_single_line(
        draw,
        statement,
        max_width=872,
        start_size=34,
        minimum_size=24,
        weight="Bold",
    )
    draw.text((174, top + 36), statement, font=headline_font, fill=WHITE)
    draw.text(
        (174, top + 94),
        "Visit www.codeastrix.com to learn more.",
        font=load_font(23, weight="Regular"),
        fill=BLUE_LIGHT,
    )
    return canvas


def render_signal(post: Image.Image, logo: Image.Image) -> Image.Image:
    """Blue signal-strip treatment with oversized verbal rhythm."""
    canvas = post.copy()
    draw = ImageDraw.Draw(canvas)
    top = CANVAS_SIZE[1] - FOOTER_HEIGHT
    for offset in range(FOOTER_HEIGHT):
        progress = offset / max(1, FOOTER_HEIGHT - 1)
        color = tuple(
            round(start + (end - start) * progress)
            for start, end in zip(BLUE_DARK[:3], BLUE[:3])
        ) + (255,)
        draw.line((0, top + offset, CANVAS_SIZE[0], top + offset), fill=color)
    draw.rectangle((0, top, CANVAS_SIZE[0], top + 5), fill=CREAM)
    draw.ellipse((28, top + 28, 158, top + 158), fill=CREAM)
    paste_logo(canvas, logo, (93, top + 93))

    word_font = load_font(26, weight="Bold")
    draw_tracking_text(draw, (187, top + 27), "CODEASTRIX", word_font, WHITE, 2)
    rhythm_font = load_font(38, weight="Bold")
    draw.text((187, top + 63), "SOLVE. EXPLAIN. IMPROVE.", font=rhythm_font, fill=CREAM)
    draw.text(
        (189, top + 116),
        CTA,
        font=load_font(23, weight="Regular"),
        fill=WHITE,
    )
    draw.rounded_rectangle(
        (882, top + 67, 1046, top + 121),
        radius=8,
        fill=CREAM,
    )
    cta_font = load_font(20, weight="Bold")
    cta_text = "GO LIVE  >"
    cta_x = 882 + (164 - text_width(draw, cta_text, cta_font)) // 2
    draw.text((cta_x, top + 82), cta_text, font=cta_font, fill=BLUE_DARK)
    return canvas


def save_variant(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="PNG", optimize=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="1080x1350 example post")
    parser.add_argument("--logo", type=Path, default=DEFAULT_LOGO)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    post = base_canvas(args.input)
    logo = prepare_logo(args.logo, 108)
    variants = {
        "01-midnight-command.png": render_midnight(post, logo),
        "02-dotted-grid.png": render_editorial(post, logo),
        "03-blue-signal.png": render_signal(post, logo),
    }
    for filename, image in variants.items():
        output = args.output_dir / filename
        save_variant(image, output)
        print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
