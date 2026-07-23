#!/usr/bin/env python3
"""Frame downloaded tweet media without cropping it."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageOps


DEFAULT_BORDER_COLOR = "#212121"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO = PROJECT_ROOT / "bitstodaylogo-trans.png"
DEFAULT_CANVAS_SIZE = (1080, 1350)
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


def read_primary_feature_image(post_metadata_path: Path) -> Path | None:
    """Read the exact tweet photo embedded in the generated primary post."""
    if not post_metadata_path.is_file():
        raise FileNotFoundError(f"Post metadata not found: {post_metadata_path}")
    payload = json.loads(post_metadata_path.read_text(encoding="utf-8"))
    raw_path = payload.get("feature_image_source")
    if raw_path is None:
        return None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(
            "Post metadata feature_image_source must be a non-empty path or null."
        )
    feature_image = Path(raw_path)
    if not feature_image.is_absolute():
        feature_image = post_metadata_path.parent / feature_image
    return feature_image.resolve()


def select_secondary_images(
    input_paths: list[Path],
    primary_feature_image: Path | None,
) -> list[Path]:
    """Preserve source order while excluding any photo used in the primary."""
    if primary_feature_image is None:
        return list(input_paths)
    primary_key = primary_feature_image.resolve()
    selected = [path for path in input_paths if path.resolve() != primary_key]
    if len(selected) == len(input_paths):
        raise ValueError(
            "Primary feature image from post metadata does not match any input "
            f"image: {primary_key}"
        )
    return selected


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
    canvas_size: tuple[int, int] = DEFAULT_CANVAS_SIZE,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Tweet image not found: {input_path}")
    color = parse_color(border_color)

    with Image.open(input_path) as image_source:
        source = ImageOps.exif_transpose(image_source).convert("RGBA")
    source_width, source_height = source.size
    if source_width < 1 or source_height < 1:
        raise ValueError(f"Tweet image has invalid dimensions: {input_path}")
    canvas_width, canvas_height = canvas_size
    if canvas_width < 1 or canvas_height < 1:
        raise ValueError("Canvas width and height must both be positive.")

    if border_width is None:
        border_width = max(24, min(72, round(min(canvas_size) * 0.045)))
    if border_width < 1:
        raise ValueError("Border width must be at least 1 pixel.")
    available_width = canvas_width - border_width * 2
    available_height = canvas_height - border_width * 2
    if available_width < 1 or available_height < 1:
        raise ValueError("Border width leaves no room for the source image.")

    scale = min(
        1.0,
        available_width / source_width,
        available_height / source_height,
    )
    rendered_width = max(1, round(source_width * scale))
    rendered_height = max(1, round(source_height * scale))
    if (rendered_width, rendered_height) != source.size:
        source = source.resize(
            (rendered_width, rendered_height),
            Image.Resampling.LANCZOS,
        )
    source_x = (canvas_width - rendered_width) // 2
    source_y = (canvas_height - rendered_height) // 2

    canvas = Image.new(
        "RGBA",
        canvas_size,
        (*color, 255),
    )
    canvas.alpha_composite(source, (source_x, source_y))

    logo_side = max(72, min(150, round(min(canvas_size) * 0.14)))
    logo = load_logo(logo_path, logo_side)
    logo_inset = max(12, round(border_width * 0.4))
    logo_x = canvas_width - logo.width - logo_inset
    logo_y = canvas_height - logo.height - logo_inset
    canvas.alpha_composite(
        logo,
        (logo_x, logo_y),
    )

    save_image(canvas, output_path)
    return {
        "input": str(input_path.resolve()),
        "output": str(output_path.resolve()),
        "source_size": [source_width, source_height],
        "rendered_size": [rendered_width, rendered_height],
        "source_box": [
            source_x,
            source_y,
            source_x + rendered_width,
            source_y + rendered_height,
        ],
        "output_size": list(canvas.size),
        "aspect_ratio": (
            f"{canvas_width // math.gcd(canvas_width, canvas_height)}:"
            f"{canvas_height // math.gcd(canvas_width, canvas_height)}"
        ),
        "border_color": border_color.upper(),
        "border_width": border_width,
        "logo": str(logo_path.resolve()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Place tweet media uncropped inside a fixed 4:5 #212121 frame with "
            "the Bits Today corner logo."
        )
    )
    parser.add_argument("images", nargs="+", type=Path, help="Tweet image files.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for branded copies; source files are never overwritten.",
    )
    parser.add_argument(
        "--post-metadata",
        type=Path,
        help=(
            "Generated primary-post JSON sidecar. If its feature_image_source "
            "is non-null, it must name an input and that image is excluded from "
            "the secondary set."
        ),
    )
    parser.add_argument("--logo", type=Path, default=DEFAULT_LOGO)
    parser.add_argument("--border-color", default=DEFAULT_BORDER_COLOR)
    parser.add_argument(
        "--border-width",
        type=int,
        help="Minimum frame inset in pixels; defaults to 4.5%% of the canvas width.",
    )
    parser.add_argument("--canvas-width", type=int, default=DEFAULT_CANVAS_SIZE[0])
    parser.add_argument("--canvas-height", type=int, default=DEFAULT_CANVAS_SIZE[1])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        primary_feature_image = (
            read_primary_feature_image(args.post_metadata)
            if args.post_metadata
            else None
        )
        secondary_images = select_secondary_images(
            args.images,
            primary_feature_image,
        )
        results = []
        for input_path in secondary_images:
            suffix = output_suffix(input_path)
            destination = args.output_dir / f"{input_path.stem}-branded{suffix}"
            results.append(
                brand_tweet_image(
                    input_path,
                    destination,
                    logo_path=args.logo,
                    border_color=args.border_color,
                    border_width=args.border_width,
                    canvas_size=(args.canvas_width, args.canvas_height),
                )
            )
        print(
            json.dumps(
                {
                    "excluded_primary_image": (
                        str(primary_feature_image)
                        if primary_feature_image is not None
                        else None
                    ),
                    "images": results,
                },
                indent=2,
            )
        )
        return 0
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
