#!/usr/bin/env python3
"""Add the Bits Today border and logo treatment to downloaded tweet photos."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageOps


DEFAULT_BORDER_COLOR = "#212121"
DEFAULT_LOGO = Path(__file__).with_name("bitstodaylogo-trans.png")
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def parse_color(value: str) -> tuple[int, int, int]:
    try:
        color = ImageColor.getrgb(value)
    except ValueError as exc:
        raise ValueError(f"Invalid border color: {value}") from exc
    if len(color) != 3:
        raise ValueError("Border color must be an opaque RGB color.")
    return color


def load_logo(logo_path: Path, maximum_side: int) -> Image.Image:
    if not logo_path.is_file():
        raise FileNotFoundError(f"Brand logo not found: {logo_path}")
    with Image.open(logo_path) as logo_source:
        logo = logo_source.convert("RGBA")
        alpha_box = logo.getchannel("A").getbbox()
        if not alpha_box:
            raise ValueError(f"Brand logo is fully transparent: {logo_path}")
        logo = logo.crop(alpha_box)
        logo.thumbnail((maximum_side, maximum_side), Image.Resampling.LANCZOS)
    return logo


def output_suffix(input_path: Path) -> str:
    suffix = input_path.suffix.lower()
    if suffix == ".jpeg":
        return ".jpg"
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported image type: {input_path.suffix or 'none'}")
    return suffix


def save_image(image: Image.Image, destination: Path) -> None:
    suffix = destination.suffix.lower()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if suffix in {".jpg", ".jpeg"}:
        image.convert("RGB").save(
            destination,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=True,
        )
    elif suffix == ".png":
        image.save(destination, format="PNG", optimize=True)
    elif suffix == ".webp":
        image.save(destination, format="WEBP", quality=95, method=6)
    else:
        raise ValueError(f"Unsupported output image type: {destination.suffix}")


def brand_tweet_image(
    input_path: Path,
    output_path: Path,
    *,
    logo_path: Path = DEFAULT_LOGO,
    border_color: str = DEFAULT_BORDER_COLOR,
    border_width: int | None = None,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Tweet image not found: {input_path}")
    color = parse_color(border_color)

    with Image.open(input_path) as image_source:
        source = ImageOps.exif_transpose(image_source).convert("RGBA")
    width, height = source.size
    if width < 1 or height < 1:
        raise ValueError(f"Tweet image has invalid dimensions: {input_path}")

    if border_width is None:
        border_width = max(24, min(72, round(min(width, height) * 0.045)))
    if border_width < 1:
        raise ValueError("Border width must be at least 1 pixel.")

    canvas = Image.new(
        "RGBA",
        (width + border_width * 2, height + border_width * 2),
        (*color, 255),
    )
    canvas.alpha_composite(source, (border_width, border_width))

    logo_side = max(72, min(150, round(min(width, height) * 0.14)))
    logo = load_logo(logo_path, logo_side)
    logo_inset = max(12, round(border_width * 0.4))
    logo_x = border_width + width - logo.width - logo_inset
    logo_y = border_width + height - logo.height - logo_inset
    canvas.alpha_composite(
        logo,
        (logo_x, logo_y),
    )

    save_image(canvas, output_path)
    return {
        "input": str(input_path.resolve()),
        "output": str(output_path.resolve()),
        "source_size": [width, height],
        "output_size": list(canvas.size),
        "border_color": border_color.upper(),
        "border_width": border_width,
        "logo": str(logo_path.resolve()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add a #212121 border and Bits Today corner logo to tweet images."
    )
    parser.add_argument("images", nargs="+", type=Path, help="Tweet image files.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for branded copies; source files are never overwritten.",
    )
    parser.add_argument("--logo", type=Path, default=DEFAULT_LOGO)
    parser.add_argument("--border-color", default=DEFAULT_BORDER_COLOR)
    parser.add_argument(
        "--border-width",
        type=int,
        help="Border width in pixels; defaults to 4.5%% of the shorter side.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        results = []
        for input_path in args.images:
            suffix = output_suffix(input_path)
            destination = args.output_dir / f"{input_path.stem}-branded{suffix}"
            results.append(
                brand_tweet_image(
                    input_path,
                    destination,
                    logo_path=args.logo,
                    border_color=args.border_color,
                    border_width=args.border_width,
                )
            )
        print(json.dumps({"images": results}, indent=2))
        return 0
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
