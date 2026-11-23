#!/usr/bin/env python3
"""Render a Cinema news card: the photo leads, and the headline sits low on a light black fade.

Photos close to the card's shape fill it edge to edge. Any other photo is shown
whole, never upscaled, over one of the approved backdrops in assets/news/backdrops,
picked at random but stably per post, so screenshots and charts are never cut.
As in the news workflow, a post with no photo, or one below the 640x480 minimum,
gets a text-free editorial background from the image model instead; pass
--background-input to reuse one already generated. Cards carry no sponsor footer.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

try:
    from . import generate_post
    from .local_backgrounds import stable_seed
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.news import generate_post
    from tools.news.local_backgrounds import stable_seed


WIDTH, HEIGHT = generate_post.CANVAS_SIZE
# Text shadows are pure black, and so is the card under the picture.
SHADE = (0, 0, 0)
INTER_TIGHT = generate_post.PROJECT_ROOT / "assets" / "fonts" / "InterTight-Variable.ttf"
GEIST_MONO = generate_post.PROJECT_ROOT / "assets" / "fonts" / "GeistMono-Variable.ttf"
BBH_BOGLE = generate_post.PROJECT_ROOT / "assets" / "fonts" / "BBHBogle-Regular.ttf"
# Approved scenes behind an embedded photo that is shown whole; each post gets one of them.
BACKDROP_DIR = generate_post.PROJECT_ROOT / "assets" / "news" / "backdrops"
MARGIN = 60
# Cover only within 5% of the card's 4:5 shape; a 3:4 document is already too far, and a crop could cut its text.
COVER_TOLERANCE = 0.05
HEADLINE_MAX_SIZE, HEADLINE_MIN_SIZE = 92, 58
HEADLINE_MAX_HEIGHT = 360
# Over a generated scene the headline may run larger, since no document needs the room.
BACKGROUND_MAX_SIZE, BACKGROUND_MAX_HEIGHT = 108, 470
# The headline block ends this far above the bottom edge.
BLOCK_BOTTOM = HEIGHT - 128
# Follow For More sits on the masthead line; Swipe For More sits on this one, under the headline block.
MASTHEAD_MIDDLE = 79
FOOTER_HINT_MIDDLE = HEIGHT - 60
MASTHEAD_BOTTOM = 128
PHOTO_GAP = 48


@dataclass(frozen=True)
class Face:
    """A variable font, its axis settings by name, and the line height it sets best at."""
    path: Path
    axes: dict[str, float] = field(default_factory=dict)
    line_height: float = 1.02
    size_scale: float = 1.0


# BBH Bogle: a single-weight, narrow, all-capitals display face, so it needs no axis settings and can run larger.
HEADLINE_FACE = Face(BBH_BOGLE, {}, 1.06, 1.15)


def load_face(face: Face, size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(face.path), size)
    try:
        axes = font.get_variation_axes()
    except OSError:
        # A single-weight font has no axes to set.
        return font
    if axes:
        font.set_variation_by_axes([
            face.axes.get(axis["name"].decode() if isinstance(axis["name"], bytes) else axis["name"], axis["default"])
            for axis in axes
        ])
    return font


def inter(size: int, weight: int) -> ImageFont.FreeTypeFont:
    return load_face(Face(INTER_TIGHT, {"Weight": weight}), size)


# Tested against the earlier prompt on real news stories: this one keeps the subject and the action above the headline.
CINEMA_COMPOSITION = (
    "Composition/framing: 4:5 portrait. The bottom 45 percent of this image will be covered by a headline, so treat the "
    "top 55 percent as the whole photograph. Stage a rich, busy, detailed scene there: the main subject, any people and "
    "their faces and hands, the devices or objects the story is about, and layered environmental context around them. "
    "Centre the main subject about 30 percent from the top edge, fully visible and never cut by the 55 percent line. The "
    "bottom 45 percent is a quiet continuation of the same scene, such as floor, desk, ground or foreground surfaces, with "
    "nothing important in it."
)


def cinema_image_prompt(news_text: str, title: str) -> str:
    """The workflow's editorial prompt with Cinema's composition: the story staged in the top 55 percent, no darkening."""
    prompt = generate_post.build_image_prompt(news_text, title)
    lines = [line for line in prompt.splitlines() if line.startswith("Composition/framing:")]
    if len(lines) != 1:
        raise RuntimeError("The news image prompt changed; update cinema_image_prompt.")
    return prompt.replace(lines[0], CINEMA_COMPOSITION)


@lru_cache(maxsize=None)
def _has_glyph(path: str, character: str) -> bool:
    """Whether the font draws `character` itself rather than its missing-glyph box (Pillow alone; no fontTools)."""
    font = ImageFont.truetype(path, 48)
    missing = font.getmask("\U000F0000")
    mask = font.getmask(character)
    return mask.size != missing.size or bytes(mask) != bytes(missing)


# Plain stand-ins for characters a display font often lacks.
SUBSTITUTES = {"‐": "-", "‑": "-", "‒": "-", "−": "-", "→": "->", "←": "<-",
               "×": "x", "′": "'", "″": '"', " ": " "}


def displayable(text: str, font_path: Path) -> str:
    """`text` with what the font cannot draw removed: accents fall back to their base letter, emoji and the rest drop."""
    path = str(font_path)
    kept = []
    for character in text:
        if character.isspace() or _has_glyph(path, character):
            kept.append(character)
            continue
        substitute = SUBSTITUTES.get(character)
        if substitute and all(part.isspace() or _has_glyph(path, part) for part in substitute):
            kept.append(substitute)
            continue
        base = "".join(part for part in unicodedata.normalize("NFKD", character) if not unicodedata.combining(part))
        if base and all(_has_glyph(path, part) for part in base):
            kept.append(base)
    return " ".join("".join(kept).split())


def tweet_seed(tweet_json: Path | None, fallback: str) -> str:
    """The post's stable seed: its tweet ID, else its URL, else `fallback`; every card of a post shares it."""
    if tweet_json is not None and tweet_json.is_file():
        payload = json.loads(tweet_json.read_text(encoding="utf-8"))
        items = payload.get("items") or []
        if items and isinstance(items[0], dict) and str(items[0].get("id") or "").strip():
            return str(items[0]["id"]).strip()
        urls = [str(url) for url in payload.get("requested_urls") or [] if url]
        if urls:
            return urls[0]
    return fallback


