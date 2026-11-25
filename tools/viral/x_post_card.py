#!/usr/bin/env python3
"""Render a faithful X post card: the post as X shows it, on a Cinema backdrop card.

Everything comes from the fetched tweet JSON (tools/news/fetch_tweets.py): avatar, name, verified badge,
handle, text with links in blue, photos or a video frame, a quoted post nested inside, the time, views and
counts. Nothing is rewritten. Long posts stop where X's timeline stops them, with "Show more". Emoji are
Twemoji, as on X, cached under .cache/twemoji.

  python tools/viral/x_post_card.py --tweet-json <tweet.json> --output card.png [--index 2 --count 3]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.news import cinema_post as cinema  # noqa: E402

WIDTH, HEIGHT = cinema.WIDTH, cinema.HEIGHT
CACHE = ROOT / ".cache"
TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{}.png"

# X's dark theme.
PANEL = (0, 0, 0)
BORDER = (47, 51, 54)
TEXT = (231, 233, 234)
MUTED = (113, 118, 123)
LINK = (29, 155, 240)
BADGE = {"individual": (29, 155, 240), "business": (233, 177, 0), "government": (130, 154, 171)}

PANEL_LEFT, PANEL_WIDTH = 40, WIDTH - 80
PAD = 40
INNER = PANEL_WIDTH - PAD * 2
TOP_LIMIT = cinema.MASTHEAD_BOTTOM + 14
BOTTOM_LIMIT = cinema.FOOTER_HINT_MIDDLE - 36
# X's timeline cuts a long post here and shows "Show more".
SHOW_MORE_AT = 280


def font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return cinema.inter(size, weight)


# ------------------------------------------------------------------------------------------------ fetching
def cached_download(url: str, folder: str, suffix: str = ".jpg") -> Path | None:
    target = CACHE / folder / (hashlib.sha1(url.encode()).hexdigest()[:16] + suffix)
    if target.exists():
        return target
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return target


def local_media(item: dict, tweet_dir: Path) -> dict[str, Path]:
    """Source URL -> downloaded file, looking in the job's media folder when the stored path is from another machine."""
    found = {}
    for entry in (item.get("downloaded_media") or []) + (item.get("downloaded_photos") or []):
        path = Path(entry.get("local_path", ""))
        if not path.exists():
            path = tweet_dir / "media" / path.name
        if path.exists() and entry.get("source_url"):
            found[entry["source_url"]] = path
    return found


def open_media(entry: dict, downloaded: dict[str, Path]) -> tuple[Image.Image | None, bool]:
    """A photo, or a video's first frame; the flag says it is a video."""
    is_video = entry.get("type") in ("video", "gif")
    url = entry.get("thumbnail_url") if is_video else entry.get("url")
    path = downloaded.get(entry.get("url", "")) or (cached_download(url, "x-media") if url else None)
    if path is None:
        return None, is_video
    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB"), is_video


def avatar(author: dict) -> Image.Image:
    url = (author.get("avatar_url") or "").replace("_normal.", "_400x400.").replace("_200x200.", "_400x400.")
    path = cached_download(url, "x-avatars") if url else None
    if path is None and author.get("screen_name"):
        # The stored picture is gone when someone changes it; ask FxTwitter (the fetcher's API) for the current one.
        try:
            user = requests.get(f"https://api.fxtwitter.com/{author['screen_name']}", timeout=20).json().get("user") or {}
        except (requests.RequestException, ValueError):
            user = {}
        current = (user.get("avatar_url") or "").replace("_normal.", "_400x400.").replace("_200x200.", "_400x400.")
        path = cached_download(current, "x-avatars") if current else None
    if path is None:
        return Image.new("RGB", (400, 400), (60, 64, 68))
    with Image.open(path) as opened:
        return opened.convert("RGB")


# ------------------------------------------------------------------------------------------------ text
EMOJI_START = re.compile(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿←-⇿〰〽㊗㊙©®‼⁉™ℹ]")
JOINERS = {"‍", "️", "⃣"}


def twemoji_name(sequence: str) -> str:
    points = [f"{ord(character):x}" for character in sequence]
    if "200d" not in points:
        points = [point for point in points if point != "fe0f"]
    return "-".join(points)


@lru_cache(maxsize=256)
def emoji_image(sequence: str) -> Image.Image | None:
    path = cached_download(TWEMOJI.format(twemoji_name(sequence)), "twemoji", ".png")
    if path is None:
        return None
    with Image.open(path) as opened:
        return opened.convert("RGBA")


