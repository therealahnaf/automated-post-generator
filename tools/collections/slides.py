"""Slides for a collection post: which slides a type makes, and rendering each one.

Types (picked by the user, never by a model):
  media   each post's photos and videos, shown whole with an @handle credit; a post without media shows as a
          post card so nothing in the collection is dropped silently
  tweets  each post as a faithful X post card; a post whose media is one video plays it inside the card
  combo   per post: media when the media carries it (it has media and at most COMBO_TEXT characters of text),
          otherwise the post card
The first slide is always the cover: the title under a fanned stack of the first posts' cards (cover.py). Photos are JPEG, videos
are H.264/AAC MP4 at most VIDEO_SECONDS long, all 1080x1350, so Instagram takes them as carousel items.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from tools.collections.store import Item
from tools.news import cinema_post as cinema
from tools.viral import x_post_card as card

WIDTH, HEIGHT = cinema.WIDTH, cinema.HEIGHT
TYPES = ("media", "tweets", "combo")
TYPE_LABELS = {"media": "Media", "tweets": "Tweets", "combo": "Media + Tweets"}
MAX_SLIDES = 10  # Instagram's carousel limit for our publisher, cover included
COMBO_TEXT = 100
VIDEO_SECONDS = 60


@dataclass
class Slide:
    kind: str  # "photo", "video", "card" or "card_video"
    item: Item
    media: Path | None = None
    extra: dict = field(default_factory=dict)


def handle(item: Item) -> str:
    return (item.status().get("author") or {}).get("screen_name", "")


def photos(item: Item) -> list[Path]:
    status = item.status()
    found = card.local_media(status, item.folder)
    ordered = [found[entry["url"]] for entry in ((status.get("media") or {}).get("all") or [])
               if entry.get("type") == "photo" and entry.get("url") in found]
    return ordered or sorted((item.folder / "media").glob("*-photo-*"))


def main_videos(item: Item) -> list[Path]:
    return [path for path in item.videos() if not path.name.startswith("quote-")]


def media_slides(item: Item) -> list[Slide]:
    status = item.status()
    entries = (status.get("media") or {}).get("all") or []
    shown_photos, shown_videos = iter(photos(item)), iter(main_videos(item))
    slides = []
    for entry in entries:
        if entry.get("type") == "photo":
            path = next(shown_photos, None)
            if path:
                slides.append(Slide("photo", item, path))
        elif entry.get("type") in ("video", "gif", "animated_gif"):
            path = next(shown_videos, None)
            if path:
                slides.append(Slide("video", item, path))
    return slides


def post_slide(item: Item) -> Slide:
    status = item.status()
    entries = (status.get("media") or {}).get("all") or []
    videos = main_videos(item)
    if len(entries) == 1 and entries[0].get("type") in ("video", "gif", "animated_gif") and videos:
        return Slide("card_video", item, videos[0])
    return Slide("card", item)


def plan(items: list[Item], kind: str) -> tuple[list[Slide], list[int]]:
    """The slides after the cover (at most MAX_SLIDES - 1) and the numbers of items that did not fit. The cover's
    card stack only previews the first posts, so every post still gets its own full slide."""
    if kind not in TYPES:
        raise ValueError(f"Unknown type {kind}; use one of {', '.join(TYPES)}.")
    wanted: list[Slide] = []
    for item in items:
        if kind == "tweets":
            wanted.append(post_slide(item))
            continue
        media = media_slides(item)
        if kind == "combo" and media and len(card.x_text(item.status())) > COMBO_TEXT:
            media = []
        wanted.extend(media or [post_slide(item)])
    slides = wanted[:MAX_SLIDES - 1]
    shown = {slide.item.id for slide in slides}
    return slides, [item.position for item in items if item.id not in shown]


# ------------------------------------------------------------------------------------------------ video helpers
def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                             "-of", "json", str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Could not read the video {path.name}.")
    payload = json.loads(result.stdout)
    video = next(stream for stream in payload["streams"] if stream.get("codec_type") == "video")
    return {"width": int(video["width"]), "height": int(video["height"]),
            "duration": float(payload.get("format", {}).get("duration") or 0),
            "audio": any(stream.get("codec_type") == "audio" for stream in payload["streams"])}


def frame(video: Path, target: Path, at: float = 1.0) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(target)],
                   check=True)
    if not target.is_file():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(target)], check=True)
    return target


def rounded_mask(size: tuple[int, int], radius: int, target: Path) -> Path:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    mask.save(target)
    return target


def video_into(background: Path, video: Path, box: tuple[int, int, int, int], target: Path, *, cover: bool,
               radius: int = 14) -> Path:
    """Play `video` inside `box` (left, top, right, bottom) of a still background; `cover` crops to fill the box,
    otherwise the box is already the video's shape."""
    info = probe(video)
    left, top, right, bottom = box
    width, height = (right - left) // 2 * 2, (bottom - top) // 2 * 2
    mask = rounded_mask((width, height), radius, target.with_suffix(".mask.png"))
    fit = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}" if cover
           else f"scale={width}:{height}")
    seconds = min(VIDEO_SECONDS, info["duration"] or VIDEO_SECONDS)
    command = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(background), "-i", str(video),
               "-loop", "1", "-i", str(mask), "-filter_complex",
               f"[1:v]{fit},setsar=1,fps=30,format=rgba[v];[2:v]format=gray[m];[v][m]alphamerge[vm];"
               f"[0:v][vm]overlay={left}:{top},format=yuv420p[out]",
               "-map", "[out]"]
    command += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "128k"] if info["audio"] else []
    command += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30", "-t", f"{seconds:.2f}",
                "-movflags", "+faststart", str(target)]
    subprocess.run(command, check=True)
    mask.unlink(missing_ok=True)
    return target