def tweet_photo_count(tweet_json: Path | None) -> int | None:
    """How many photos the tweet and its thread downloaded, or None without a tweet file."""
    if tweet_json is None or not tweet_json.is_file():
        return None
    payload = json.loads(tweet_json.read_text(encoding="utf-8"))
    count = 0
    for item in payload.get("items") or []:
        photos = item.get("downloaded_photos")
        if not isinstance(photos, list):
            photos = [media for media in item.get("downloaded_media") or [] if media.get("kind") == "photo"]
        count += sum(1 for photo in photos if photo.get("local_path"))
    return count


def has_more_cards(tweet_json: Path | None, primary_photo: Path | None) -> bool:
    """Whether the carousel continues past the headline: false only when its one photo is already on the headline card."""
    count = tweet_photo_count(tweet_json)
    return count is None or not (primary_photo is not None and count <= 1)


def resolve_highlight(highlight: str, seed: str) -> str:
    """Cinema boxes one line in a single colour, so a saved "dual" settles on red or cyan, stably per post."""
    if highlight in ("red", "cyan"):
        return highlight
    return ("red", "cyan")[stable_seed("cinema-highlight", seed) % 2]


def highlight_color(highlight: str) -> tuple[int, ...]:
    """The brand colour of the first line's box; the boxed text is black and the date white."""
    return generate_post.BRAND_CORAL if highlight == "red" else generate_post.BRAND_MINT


def balanced_lines(words: list[str], font: ImageFont.FreeTypeFont, max_width: int) -> list[list[str]]:
    """Greedy wrap at the full width, then the narrowest width that keeps the same line count."""
    space = font.getlength(" ")

    def wrap(width: float) -> list[list[str]]:
        lines: list[list[str]] = [[]]
        used = 0.0
        for word in words:
            length = font.getlength(word)
            if lines[-1] and used + space + length > width:
                lines.append([])
                used = 0.0
            used += (space if lines[-1] else 0) + length
            lines[-1].append(word)
        return lines

    target = len(wrap(max_width))
    low, high = max(font.getlength(word) for word in words), float(max_width)
    while high - low > 2:
        middle = (low + high) / 2
        if len(wrap(middle)) <= target:
            high = middle
        else:
            low = middle
    return wrap(high)