def split_emoji(word: str) -> list[tuple[str, str]]:
    """A word as ("text", ...) and ("emoji", ...) parts."""
    parts: list[tuple[str, str]] = []
    index = 0
    while index < len(word):
        character = word[index]
        if EMOJI_START.match(character) and not (character in "©®™" and not word[index + 1:index + 2] == "️"):
            sequence = character
            index += 1
            while index < len(word) and (word[index] in JOINERS or 0x1F3FB <= ord(word[index]) <= 0x1F3FF
                                         or (sequence.endswith("‍") and EMOJI_START.match(word[index]))
                                         or 0x1F1E6 <= ord(word[index]) <= 0x1F1FF and len(sequence) == 1
                                         and 0x1F1E6 <= ord(sequence) <= 0x1F1FF):
                sequence += word[index]
                index += 1
            parts.append(("emoji", sequence))
        else:
            if parts and parts[-1][0] == "text":
                parts[-1] = ("text", parts[-1][1] + character)
            else:
                parts.append(("text", character))
            index += 1
    return parts


URL = re.compile(r"https?://\S+")
LINKISH = re.compile(r"^(https?://|[@#$]\w)")


def display_url(url: str) -> str:
    """A link as X shows it: no scheme or www, and a long one cut with an ellipsis."""
    shown = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    return shown if len(shown) <= 30 else shown[:29] + "…"


def x_text(status: dict) -> str:
    """The post's text as X displays it: expanded links shortened for display, a trailing media link dropped."""
    text = status.get("text") or ""
    text = URL.sub(lambda match: display_url(match.group(0)), text)
    return text.strip()


def shorten_for_timeline(text: str, limit: int = SHOW_MORE_AT) -> tuple[str, bool]:
    """Where X's timeline stops a long post: a paragraph break if a good one is near, else a word boundary."""
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    paragraph = head.rfind("\n")
    if paragraph > limit * 0.55:
        return head[:paragraph].rstrip(), True
    return head.rsplit(" ", 1)[0].rstrip(" ,;:") + "…", True


@dataclass
class Word:
    parts: list[tuple[str, str]]
    color: tuple[int, int, int]


def words_of(text: str) -> list[Word | None]:
    """Words in order; None marks a line break."""
    result: list[Word | None] = []
    for row, line in enumerate(text.split("\n")):
        if row:
            result.append(None)
        for raw in line.split(" "):
            if raw:
                result.append(Word(split_emoji(raw), LINK if LINKISH.match(raw) or "." in raw and display_url(raw) == raw
                                   and re.match(r"^[\w-]+\.[a-z]{2,}", raw) else TEXT))
    return result


def word_width(word: Word, text_font: ImageFont.FreeTypeFont) -> float:
    size = text_font.size
    return sum(text_font.getlength(text) if kind == "text" else size * 1.18 for kind, text in word.parts)


def wrap(text: str, text_font: ImageFont.FreeTypeFont, width: int) -> list[list[Word]]:
    lines: list[list[Word]] = [[]]
    used = 0.0
    space = text_font.getlength(" ")
    for word in words_of(text):
        if word is None:
            lines.append([])
            used = 0.0
            continue
        size = word_width(word, text_font)
        if lines[-1] and used + space + size > width:
            lines.append([])
            used = 0.0
        used += (space if lines[-1] else 0) + size
        lines[-1].append(word)
    return lines


def draw_lines(canvas: Image.Image, lines: list[list[Word]], text_font: ImageFont.FreeTypeFont, left: int, top: int,
               step: int, color_override: tuple[int, int, int] | None = None,
               boxes: list | None = None) -> None:
    """Draw wrapped words; `boxes` collects (word text, (left, top, right, bottom)) for every word drawn."""
    draw = ImageDraw.Draw(canvas)
    space = text_font.getlength(" ")
    size = text_font.size
    for row, line in enumerate(lines):
        baseline = top + row * step + size
        x = float(left)
        for word in line:
            start = x
            for kind, text in word.parts:
                if kind == "text":
                    draw.text((x, baseline), text, font=text_font, fill=color_override or word.color, anchor="ls")
                    x += text_font.getlength(text)
                else:
                    glyph = emoji_image(text)
                    edge = round(size * 1.08)
                    if glyph is not None:
                        canvas.alpha_composite(glyph.resize((edge, edge), Image.Resampling.LANCZOS),
                                               (round(x + size * 0.05), round(baseline - size * 0.9)))
                    x += size * 1.18
            if boxes is not None:
                boxes.append(("".join(text for _, text in word.parts), (start, baseline - size * 0.95, x, baseline + size * 0.25)))
            x += space


