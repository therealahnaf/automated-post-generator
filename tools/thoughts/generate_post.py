#!/usr/bin/env python3
"""Render a Today's Tokens for Thought carousel with local backgrounds."""

from __future__ import annotations

import argparse
import json
import random
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from PIL import Image, ImageDraw, ImageFont, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.news import generate_post as news_post
from tools.thoughts.generate_copy import (
    MAX_PARAGRAPHS,
    MIN_PARAGRAPHS,
    SERIES_TITLE,
    normalize_hook,
    normalize_paragraph,
)


CANVAS_SIZE = news_post.CANVAS_SIZE
DEFAULT_BACKGROUND_DIR = PROJECT_ROOT / "assets" / "fonts" / "images"
DEFAULT_LOGO = news_post.DEFAULT_BRAND_LOGO
MARGIN = 72
BODY_LEFT = 92
BODY_WIDTH = 896


@dataclass(frozen=True)
class ThoughtPostMetadata:
    series_title: str
    hook: str
    paragraphs: list[str]
    images: list[str]
    preview_sheet: str
    background_sources: list[str]
    random_seed: int
    logo_source: str
    created_at: str


def read_copy_file(path: Path) -> tuple[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("series_title") != SERIES_TITLE:
        raise ValueError(f"series_title must be exactly: {SERIES_TITLE}")
    hook = normalize_hook(str(payload.get("hook", "")))
    raw_paragraphs = payload.get("paragraphs")
    if (
        not isinstance(raw_paragraphs, list)
        or not MIN_PARAGRAPHS <= len(raw_paragraphs) <= MAX_PARAGRAPHS
    ):
        raise ValueError(
            f"Copy file must contain {MIN_PARAGRAPHS}–{MAX_PARAGRAPHS} paragraphs."
        )
    paragraphs = [normalize_paragraph(str(value)) for value in raw_paragraphs]
    return hook, paragraphs


def list_backgrounds(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Background directory not found: {directory}")
    backgrounds = sorted(
        path
        for path in directory.glob("bg-*.png")
        if path.is_file()
    )
    if not backgrounds:
        raise FileNotFoundError(f"No bg-*.png images found in {directory}")
    return backgrounds


def select_backgrounds(
    backgrounds: list[Path],
    count: int,
    seed: int,
) -> list[Path]:
    if not backgrounds:
        raise ValueError("At least one background is required.")
    rng = random.Random(seed)
    selected: list[Path] = []
    for _ in range(count):
        choices = backgrounds
        if len(backgrounds) > 1 and selected:
            choices = [path for path in backgrounds if path != selected[-1]]
        selected.append(rng.choice(choices))
    return selected


def prepare_background(path: Path) -> Image.Image:
    with Image.open(path) as source:
        background = ImageOps.fit(
            source.convert("RGBA"),
            CANVAS_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    veil = Image.new("RGBA", CANVAS_SIZE, (3, 5, 6, 92))
    background.alpha_composite(veil)
    return background


def wrap_text(
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
        if news_post.text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    max_lines: int,
    start_size: int,
    minimum_size: int,
    bold: bool,
    italic: bool = False,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in range(start_size, minimum_size - 1, -2):
        font = news_post.load_roboto_font(
            size=size,
            bold=bold,
            italic=italic,
        )
        lines = wrap_text(draw, text, font, max_width)
        line_height = int(size * 1.28)
        if len(lines) <= max_lines:
            return font, lines, line_height
    raise ValueError("Text is too long for the thought card.")


def compose_cover(
    background_path: Path,
    hook: str,
    post_date: date,
    total_cards: int,
) -> Image.Image:
    canvas = prepare_background(background_path)
    draw = ImageDraw.Draw(canvas)
    del post_date, total_cards

    title_font = news_post.load_roboto_font(size=40, bold=True)
    title = SERIES_TITLE.upper()
    title_width = news_post.text_width(draw, title, title_font)
    draw.text(
        ((CANVAS_SIZE[0] - title_width) // 2, 408),
        title,
        font=title_font,
        fill=news_post.BRAND_CORAL,
    )

    hook_font, hook_lines, line_height = fit_text(
        draw,
        hook,
        max_width=CANVAS_SIZE[0] - MARGIN * 2 - 50,
        max_lines=4,
        start_size=66,
        minimum_size=44,
        bold=True,
        italic=True,
    )
    text_height = len(hook_lines) * line_height
    y = 730 - text_height // 2
    for index, line in enumerate(hook_lines):
        line_width = news_post.text_width(draw, line, hook_font)
        draw.text(
            ((CANVAS_SIZE[0] - line_width) // 2, y),
            line,
            font=hook_font,
            fill=(
                news_post.BRAND_MINT
                if index == len(hook_lines) - 1
                else news_post.WHITE
            ),
        )
        y += line_height
    return canvas.convert("RGB")


def compose_thought_card(
    background_path: Path,
    paragraph: str,
    post_date: date,
    *,
    position: int,
    paragraph_count: int,
) -> Image.Image:
    canvas = prepare_background(background_path)
    draw = ImageDraw.Draw(canvas)
    del post_date, position, paragraph_count

    quote_font = news_post.load_roboto_font(size=142, bold=True, italic=True)
    draw.text(
        (BODY_LEFT - 10, 270),
        "“",
        font=quote_font,
        fill=news_post.BRAND_CORAL,
    )

    body_font, lines, line_height = fit_text(
        draw,
        paragraph,
        max_width=BODY_WIDTH,
        max_lines=7,
        start_size=62,
        minimum_size=40,
        bold=False,
    )
    text_height = len(lines) * line_height
    y = 680 - text_height // 2
    for index, line in enumerate(lines):
        draw.text(
            (BODY_LEFT, y),
            line,
            font=body_font,
            fill=(
                news_post.BRAND_MINT
                if index == 0
                else news_post.WHITE
            ),
        )
        y += line_height

    close_font = news_post.load_roboto_font(size=60, bold=True, italic=True)
    draw.text(
        (CANVAS_SIZE[0] - BODY_LEFT - 48, 1015),
        "”",
        font=close_font,
        fill=news_post.BRAND_MINT,
    )
    return canvas.convert("RGB")


def make_preview_sheet(images: list[Path], output: Path) -> None:
    thumb_size = (324, 405)
    columns = 2
    gap = 24
    padding = 30
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (
            padding * 2 + columns * thumb_size[0] + (columns - 1) * gap,
            padding * 2 + rows * thumb_size[1] + (rows - 1) * gap,
        ),
        (20, 20, 20),
    )
    for index, path in enumerate(images):
        with Image.open(path) as source:
            thumb = ImageOps.fit(
                source.convert("RGB"),
                thumb_size,
                method=Image.Resampling.LANCZOS,
            )
        x = padding + (index % columns) * (thumb_size[0] + gap)
        y = padding + (index // columns) * (thumb_size[1] + gap)
        sheet.paste(thumb, (x, y))
    news_post.save_png_atomic(sheet, output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--background-dir",
        type=Path,
        default=DEFAULT_BACKGROUND_DIR,
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument("--date", type=news_post.parse_date, default=date.today())
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
        hook, paragraphs = read_copy_file(args.copy_json)
        backgrounds = list_backgrounds(args.background_dir)
        seed = args.seed if args.seed is not None else secrets.randbits(63)
        selected = select_backgrounds(
            backgrounds,
            len(paragraphs) + 1,
            seed,
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output_paths: list[Path] = []

        cover_path = args.output_dir / "01-cover.png"
        news_post.save_png_atomic(
            compose_cover(
                selected[0],
                hook,
                args.date,
                len(paragraphs) + 1,
            ),
            cover_path,
        )
        output_paths.append(cover_path)

        for index, (paragraph, background) in enumerate(
            zip(paragraphs, selected[1:]),
            start=1,
        ):
            path = args.output_dir / f"{index + 1:02d}-thought-{index}.png"
            news_post.save_png_atomic(
                compose_thought_card(
                    background,
                    paragraph,
                    args.date,
                    position=index,
                    paragraph_count=len(paragraphs),
                ),
                path,
            )
            output_paths.append(path)

        preview_path = args.output_dir / "preview-contact-sheet.png"
        make_preview_sheet(output_paths, preview_path)
        metadata = ThoughtPostMetadata(
            series_title=SERIES_TITLE,
            hook=hook,
            paragraphs=paragraphs,
            images=[str(path.resolve()) for path in output_paths],
            preview_sheet=str(preview_path.resolve()),
            background_sources=[str(path.resolve()) for path in selected],
            random_seed=seed,
            logo_source=str(DEFAULT_LOGO.resolve()),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        metadata_path = args.output_dir / "post.json"
        metadata_path.write_text(
            json.dumps(asdict(metadata), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for path in output_paths:
            print(path.resolve())
        print(preview_path.resolve())
        print(metadata_path.resolve())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