# Only the boxed first line gets extra height, so its box can hold descenders.
LEAD_LINE_HEIGHT = 1.26


@dataclass(frozen=True)
class HeadlineLayout:
    font: ImageFont.FreeTypeFont
    lines: list[str]
    lead_height: int
    line_height: int

    @property
    def height(self) -> int:
        return self.lead_height + (len(self.lines) - 1) * self.line_height


def fit_headline(headline: str, max_size: int, max_height: int, face: Face) -> HeadlineLayout:
    """The largest size whose evenly balanced lines, the boxed first one included, fit within `max_height`."""
    words = headline.split()
    for size in range(round(max_size * face.size_scale), HEADLINE_MIN_SIZE - 1, -2):
        font = load_face(face, size)
        lines = [" ".join(line) for line in balanced_lines(words, font, WIDTH - MARGIN * 2)]
        layout = HeadlineLayout(font, lines, round(size * LEAD_LINE_HEIGHT), round(size * face.line_height))
        if layout.height <= max_height and all(font.getlength(line) <= WIDTH - MARGIN * 2 for line in lines):
            return layout
    # The longest headlines keep the minimum size and grow the block upwards.
    return layout


def photo_is_usable(path: Path) -> bool:
    return generate_post.feature_photo_meets_minimum(path)


