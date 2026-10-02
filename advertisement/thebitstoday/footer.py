"""Render the approved compact The Bits Today banner without regenerating it."""

from functools import lru_cache
from pathlib import Path

from PIL import Image


DEFAULT_BANNER = Path(__file__).resolve().parent / "assets" / "banner-follow-the-journey.png"
BANNER_SIZE = (2172, 233)
REFERENCE_WIDTH = 1080


def footer_height(width: int = REFERENCE_WIDTH) -> int:
    if width < 1:
        raise ValueError("Advertisement width must be positive.")
    return max(1, round(width * BANNER_SIZE[1] / BANNER_SIZE[0]))


def footer_top(size: tuple[int, int]) -> int:
    return size[1] - footer_height(size[0])


@lru_cache(maxsize=1)
def _original_banner() -> Image.Image:
    with Image.open(DEFAULT_BANNER) as source:
        if source.size != BANNER_SIZE:
            raise ValueError(f"Advertisement must have dimensions {BANNER_SIZE}.")
        banner = source.convert("RGBA")
    if banner.getchannel("A").getextrema() != (255, 255):
        raise ValueError("Advertisement must be fully opaque.")
    return banner


@lru_cache(maxsize=16)
def _scaled_banner(width: int) -> Image.Image:
    return _original_banner().resize(
        (width, footer_height(width)), Image.Resampling.LANCZOS
    )


def load_banner(width: int = REFERENCE_WIDTH) -> Image.Image:
    """Return an independent, proportionally scaled copy of the approved asset."""
    if width < 320:
        raise ValueError("Advertisement requires a canvas at least 320px wide.")
    return _scaled_banner(width).copy()


def draw_footer(canvas: Image.Image, *, top: int | None = None) -> None:
    banner = load_banner(canvas.width)
    position = footer_top(canvas.size) if top is None else top
    if canvas.height <= banner.height or not 0 <= position <= canvas.height - banner.height:
        raise ValueError("Advertisement does not fit inside the canvas.")
    canvas.paste(banner, (0, position))


def apply_footer(image: Image.Image, *, top: int | None = None) -> Image.Image:
    canvas = image.convert("RGBA")
    draw_footer(canvas, top=top)
    return canvas


def make_footer_layer(size: tuple[int, int], *, top: int | None = None) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_footer(layer, top=top)
    return layer