# ------------------------------------------------------------------------------------------------ marks
def supersampled(size: int, painter) -> Image.Image:
    scale = 4
    big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    painter(ImageDraw.Draw(big), size * scale)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def badge(size: int, kind: str) -> Image.Image:
    color = BADGE.get(kind, BADGE["individual"])

    def paint(draw: ImageDraw.ImageDraw, edge: int) -> None:
        middle, radius = edge / 2, edge * 0.46
        if kind == "business":
            draw.rounded_rectangle((edge * 0.06, edge * 0.06, edge * 0.94, edge * 0.94), radius=edge * 0.2, fill=color)
        else:
            points = []
            for step in range(240):
                angle = 2 * math.pi * step / 240
                r = radius * (0.9 + 0.1 * math.cos(8 * angle))
                points.append((middle + r * math.cos(angle), middle + r * math.sin(angle)))
            draw.polygon(points, fill=color)
        draw.line([(edge * 0.3, edge * 0.52), (edge * 0.44, edge * 0.66), (edge * 0.71, edge * 0.37)],
                  fill=(255, 255, 255), width=round(edge * 0.09), joint="curve")

    return supersampled(size, paint)


def icon(name: str, size: int, color: tuple[int, int, int] = MUTED) -> Image.Image:
    def paint(draw: ImageDraw.ImageDraw, edge: int) -> None:
        line = round(edge * 0.085)
        if name == "reply":
            draw.rounded_rectangle((edge * 0.1, edge * 0.16, edge * 0.9, edge * 0.72), radius=edge * 0.28, outline=color, width=line)
            draw.polygon([(edge * 0.28, edge * 0.66), (edge * 0.28, edge * 0.9), (edge * 0.5, edge * 0.7)], fill=color)
        elif name == "repost":
            draw.line([(edge * 0.2, edge * 0.75), (edge * 0.2, edge * 0.28), (edge * 0.68, edge * 0.28)], fill=color, width=line, joint="curve")
            draw.line([(edge * 0.06, edge * 0.42), (edge * 0.2, edge * 0.26), (edge * 0.34, edge * 0.42)], fill=color, width=line, joint="curve")
            draw.line([(edge * 0.8, edge * 0.25), (edge * 0.8, edge * 0.72), (edge * 0.32, edge * 0.72)], fill=color, width=line, joint="curve")
            draw.line([(edge * 0.66, edge * 0.58), (edge * 0.8, edge * 0.74), (edge * 0.94, edge * 0.58)], fill=color, width=line, joint="curve")
        elif name == "like":
            points = []
            for step in range(200):
                t = 2 * math.pi * step / 200
                x = 16 * math.sin(t) ** 3
                y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
                points.append((edge / 2 + x * edge / 38, edge * 0.47 - y * edge / 38))
            draw.line(points + points[:1], fill=color, width=line, joint="curve")
        elif name == "bookmark":
            draw.line([(edge * 0.25, edge * 0.9), (edge * 0.25, edge * 0.12), (edge * 0.75, edge * 0.12), (edge * 0.75, edge * 0.9),
                       (edge * 0.5, edge * 0.7), (edge * 0.25, edge * 0.9)], fill=color, width=line, joint="curve")
        elif name == "play":
            draw.ellipse((0, 0, edge - 1, edge - 1), fill=(29, 155, 240))
            draw.polygon([(edge * 0.4, edge * 0.3), (edge * 0.4, edge * 0.7), (edge * 0.72, edge * 0.5)], fill=(255, 255, 255))

    return supersampled(size, paint)


def circle(image: Image.Image, size: int) -> Image.Image:
    image = ImageOps.fit(image, (size, size), Image.Resampling.LANCZOS).convert("RGBA")
    mask = supersampled(size, lambda draw, edge: draw.ellipse((0, 0, edge - 1, edge - 1), fill=(255, 255, 255, 255)))
    image.putalpha(mask.getchannel("A"))
    return image


def rounded(image: Image.Image, radius: int, border: bool = True) -> Image.Image:
    image = image.convert("RGBA")
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    image.putalpha(mask)
    if border:
        ImageDraw.Draw(image).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, outline=BORDER, width=2)
    return image


