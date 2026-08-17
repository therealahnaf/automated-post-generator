#!/usr/bin/env python3
"""Render a Today's Tokens for Thought carousel with local backgrounds."""

from __future__ import annotations

import argparse
import json
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

from tools.news import generate_description as news_description
from tools.news import codeastrix_footer
from tools.news import generate_post as news_post
from tools.news import local_backgrounds
from tools.news.post_language import read_platform_language
from tools.thoughts.generate_copy import (
    MAX_PARAGRAPHS,
    MIN_PARAGRAPHS,
    SERIES_TITLE,
    normalize_hook,
    normalize_paragraph,
)


CANVAS_SIZE = news_post.CANVAS_SIZE
DEFAULT_BACKGROUND_DIR = local_backgrounds.DEFAULT_BACKGROUND_DIR
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
    platform: str | None
    post_language: str
    created_at: str


def read_copy_file(path: Path) -> tuple[str, str, list[str], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    copy_language = str(payload.get("copy_language", "english")).lower()
    if copy_language not in {"english", "bangla"}:
        raise ValueError("copy_language must be english or bangla.")
    series_title = news_description.normalize_source_text(
        str(payload.get("series_title", ""))
    )
    if copy_language == "english":
        if series_title != SERIES_TITLE:
            raise ValueError(f"series_title must be exactly: {SERIES_TITLE}")
        hook = normalize_hook(str(payload.get("hook", "")))
    else:
        hook = news_description.normalize_source_text(
            str(payload.get("hook", ""))
        )
        if not series_title or not news_post.contains_bangla_text(series_title):
            raise ValueError("Bangla series_title must contain Bangla text.")
        if not hook or not news_post.contains_bangla_text(hook):
            raise ValueError("Bangla hook must contain Bangla text.")
    raw_paragraphs = payload.get("paragraphs")
    if (
        not isinstance(raw_paragraphs, list)
        or not MIN_PARAGRAPHS <= len(raw_paragraphs) <= MAX_PARAGRAPHS
    ):
        raise ValueError(
            f"Copy file must contain {MIN_PARAGRAPHS}–{MAX_PARAGRAPHS} paragraphs."
        )
    if copy_language == "english":
        paragraphs = [
            normalize_paragraph(str(value)) for value in raw_paragraphs
        ]
    else:
        paragraphs = [
            news_description.normalize_source_text(str(value))
            for value in raw_paragraphs
        ]
        if any(
            not paragraph or not news_post.contains_bangla_text(paragraph)
            for paragraph in paragraphs
        ):
            raise ValueError("Every Bangla paragraph must contain Bangla text.")
    return series_title, hook, paragraphs, copy_language


def list_backgrounds(directory: Path) -> list[Path]:
    return local_backgrounds.list_backgrounds(directory)


def select_backgrounds(
    backgrounds: list[Path],
    count: int,
    seed: int,
) -> list[Path]:
    return local_backgrounds.select_backgrounds(backgrounds, count, seed)


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


def load_text_font(
    text: str,
    *,
    size: int,
    bold: bool,
    italic: bool = False,
) -> ImageFont.FreeTypeFont:
    if news_post.contains_bangla_text(text):
        path, index = news_post.find_bangla_font(bold=bold)
        return news_post.load_font(path, size=size, index=index)
    return news_post.load_roboto_font(
        size=size,
        bold=bold,
        italic=italic,
    )


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
        font = load_text_font(
            text,
            size=size,
            bold=bold,
            italic=italic,
        )
        lines = wrap_text(draw, text, font, max_width)
        line_height = int(size * 1.28)
        if len(lines) <= max_lines:
            return font, lines, line_height
    raise ValueError("Text is too long for the thought card.")


def fit_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    start_size: int,
    minimum_size: int,
) -> ImageFont.FreeTypeFont:
    for size in range(start_size, minimum_size - 1, -2):
        font = load_text_font(text, size=size, bold=True)
        if news_post.text_width(draw, text, font) <= max_width:
            return font
    raise ValueError("Series title is too long for one line.")


def compose_cover(
    background_path: Path,
    hook: str,
    post_date: date,
    total_cards: int,
    *,
    series_title: str = SERIES_TITLE,
) -> Image.Image:
    canvas = prepare_background(background_path)
    draw = ImageDraw.Draw(canvas)
    del post_date, total_cards

    title = (
        series_title
        if news_post.contains_bangla_text(series_title)
        else series_title.upper()
    )
    title_font = fit_single_line(
        draw,
        title,
        max_width=CANVAS_SIZE[0] - MARGIN * 2,
        start_size=40,
        minimum_size=28,
    )
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
    codeastrix_footer.draw_footer(canvas)
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
    codeastrix_footer.draw_footer(canvas)
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
    parser.add_argument("--tweet-json", type=Path)
    parser.add_argument(
        "--platform",
        choices=("facebook", "instagram"),
        help="Validate the copy against that platform's persisted language.",
    )
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
        series_title, hook, paragraphs, copy_language = read_copy_file(
            args.copy_json
        )
        if args.platform:
            if args.tweet_json is None:
                raise ValueError("--tweet-json is required with --platform.")
            platform_language = read_platform_language(
                args.tweet_json,
                args.platform,
            )
            if copy_language != platform_language:
                raise ValueError(
                    f"{args.platform} requires {platform_language} copy, "
                    f"but {args.copy_json} contains {copy_language}."
                )

        backgrounds = list_backgrounds(args.background_dir)
        source_tweet_json = args.tweet_json
        if source_tweet_json is None:
            payload = json.loads(args.copy_json.read_text(encoding="utf-8"))
            stored_source = payload.get("source_tweet_json")
            if isinstance(stored_source, str) and stored_source.strip():
                candidate = Path(stored_source)
                if candidate.is_file():
                    source_tweet_json = candidate
        if args.seed is not None:
            seed = args.seed
        elif source_tweet_json is not None and source_tweet_json.is_file():
            source_text = news_description.read_tweet_text(source_tweet_json)
            seed = local_backgrounds.stable_seed("informative", source_text)
        else:
            seed = secrets.randbits(63)
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
                series_title=series_title,
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
            series_title=series_title,
            hook=hook,
            paragraphs=paragraphs,
            images=[str(path.resolve()) for path in output_paths],
            preview_sheet=str(preview_path.resolve()),
            background_sources=[str(path.resolve()) for path in selected],
            random_seed=seed,
            platform=args.platform,
            post_language=copy_language,
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