def pick_backdrop(seed: str, directory: Path = BACKDROP_DIR) -> Path:
    """A random backdrop that stays the same for the same post across revisions and platforms."""
    backdrops = sorted(path for path in directory.iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not backdrops:
        raise FileNotFoundError(f"No backdrops found in {directory}")
    return backdrops[stable_seed("cinema-backdrop", seed) % len(backdrops)]


def vertical_gradient(size: tuple[int, int], stops: list[tuple[float, int]]) -> Image.Image:
    """A black fade whose alpha follows (position, alpha) stops down the height."""
    width, height = size
    column = Image.new("L", (1, height))
    for y in range(height):
        position = y / max(1, height - 1)
        for (start, low), (end, high) in zip(stops, stops[1:]):
            if start <= position <= end:
                column.putpixel((0, y), round(low + (high - low) * (position - start) / max(1e-6, end - start)))
                break
    layer = Image.new("RGBA", size, SHADE + (0,))
    layer.putalpha(column.resize(size))
    return layer


def text_shadow(layer: Image.Image, *, spread: int = 13, blur: int = 22, strength: int = 235) -> Image.Image:
    """Black shadows for everything drawn on `layer`: a wide soft one for contrast and a tight one for the edges."""
    alpha = layer.getchannel("A")
    wide = alpha.filter(ImageFilter.MaxFilter(spread)).filter(ImageFilter.GaussianBlur(blur)).point(lambda value: value * strength // 255)
    tight = alpha.filter(ImageFilter.GaussianBlur(3)).point(lambda value: value * min(200, strength) // 255)
    shadow = Image.new("L", layer.size, 0)
    shadow.paste(wide, (0, 6))
    shadow = ImageChops.lighter(shadow, ImageChops.offset(tight, 0, 3))
    result = Image.new("RGBA", layer.size, SHADE + (0,))
    result.putalpha(shadow)
    return result


def dark_backdrop(source: Image.Image) -> Image.Image:
    """The black-and-white backdrop, fitted to the card and pushed about halfway to black so photos and text stay in front."""
    image = ImageOps.fit(source.convert("RGB"), (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    return Image.blend(image, Image.new("RGB", image.size, SHADE), 0.52)


def rounded(photo: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width - 1, photo.height - 1), radius=radius, fill=255)
    photo = photo.convert("RGBA")
    photo.putalpha(mask)
    return photo


def kinks(rng: random.Random, xs: list[float], amplitude: float) -> list[float]:
    """Offsets for an edge drawn as a few long straight strokes: random corners, straight runs between them."""
    return [rng.uniform(-amplitude, amplitude) for _ in xs]


def marker_highlight(width: int, height: int, color: tuple[int, ...], seed: str) -> Image.Image:
    """A hand-drawn marker stroke about `width` by `height`: straight edges with slight kinks, slanted cut ends,
    streaky ink and a slight tilt."""
    rng = random.Random(stable_seed("cinema-marker", seed))
    scale = 3
    margin = height
    canvas_w, canvas_h = (width + margin * 2) * scale, (height + margin * 2) * scale
    left, right = margin * scale, (margin + width) * scale
    top, bottom = margin * scale, (margin + height) * scale
    # A handful of corners per edge at uneven spacing, joined by straight lines.
    steps = max(3, width // 190)
    xs = sorted({left, right, *(left + (right - left) * (index + rng.uniform(-0.3, 0.3)) / steps for index in range(1, steps))})
    top_edge = kinks(rng, xs, height * scale * 0.045)
    bottom_edge = kinks(rng, xs, height * scale * 0.05)
    # The pen lands a little high and lifts a little low, so the stroke rises slightly across its length.
    drift = rng.uniform(-0.035, 0.015) * height * scale
    upper = [(x, top + offset + drift * (x - left) / (right - left)) for x, offset in zip(xs, top_edge)]
    lower = [(x, bottom + offset + drift * (x - left) / (right - left)) for x, offset in zip(xs, bottom_edge)]
    # Ends: straight cuts, each slanted a little its own way, as a chisel marker leaves them.
    start_slant = rng.uniform(-0.2, 0.2) * height * scale
    end_slant = rng.uniform(-0.25, 0.25) * height * scale
    upper[0] = (upper[0][0] + start_slant, upper[0][1])
    lower[0] = (lower[0][0] - start_slant, lower[0][1])
    upper[-1] = (upper[-1][0] + end_slant, upper[-1][1])
    lower[-1] = (lower[-1][0] - end_slant, lower[-1][1])
    outline = upper + list(reversed(lower))
    mask = Image.new("L", (canvas_w, canvas_h), 0)
    ImageDraw.Draw(mask).polygon(outline, fill=255)
    # A second, shorter pass overlaps part of the first, so the ink is darker where the marker went over twice.
    second = Image.new("L", (canvas_w, canvas_h), 0)
    pass_start = left + (right - left) * rng.uniform(0.05, 0.35)
    pass_end = left + (right - left) * rng.uniform(0.55, 0.95)
    band = [(x, y + rng.uniform(0.18, 0.3) * height * scale) for x, y in upper if pass_start <= x <= pass_end]
    band_low = [(x, y - rng.uniform(0.2, 0.35) * height * scale) for x, y in lower if pass_start <= x <= pass_end]
    if len(band) > 1:
        ImageDraw.Draw(second).polygon(band + list(reversed(band_low)), fill=255)
    second = second.filter(ImageFilter.GaussianBlur(scale * 6))
    # Streaks along the stroke: thin bands of slightly lighter ink that run its whole length.
    streaks = Image.new("L", (8, max(2, height // 5)))
    streaks.putdata([rng.randint(226, 255) for _ in range(streaks.width * streaks.height)])
    streaks = streaks.resize((canvas_w, canvas_h), Image.Resampling.BICUBIC)
    mask = ImageChops.multiply(mask.filter(ImageFilter.GaussianBlur(scale * 0.7)), streaks)
    stroke = Image.new("RGBA", (canvas_w, canvas_h), color[:3] + (0,))
    stroke.putalpha(mask)
    darker = Image.new("RGBA", (canvas_w, canvas_h), tuple(round(channel * 0.86) for channel in color[:3]) + (0,))
    darker.putalpha(ImageChops.multiply(second, mask).point(lambda value: value * 120 // 255))
    stroke.alpha_composite(darker)
    stroke = stroke.rotate(rng.uniform(-1.1, 0.6), resample=Image.Resampling.BICUBIC, center=(left, (top + bottom) / 2))
    return stroke.resize((canvas_w // scale, canvas_h // scale), Image.Resampling.LANCZOS)


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill, tracking: float) -> None:
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=font, fill=fill, anchor="lm")
        x += font.getlength(character) + tracking


def draw_follow_hint(draw: ImageDraw.ImageDraw, accent: tuple[int, ...]) -> None:
    """"Follow For More" and a plus, right-aligned on the masthead line."""
    font = inter(24, 600)
    right, middle = WIDTH - 58, MASTHEAD_MIDDLE
    plus = 24
    draw.text((right - plus - 16, middle), "Follow For More", font=font, fill=generate_post.WHITE, anchor="rm")
    draw.line((right - plus, middle, right, middle), fill=accent, width=4)
    draw.line((right - plus / 2, middle - plus / 2, right - plus / 2, middle + plus / 2), fill=accent, width=4)


def draw_swipe_hint(draw: ImageDraw.ImageDraw, accent: tuple[int, ...]) -> None:
    """"Swipe For More" and an arrow, right-aligned at the foot of the card to match the follow hint above."""
    font = inter(24, 600)
    right, middle = WIDTH - 58, FOOTER_HINT_MIDDLE
    arrow = 34
    draw.text((right - arrow - 14, middle), "Swipe For More", font=font, fill=generate_post.WHITE, anchor="rm")
    draw.line((right - arrow, middle, right, middle), fill=accent, width=4)
    draw.line((right - 11, middle - 10, right, middle, right - 11, middle + 10), fill=accent, width=4, joint="curve")


def place_photo(canvas: Image.Image, source: Image.Image, box: tuple[int, int, int, int], max_scale: float = 1.0) -> list[int]:
    """Show `source` whole, centred in `box` (left, top, width, height), with rounded corners and a two-layer shadow."""
    left, top, slot_width, slot_height = box
    scale = min(max_scale, slot_width / source.width, slot_height / source.height)
    size = (round(source.width * scale), round(source.height * scale))
    photo = rounded(source.resize(size, Image.Resampling.LANCZOS), 14)
    x = left + (slot_width - size[0]) // 2
    y = top + (slot_height - size[1]) // 2
    # A wide soft shadow lifts the photo off the backdrop; a tight one grounds its edges.
    for offset, blur, strength in ((28, 42, 215), (8, 10, 150)):
        shadow = Image.new("L", canvas.size, 0)
        shadow.paste(photo.getchannel("A").point(lambda value, strength=strength: value * strength // 255), (x, y + offset))
        shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow_layer.putalpha(shadow.filter(ImageFilter.GaussianBlur(blur)))
        canvas.alpha_composite(shadow_layer)
    canvas.alpha_composite(photo, (x, y))
    return [x, y, size[0], size[1]]


def draw_marked_line(overlay: Image.Image, text: str, font: ImageFont.FreeTypeFont, top: float, slot_height: int,
                     accent: tuple[int, ...], seed: str) -> None:
    """One line of black text on a hand-cut marker stroke, its full letter height centred in a `slot_height` band."""
    _, glyph_top, _, glyph_bottom = font.getbbox("Hgjpy", anchor="ls")
    glyph_height = glyph_bottom - glyph_top
    baseline = top + (slot_height - glyph_height) / 2 - glyph_top
    pad = max(4, min(12, (slot_height - glyph_height) / 2 - 2))
    width, height = round(font.getlength(text) + 32), round(glyph_height + pad * 2)
    overlay.alpha_composite(marker_highlight(width, height, accent, seed),
                            (round(MARGIN - 16 - height), round(baseline + glyph_top - pad - height)))
    ImageDraw.Draw(overlay).text((MARGIN, baseline), text, font=font, fill=generate_post.INK, anchor="ls")


def draw_headline(overlay: Image.Image, layout: HeadlineLayout, top: float, accent: tuple[int, ...], seed: str) -> float:
    """The headline from `top`: its first line on the marker in black, the rest white; returns the block's bottom."""
    font = layout.font
    _, glyph_top, _, glyph_bottom = font.getbbox("Hgjpy", anchor="ls")
    glyph_height = glyph_bottom - glyph_top
    draw_marked_line(overlay, layout.lines[0], font, top, layout.lead_height, accent, seed)
    draw = ImageDraw.Draw(overlay)
    for row, text in enumerate(layout.lines[1:]):
        line_top = top + layout.lead_height + row * layout.line_height
        draw.text((MARGIN, line_top + (layout.line_height - glyph_height) / 2 - glyph_top), text, font=font,
                  fill=generate_post.WHITE, anchor="ls")
    return top + layout.height


def draw_chrome(canvas: Image.Image, accent: tuple[int, ...], swipe_hint: bool) -> None:
    """The logo, name and Follow For More across the top, and Swipe For More at the foot, with a light shadow."""
    masthead = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(masthead)
    with Image.open(generate_post.DEFAULT_BRAND_LOGO) as logo_source:
        logo = logo_source.convert("RGBA")
        logo = logo.crop(logo.getchannel("A").getbbox())
        logo.thumbnail((58, 58), Image.Resampling.LANCZOS)
    masthead.alpha_composite(logo, (56, 50 + (58 - logo.height) // 2))
    draw.text((56 + 58 + 16, MASTHEAD_MIDDLE), "The Bits Today", font=inter(30, 700), fill=generate_post.WHITE, anchor="lm")
    draw_follow_hint(draw, accent)
    if swipe_hint:
        draw_swipe_hint(draw, accent)
    canvas.alpha_composite(text_shadow(masthead, spread=5, blur=10, strength=120))
    canvas.alpha_composite(masthead)


def draw_date(draw: ImageDraw.ImageDraw, middle: float, label: str) -> None:
    """The white monospaced date on the headline card, on its own: no rule or bar beside it."""
    draw_tracked(draw, (MARGIN, middle), label, load_face(Face(GEIST_MONO, {"Weight": 600}), 24), generate_post.WHITE, 3)


def render(
    headline: str,
    post_date: date,
    *,
    photo_path: Path | None,
    highlight: str = "dual",
    background_path: Path | None = None,
    photo_backdrop: Path | None = None,
    seed: str | None = None,
    swipe_hint: bool = True,
    face: Face = HEADLINE_FACE,
) -> tuple[Image.Image, dict[str, object]]:
    """Lay out the card.

    `background_path` is the generated scene used when the photo is missing or too small.
    `photo_backdrop` sits behind a photo shown whole; by default one is picked from
    BACKDROP_DIR by `seed` (the tweet ID or URL), falling back to the headline.
    `swipe_hint` is off only when the post has no further carousel items.
    """
    headline = generate_post.normalize_news_text(headline)
    highlight = resolve_highlight(highlight, seed or headline)
    accent = highlight_color(highlight)
    usable = photo_path is not None and photo_is_usable(photo_path)
    if not usable and background_path is None:
        raise ValueError("A post without a usable photo needs a generated background.")
    shown = displayable(headline, face.path)
    if not shown:
        raise ValueError("The headline has no characters the headline font can draw.")
    layout = (
        fit_headline(shown, HEADLINE_MAX_SIZE, HEADLINE_MAX_HEIGHT, face) if usable
        else fit_headline(shown, BACKGROUND_MAX_SIZE, BACKGROUND_MAX_HEIGHT, face)
    )
    headline_top = BLOCK_BOTTOM - layout.height
    date_center = headline_top - 40
    block_top = date_center - 16

    source_path = photo_path if usable else background_path
    with Image.open(source_path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")

    card_ratio = WIDTH / HEIGHT
    ratio = source.width / source.height
    mode = "cover" if not usable or abs(math.log(ratio / card_ratio)) < COVER_TOLERANCE else "contain"

    canvas = Image.new("RGBA", (WIDTH, HEIGHT), SHADE + (255,))
    if mode == "cover":
        # People and scenes sit high in most photos, so keep the upper part.
        canvas.paste(ImageOps.fit(source, (WIDTH, HEIGHT), Image.Resampling.LANCZOS, centering=(0.5, 0.3)), (0, 0))
    else:
        photo_backdrop = photo_backdrop or pick_backdrop(seed or headline)
        with Image.open(photo_backdrop) as opened_backdrop:
            canvas.paste(dark_backdrop(ImageOps.exif_transpose(opened_backdrop)), (0, 0))

    # A lighter fade than before keeps the picture visible behind the headline; the text shadows do the rest.
    fade_top = max(0, block_top - 320)
    canvas.alpha_composite(vertical_gradient((WIDTH, HEIGHT - fade_top), [(0, 0), (0.4, 150), (0.75, 190), (1, 190)]), (0, fade_top))
    canvas.alpha_composite(vertical_gradient((WIDTH, 220), [(0, 105), (1, 0)]), (0, 0))

    photo_box = None
    if mode == "contain":
        photo_box = place_photo(canvas, source, (40, MASTHEAD_BOTTOM, WIDTH - 80, max(240, block_top - PHOTO_GAP - MASTHEAD_BOTTOM)))

    draw_chrome(canvas, accent, swipe_hint)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw_date(draw, date_center, post_date.strftime("%d %b %Y").upper())

    # The persisted highlight marks the first line with a hand-cut coral or mint marker stroke, under black text.
    draw_headline(overlay, layout, headline_top, accent, seed or headline)

    canvas.alpha_composite(text_shadow(overlay))
    canvas.alpha_composite(overlay)
    return canvas.convert("RGB"), {
        "style": "cinema",
        "headline": headline,
        "headline_font": layout.font.getname()[0],
        "headline_size": layout.font.size,
        "headline_lines": layout.lines,
        "headline_highlight": highlight,
        "photo_mode": mode if usable else "background",
        "photo": str(photo_path) if photo_path else None,
        "photo_used": usable,
        "background": None if usable else str(source_path),
        "backdrop": str(photo_backdrop) if mode == "contain" else None,
        "photo_box": photo_box,
        "swipe_hint": swipe_hint,
    }


# Carousel cards after the headline: the story's short descriptions, with or without a photo.
DESCRIPTION_TOP = 196
DESCRIPTION_MAX_SIZE, DESCRIPTION_MIN_SIZE = 54, 38
DESCRIPTION_MAX_HEIGHT = 300
# Detail photos may be small screenshots, so they can enlarge a little; the headline card never enlarges its photo.
DETAIL_MAX_SCALE = 1.75
PHOTO_BOTTOM = FOOTER_HINT_MIDDLE - 56


def fit_description(text: str, max_size: int, min_size: int, max_height: int, weight: int,
                    line_height: float) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    words = text.split()
    for size in range(max_size, min_size - 1, -2):
        font = inter(size, weight)
        lines = [" ".join(line) for line in balanced_lines(words, font, WIDTH - MARGIN * 2)]
        step = round(size * line_height)
        if len(lines) * step <= max_height:
            return font, lines, step
    return font, lines, step


def card_backdrop(seed: str, index: int) -> Image.Image:
    """Each card picks its own backdrop from the pool, so a swipe shows a change of scene."""
    with Image.open(pick_backdrop(f"{seed}-card-{index}")) as opened:
        canvas = dark_backdrop(ImageOps.exif_transpose(opened)).convert("RGBA")
    canvas.alpha_composite(vertical_gradient((WIDTH, 260), [(0, 120), (1, 0)]), (0, 0))
    canvas.alpha_composite(vertical_gradient((WIDTH, 260), [(0, 0), (1, 120)]), (0, HEIGHT - 260))
    return canvas


def render_detail(description: str, photo_path: Path, *, index: int, count: int, highlight: str = "dual",
                  seed: str) -> Image.Image:
    """A media card: its short description at the top and the tweet photo whole beneath it, with no page number.

    `index` counts from 2, since the headline card is 1; `count` is the whole carousel, headline included.
    """
    accent = highlight_color(resolve_highlight(highlight, seed))
    description = displayable(generate_post.normalize_news_text(description), INTER_TIGHT)
    font, lines, step = fit_description(description, DESCRIPTION_MAX_SIZE, DESCRIPTION_MIN_SIZE, DESCRIPTION_MAX_HEIGHT, 700, 1.2)
    canvas = card_backdrop(seed, index)
    text_top = DESCRIPTION_TOP - 20
    text_bottom = text_top + len(lines) * step
    with Image.open(photo_path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    place_photo(canvas, source, (40, text_bottom + 44, WIDTH - 80, PHOTO_BOTTOM - text_bottom - 44), DETAIL_MAX_SCALE)
    draw_chrome(canvas, accent, swipe_hint=index < count)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for row, line in enumerate(lines):
        draw.text((MARGIN, text_top + row * step + font.size * 0.92), line, font=font, fill=generate_post.WHITE, anchor="ls")
    canvas.alpha_composite(text_shadow(overlay, spread=9, blur=16, strength=200))
    canvas.alpha_composite(overlay)
    return canvas.convert("RGB")


def render_summary(description: str, *, index: int, count: int, highlight: str = "dual", seed: str) -> Image.Image:
    """The no-media card: one larger summary, its first line on the same hand-cut marker as the headline."""
    accent = highlight_color(resolve_highlight(highlight, seed))
    description = displayable(generate_post.normalize_news_text(description), INTER_TIGHT)
    font, lines, step = fit_description(description, 76, 48, 520, 800, 1.16)
    lead_step = round(font.size * 1.36)
    block_height = lead_step + (len(lines) - 1) * step
    top = (HEIGHT - block_height) // 2 + 30
    canvas = card_backdrop(seed, index)
    draw_chrome(canvas, accent, swipe_hint=index < count)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw_marked_line(overlay, lines[0], font, top, lead_step, accent, f"{seed}-summary")
    _, glyph_top, _, glyph_bottom = font.getbbox("Hgjpy", anchor="ls")
    for row, line in enumerate(lines[1:]):
        line_top = top + lead_step + row * step
        draw.text((MARGIN, line_top + (step - (glyph_bottom - glyph_top)) / 2 - glyph_top), line, font=font,
                  fill=generate_post.WHITE, anchor="ls")
    canvas.alpha_composite(text_shadow(overlay))
    canvas.alpha_composite(overlay)
    return canvas.convert("RGB")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--headline", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--photo", type=Path, help="First downloaded tweet photo, if any.")
    parser.add_argument("--news", help="Source text; the image model uses it when there is no usable photo.")
    parser.add_argument("--background-input", type=Path, help="Reuse an already generated background instead of calling the image model.")
    parser.add_argument("--keep-background", action="store_true", help="Save a generated background beside the card.")
    parser.add_argument("--photo-backdrop", type=Path, help="Override the backdrop picked from assets/news/backdrops.")
    parser.add_argument("--seed", help="Stable value for the backdrop pick, such as the tweet ID; defaults to the headline.")
    parser.add_argument("--no-swipe", action="store_true", help="Omit Swipe For More when the post has no further carousel items.")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today.")
    parser.add_argument("--highlight", choices=("dual", "red", "cyan"), default="dual",
                        help="Saved highlight: red boxes the first line in coral, cyan in mint; dual picks one per post.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        post_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
        if args.photo is not None and not args.photo.is_file():
            raise FileNotFoundError(f"Photo not found: {args.photo}")
        background = args.background_input
        if background is not None and not background.is_file():
            raise FileNotFoundError(f"Background not found: {background}")
        usable = args.photo is not None and photo_is_usable(args.photo)
        if not usable and background is None:
            if not args.news:
                raise ValueError("--news is required to generate a background when there is no usable photo.")
            print("Generating editorial background...", file=sys.stderr)
            image = generate_post.generate_background(
                generate_post.make_client(),
                cinema_image_prompt(generate_post.normalize_news_text(args.news), generate_post.normalize_news_text(args.headline)),
                generate_post.DEFAULT_IMAGE_MODEL,
                generate_post.DEFAULT_IMAGE_SIZE,
                generate_post.DEFAULT_IMAGE_QUALITY,
            )
            background = args.output.with_name(f"{args.output.stem}-background.png")
            background.parent.mkdir(parents=True, exist_ok=True)
            background.write_bytes(image)
        if args.photo_backdrop is not None and not args.photo_backdrop.is_file():
            raise FileNotFoundError(f"Photo backdrop not found: {args.photo_backdrop}")
        card, metadata = render(args.headline, post_date, photo_path=args.photo, highlight=args.highlight,
                                background_path=background, photo_backdrop=args.photo_backdrop, seed=args.seed,
                                swipe_hint=not args.no_swipe)
        if background is not None and background != args.background_input and not args.keep_background:
            background.unlink()
            metadata["background"] = "openai-image-api"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        generate_post.save_png_atomic(card, args.output)
        metadata["output"] = str(args.output)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