def compact(number: Any) -> str:
    """Counts as X prints them: 617, 11.5K, 1.2M."""
    try:
        value = int(number)
    except (TypeError, ValueError):
        return "0"
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= limit:
            return f"{value / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


def posted_at(status: dict) -> datetime | None:
    stamp = status.get("created_timestamp")
    if stamp:
        return datetime.fromtimestamp(int(stamp), tz=timezone.utc)
    try:
        return datetime.strptime(status.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------ layout
def media_grid(images: list[tuple[Image.Image, bool]], width: int, height: int) -> Image.Image:
    """X's photo grid: one, side by side, one plus two stacked, or two by two; a video shows its frame and a play button."""
    grid = Image.new("RGBA", (width, height), PANEL + (255,))
    gap = 6
    count = min(4, len(images))
    half_w, half_h = (width - gap) // 2, (height - gap) // 2
    boxes = {
        1: [(0, 0, width, height)],
        2: [(0, 0, half_w, height), (half_w + gap, 0, width - half_w - gap, height)],
        3: [(0, 0, half_w, height), (half_w + gap, 0, width - half_w - gap, half_h), (half_w + gap, half_h + gap, width - half_w - gap, height - half_h - gap)],
        4: [(0, 0, half_w, half_h), (half_w + gap, 0, width - half_w - gap, half_h), (0, half_h + gap, half_w, height - half_h - gap),
            (half_w + gap, half_h + gap, width - half_w - gap, height - half_h - gap)],
    }[count]
    for (image, is_video), (x, y, w, h) in zip(images, boxes):
        grid.paste(ImageOps.fit(image, (w, h), Image.Resampling.LANCZOS, centering=(0.5, 0.15)), (x, y))
        if is_video:
            play = icon("play", 96)
            grid.alpha_composite(play, (x + (w - 96) // 2, y + (h - 96) // 2))
    return rounded(grid, 22)


def media_height(images: list[tuple[Image.Image, bool]], width: int, cap: int) -> int:
    if not images:
        return 0
    if len(images) == 1:
        image = images[0][0]
        return min(cap, round(width * image.height / image.width))
    return min(cap, round(width * 0.62))


@dataclass
class Layout:
    text_font: ImageFont.FreeTypeFont
    lines: list[list[Word]]
    show_more: bool
    media_h: int
    quote: dict | None
    quote_lines: list[list[Word]]
    quote_media_h: int
    quote_compact: bool
    height: int


HEADER = 88
# A quoted post inside a post that has its own media shows its media as a thumbnail beside its text, as X does.
THUMB = 150
META_H = 34
STATS_H = 66


def plan(status: dict, images, quote: dict | None, quote_images, budget: int) -> Layout:
    """The largest text and media that fit the panel's height budget."""
    text, cut = shorten_for_timeline(x_text(status))
    quote_text = shorten_for_timeline(x_text(quote), 180)[0] if quote else ""
    layout = None
    # Shrink the text before cropping the media: the picture is the evidence, so it stays whole as long as possible.
    for cap, size in [(620, size) for size in range(36, 27, -2)] + [(cap, 28) for cap in (540, 460, 380, 320, 260)]:
        text_font = font(size)
        lines = wrap(text, text_font, INNER) if text else []
        step = round(size * 1.34)
        if True:
            media_h = media_height(images, INNER, cap)
            quote_font = font(size - 4)
            compact = bool(images and quote_images)
            quote_width = INNER - 48 - (THUMB + 20 if compact else 0)
            quote_lines = wrap(quote_text, quote_font, quote_width)[:4 if compact else 5] if quote else []
            quote_media_h = 0 if compact or not quote else media_height(quote_images, INNER - 48, min(cap // 2, 340))
            quote_body = len(quote_lines) * round((size - 4) * 1.32)
            quote_h = (24 + 44 + 14 + (max(quote_body, THUMB) if compact else quote_body)
                       + (16 + quote_media_h if quote_media_h else 0) + 24) if quote else 0
            height = (PAD + HEADER + 18 + len(lines) * step + (step if cut else 0) + (24 + media_h if media_h else 0)
                      + (24 + quote_h if quote else 0) + 24 + META_H + 18 + STATS_H)
            layout = Layout(text_font, lines, cut, media_h, quote, quote_lines, quote_media_h, compact, height)
            if height <= budget:
                return layout
    return layout


def normalise(word: str) -> str:
    return re.sub(r"[^\w%+-]", "", word.lower())


def find_phrase(boxes: list, phrase: str) -> tuple[float, float, float, float] | None:
    """The box around `phrase` if it appears word for word on one line of the drawn text, else None."""
    wanted = [normalise(word) for word in phrase.split() if normalise(word)]
    if not wanted:
        return None
    for start in range(len(boxes) - len(wanted) + 1):
        run = boxes[start:start + len(wanted)]
        if [normalise(text) for text, _ in run] == wanted and len({round(box[1]) for _, box in run}) == 1:
            return (run[0][1][0], run[0][1][1], run[-1][1][2], run[-1][1][3])
    return None


def render_post_card(status: dict, tweet_dir: Path, *, budget: int = BOTTOM_LIMIT - TOP_LIMIT, anchors: dict | None = None,
                     phrase: str | None = None) -> Image.Image:
    """The post itself, X dark theme, as an RGBA panel PANEL_WIDTH wide.

    `anchors`, when given, receives panel-relative boxes: "media", "quote" and "phrase" (where `phrase` appears in the
    post's own text or the quoted text, word for word on one line).
    """
    anchors = anchors if anchors is not None else {}
    word_boxes: list = []
    downloaded = local_media(status, tweet_dir)
    entries = (status.get("media") or {}).get("all") or []
    images = [pair for pair in (open_media(entry, downloaded) for entry in entries[:4]) if pair[0] is not None]
    quote = status.get("quote") if isinstance(status.get("quote"), dict) else None
    quote_entries = ((quote or {}).get("media") or {}).get("all") or []
    quote_images = [pair for pair in (open_media(entry, downloaded) for entry in quote_entries[:4]) if pair[0] is not None]
    layout = plan(status, images, quote, quote_images, budget)

    panel = Image.new("RGBA", (PANEL_WIDTH, layout.height), PANEL + (255,))
    draw = ImageDraw.Draw(panel)
    author = status.get("author") or {}
    # Header: avatar, name and badge, handle.
    panel.alpha_composite(circle(avatar(author), HEADER), (PAD, PAD))
    name_font, handle_font = font(34, 700), font(29)
    name = cinema.displayable(author.get("name") or author.get("screen_name") or "", cinema.INTER_TIGHT)
    name_x, name_y = PAD + HEADER + 22, PAD + 36
    draw.text((name_x, name_y), name, font=name_font, fill=TEXT, anchor="ls")
    verification = author.get("verification") or {}
    if verification.get("verified"):
        panel.alpha_composite(badge(34, verification.get("type") or "individual"),
                              (round(name_x + name_font.getlength(name) + 8), name_y - 30))
    draw.text((name_x, name_y + 42), "@" + (author.get("screen_name") or ""), font=handle_font, fill=MUTED, anchor="ls")

    y = PAD + HEADER + 18
    size = layout.text_font.size
    step = round(size * 1.34)
    draw_lines(panel, layout.lines, layout.text_font, PAD, y, step, boxes=word_boxes)
    y += len(layout.lines) * step
    if layout.show_more:
        draw.text((PAD, y + size), "Show more", font=layout.text_font, fill=LINK, anchor="ls")
        y += step
    if layout.media_h:
        y += 24
        panel.alpha_composite(media_grid(images, INNER, layout.media_h), (PAD, y))
        anchors["media"] = (PAD, y, PAD + INNER, y + layout.media_h)
        y += layout.media_h
    if quote:
        y += 24
        quote_size = size - 4
        quote_step = round(quote_size * 1.32)
        body = len(layout.quote_lines) * quote_step
        box_h = 24 + 44 + 14 + (max(body, THUMB) if layout.quote_compact else body) + (16 + layout.quote_media_h if layout.quote_media_h else 0) + 24
        box = Image.new("RGBA", (INNER, box_h), PANEL + (255,))
        box_draw = ImageDraw.Draw(box)
        quote_author = quote.get("author") or {}
        box.alpha_composite(circle(avatar(quote_author), 44), (24, 24))
        quote_name = cinema.displayable(quote_author.get("name") or "", cinema.INTER_TIGHT)
        small_bold = font(27, 700)
        box_draw.text((80, 56), quote_name, font=small_bold, fill=TEXT, anchor="ls")
        after = 80 + small_bold.getlength(quote_name) + 6
        if (quote_author.get("verification") or {}).get("verified"):
            box.alpha_composite(badge(26, (quote_author.get("verification") or {}).get("type") or "individual"), (round(after), 33))
            after += 32
        box_draw.text((after + 4, 56), "@" + (quote_author.get("screen_name") or ""), font=font(26), fill=MUTED, anchor="ls")
        text_left = 24
        if layout.quote_compact:
            thumb = media_grid(quote_images[:1], THUMB, THUMB)
            box.alpha_composite(thumb, (24, 24 + 44 + 14))
            text_left = 24 + THUMB + 20
        quote_boxes: list = []
        draw_lines(box, layout.quote_lines, font(quote_size), text_left, 24 + 44 + 14, quote_step, boxes=quote_boxes)
        word_boxes += [(text, (b[0] + PAD, b[1] + y, b[2] + PAD, b[3] + y)) for text, b in quote_boxes]
        if layout.quote_media_h:
            box.alpha_composite(media_grid(quote_images, INNER - 48, layout.quote_media_h),
                                (24, 24 + 44 + 14 + len(layout.quote_lines) * quote_step + 16))
        panel.alpha_composite(rounded(box, 22), (PAD, y))
        anchors["quote"] = (PAD, y, PAD + INNER, y + box_h)
        y += box_h
    # Time, date and views, then the counts.
    y += 24
    when = posted_at(status)
    meta_font, bold = font(27), font(27, 700)
    x = PAD
    if when:
        stamp = when.strftime("%I:%M %p · %b %d, %Y").lstrip("0").replace(" 0", " ")
        draw.text((x, y + 27), stamp, font=meta_font, fill=MUTED, anchor="ls")
        x += meta_font.getlength(stamp)
    if status.get("views"):
        draw.text((x, y + 27), " · ", font=meta_font, fill=MUTED, anchor="ls")
        x += meta_font.getlength(" · ")
        views = compact(status["views"])
        draw.text((x, y + 27), views, font=bold, fill=TEXT, anchor="ls")
        draw.text((x + bold.getlength(views), y + 27), " Views", font=meta_font, fill=MUTED, anchor="ls")
    y += META_H + 18
    draw.line((PAD, y, PANEL_WIDTH - PAD, y), fill=BORDER, width=2)
    counts = [("reply", status.get("replies")), ("repost", status.get("reposts")), ("like", status.get("likes")),
              ("bookmark", status.get("bookmarks"))]
    slot = INNER / len(counts)
    for index, (name_, value) in enumerate(counts):
        left = PAD + index * slot
        panel.alpha_composite(icon(name_, 34), (round(left), y + 18))
        draw.text((left + 46, y + 46), compact(value), font=font(27), fill=MUTED, anchor="ls")
    if phrase:
        found = find_phrase(word_boxes, phrase)
        if found:
            anchors["phrase"] = found
    return rounded(panel, 30)


def render(tweet_json: Path, *, index: int = 2, count: int = 3, highlight: str = "dual", seed: str | None = None) -> Image.Image:
    """The post card on a Cinema backdrop with the masthead; `index` counts from 2, the cover being 1."""
    tweet = json.loads(tweet_json.read_text(encoding="utf-8-sig"))
    status = (tweet.get("items") or [{}])[0]
    seed = seed or str(status.get("id") or tweet_json)
    accent = cinema.highlight_color(cinema.resolve_highlight(highlight, seed))
    canvas = cinema.card_backdrop(seed, index)
    panel = render_post_card(status, tweet_json.parent)
    top = TOP_LIMIT + max(0, (BOTTOM_LIMIT - TOP_LIMIT - panel.height) // 2)
    for offset, blur, strength in ((28, 42, 215), (8, 10, 150)):
        shadow = Image.new("L", canvas.size, 0)
        shadow.paste(panel.getchannel("A").point(lambda value, strength=strength: value * strength // 255), (PANEL_LEFT, top + offset))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.putalpha(shadow.filter(ImageFilter.GaussianBlur(blur)))
        canvas.alpha_composite(layer)
    canvas.alpha_composite(panel, (PANEL_LEFT, top))
    cinema.draw_chrome(canvas, accent, swipe_hint=index < count)
    return canvas.convert("RGB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index", type=int, default=2)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--highlight", default="dual")
    args = parser.parse_args(argv)
    image = render(args.tweet_json, index=args.index, count=args.count, highlight=args.highlight)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