# ------------------------------------------------------------------------------------------------ rendering
def credit(item: Item) -> str:
    return f"Via @{handle(item)}"


def render_cover(title: str, post_date: date, kind: str, items: list[Item], planned: list[Slide], work: Path,
                 seed: str) -> Image.Image:
    """The stacked-card cover; see tools/collections/cover.py."""
    from tools.collections import cover

    return cover.render(title, post_date, kind, items, planned, work, seed)


def render_slide(slide: Slide, index: int, count: int, work: Path, seed: str) -> Path:
    """Render one slide to `work`; returns the JPEG or MP4."""
    stem = work / f"{index:02d}"
    if slide.kind == "photo":
        image = cinema.render_photo(slide.media, index=index, count=count, seed=seed, credit=credit(slide.item))
        image.save(stem.with_suffix(".jpg"), quality=92)
        return stem.with_suffix(".jpg")
    if slide.kind == "card":
        card.render(slide.item.tweet_json, index=index, count=count, seed=seed).save(stem.with_suffix(".jpg"), quality=92)
        return stem.with_suffix(".jpg")
    if slide.kind == "card_video":
        anchors: dict = {}
        still = card.render(slide.item.tweet_json, index=index, count=count, seed=seed, anchors=anchors, play_icons=False)
        still.save(stem.with_suffix(".png"))
        box = tuple(round(value) for value in anchors["media"])
        return video_into(stem.with_suffix(".png"), slide.media, box, stem.with_suffix(".mp4"), cover=True, radius=22)
    # A video shown whole: the same layout as a photo slide, with the video playing where the photo would sit.
    info = probe(slide.media)
    still_frame = frame(slide.media, stem.with_suffix(".frame.jpg"))
    canvas = cinema.card_backdrop(seed, index)
    top = cinema.MASTHEAD_BOTTOM + 30
    with Image.open(still_frame) as opened:
        placeholder = Image.new("RGB", (info["width"], info["height"]), (0, 0, 0))
        box = cinema.place_photo(canvas, placeholder, (40, top, WIDTH - 80, cinema.PHOTO_BOTTOM - top), cinema.DETAIL_MAX_SCALE)
    accent = cinema.highlight_color(cinema.resolve_highlight("dual", seed))
    cinema.draw_chrome(canvas, accent, swipe_hint=index < count)
    cinema.draw_credit(canvas, credit(slide.item))
    canvas.convert("RGB").save(stem.with_suffix(".png"))
    x, y, width, height = box
    still_frame.unlink(missing_ok=True)
    return video_into(stem.with_suffix(".png"), slide.media, (x, y, x + width, y + height), stem.with_suffix(".mp4"),
                      cover=False)


def contact_sheet(items: list[Item], name: str, target: Path) -> Path:
    """Numbered thumbnails of a collection for Telegram: ▶ on videos, the handle under each."""
    columns, cell, gap, label = 3, 340, 20, 54
    rows = max(1, (len(items) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * cell + (columns + 1) * gap, 110 + rows * (cell + label + gap)), (16, 17, 19))
    draw = ImageDraw.Draw(sheet)
    draw.text((gap, 40), name, font=cinema.inter(40, 700), fill=(240, 240, 240), anchor="lm")
    draw.text((gap, 82), f"{len(items)} posts", font=cinema.inter(26, 400), fill=(150, 150, 150), anchor="lm")
    for index, item in enumerate(items):
        x = gap + (index % columns) * (cell + gap)
        y = 110 + (index // columns) * (cell + label + gap)
        thumb, is_video = None, False
        found = photos(item)
        if found:
            with Image.open(found[0]) as opened:
                thumb = ImageOps.fit(opened.convert("RGB"), (cell, cell))
        elif main_videos(item):
            is_video = True
            with Image.open(frame(main_videos(item)[0], target.with_suffix(f".{item.id}.jpg"))) as opened:
                thumb = ImageOps.fit(opened.convert("RGB"), (cell, cell))
            target.with_suffix(f".{item.id}.jpg").unlink(missing_ok=True)
        if thumb is None:
            thumb = Image.new("RGB", (cell, cell), (0, 0, 0))
            words = card.x_text(item.status())[:140]
            lines, line = [], ""
            for word in words.split():
                if cinema.inter(24, 400).getlength(f"{line} {word}") > cell - 40:
                    lines.append(line)
                    line = word
                else:
                    line = f"{line} {word}".strip()
            lines.append(line)
            for row, text in enumerate(lines[:7]):
                while text and cinema.inter(24, 400).getlength(text) > cell - 40:
                    text = text[:-2] + "…"
                ImageDraw.Draw(thumb).text((20, 84 + row * 32), text, font=cinema.inter(24, 400), fill=(230, 230, 230))
        sheet.paste(thumb, (x, y))
        if is_video or main_videos(item):
            draw.ellipse((x + cell - 70, y + 14, x + cell - 14, y + 70), fill=(29, 155, 240))
            draw.polygon([(x + cell - 52, y + 28), (x + cell - 52, y + 56), (x + cell - 28, y + 42)], fill=(255, 255, 255))
        draw.rounded_rectangle((x + 10, y + 10, x + 62, y + 62), radius=10, fill=(0, 0, 0))
        draw.text((x + 36, y + 36), str(index + 1), font=cinema.inter(30, 800), fill=(255, 255, 255), anchor="mm")
        draw.text((x, y + cell + 28), "@" + handle(item), font=cinema.inter(26, 600), fill=(200, 200, 200), anchor="lm")
    sheet.save(target, quality=90)
    return target
