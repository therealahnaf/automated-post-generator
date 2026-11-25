#!/usr/bin/env python3
"""Render a meme post: one image with our title and the faithful X post (tools/viral/x_post_card.py).

Layouts:
  top     the date and title under the masthead, the post below
  bottom  the post first, the date and title low on the card, as on a news cover
  arrow   "top", plus a hand-drawn arrow from the title to `--point` and a circle around it
`--point` is either a phrase that appears word for word on one line of the post or the quoted post (it is
circled), or "media" or "quote" for the whole photo grid or quoted post. If the phrase is not found, the card
is drawn without the arrow rather than pointing at the wrong place.

  python tools/viral/meme_post.py --tweet-json <tweet.json> --title "<title>" --layout arrow --point "<phrase>" --output card.png
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.news import cinema_post as cinema  # noqa: E402
from tools.news import generate_post  # noqa: E402
from tools.news.local_backgrounds import stable_seed  # noqa: E402
from tools.viral import x_post_card as card  # noqa: E402

WIDTH, HEIGHT = cinema.WIDTH, cinema.HEIGHT
TITLE_MAX_SIZE, TITLE_MIN_SIZE, TITLE_MAX_HEIGHT = 84, 56, 300
FOOT = HEIGHT - 44


def place_panel(canvas: Image.Image, panel: Image.Image, top: int) -> None:
    for offset, blur, strength in ((28, 42, 215), (8, 10, 150)):
        shadow = Image.new("L", canvas.size, 0)
        shadow.paste(panel.getchannel("A").point(lambda value, strength=strength: value * strength // 255),
                     (card.PANEL_LEFT, top + offset))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.putalpha(shadow.filter(ImageFilter.GaussianBlur(blur)))
        canvas.alpha_composite(layer)
    canvas.alpha_composite(panel, (card.PANEL_LEFT, top))


def fit_title(title: str) -> cinema.HeadlineLayout:
    shown = cinema.displayable(generate_post.normalize_news_text(title), cinema.HEADLINE_FACE.path)
    return cinema.fit_headline(shown, TITLE_MAX_SIZE, TITLE_MAX_HEIGHT, cinema.HEADLINE_FACE)


def draw_title(canvas: Image.Image, layout, date_middle: float, label: str, accent, seed: str) -> float:
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    cinema.draw_date(ImageDraw.Draw(overlay), date_middle, label)
    bottom = cinema.draw_headline(overlay, layout, date_middle + 40, accent, seed)
    canvas.alpha_composite(cinema.text_shadow(overlay))
    canvas.alpha_composite(overlay)
    return bottom


def stroke_layer(size: tuple[int, int], painter, scale: int = 3) -> Image.Image:
    big = Image.new("RGBA", (size[0] * scale, size[1] * scale), (0, 0, 0, 0))
    painter(ImageDraw.Draw(big), scale)
    return big.resize(size, Image.Resampling.LANCZOS)


def hand_arrow(canvas: Image.Image, start: tuple[float, float], end: tuple[float, float], color, seed: str,
               gutter: float) -> None:
    """A marker arrow from the title down the right-hand gutter and into its target from the side, so it never
    crosses the post's words: one curved stroke and a two-stroke head, slightly uneven, with a soft shadow."""
    rng = random.Random(stable_seed("meme-arrow", seed))
    (x0, y0), (x3, y3) = start, end
    # A smooth curve through fixed waypoints: right into the gutter, down it, then level into the target.
    waypoints = [(x0, y0), (gutter + rng.uniform(-5, 5), y0 + 90), (gutter + rng.uniform(-5, 5), y3 - 110),
                 (min(gutter - 60, x3 + 150), y3 + rng.uniform(-3, 3)), (x3, y3)]
    if y3 - 110 <= y0 + 90:
        waypoints = [waypoints[0], ((gutter + x3) / 2, (y0 + y3) / 2), waypoints[-1]]
    padded = [waypoints[0], *waypoints, waypoints[-1]]
    points = []
    for index in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[index - 1:index + 3]
        for step in range(24):
            t = step / 24
            points.append(tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t ** 2
                                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t ** 3) for k in (0, 1)))
    points.append((x3, y3))
    # Keep the stroke and its shadow inside the card.
    points = [(min(x, WIDTH - 22), y) for x, y in points]
    angle = math.atan2(y3 - points[-5][1], x3 - points[-5][0])
    head = [(x3 + 44 * math.cos(angle + math.pi + turn), y3 + 44 * math.sin(angle + math.pi + turn)) for turn in (-0.5, 0.48)]

    def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
        scaled = [(x * scale, y * scale) for x, y in points]
        draw.line(scaled, fill=color, width=11 * scale, joint="curve")
        for hx, hy in head:
            draw.line([(hx * scale, hy * scale), (x3 * scale, y3 * scale)], fill=color, width=11 * scale)
        for x, y in [points[0], (x3, y3), *head]:
            draw.ellipse(((x - 5.5) * scale, (y - 5.5) * scale, (x + 5.5) * scale, (y + 5.5) * scale), fill=color)

    layer = stroke_layer(canvas.size, paint)
    canvas.alpha_composite(cinema.text_shadow(layer, spread=5, blur=10, strength=170))
    canvas.alpha_composite(layer)


def hand_circle(canvas: Image.Image, box: tuple[float, float, float, float], color, seed: str) -> tuple[float, float]:
    """A loose marker loop around `box` that overshoots where it started; returns the point just right of it."""
    rng = random.Random(stable_seed("meme-circle", seed))
    left, top, right, bottom = box
    cx, cy = (left + right) / 2, (top + bottom) / 2
    rx, ry = (right - left) / 2 + 22, (bottom - top) / 2 + 9
    start = rng.uniform(-2.4, -1.9)
    points = []
    for step in range(121):
        t = start + (2 * math.pi + 0.55) * step / 120
        wobble = 1 + 0.05 * math.sin(3 * t + rng.uniform(0, 1)) + 0.04 * step / 120
        points.append((cx + rx * wobble * math.cos(t), cy + ry * wobble * math.sin(t)))

    def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
        draw.line([(x * scale, y * scale) for x, y in points], fill=color, width=8 * scale, joint="curve")

    layer = stroke_layer(canvas.size, paint)
    canvas.alpha_composite(cinema.text_shadow(layer, spread=5, blur=10, strength=150))
    canvas.alpha_composite(layer)
    return cx + rx * 1.06 + 14, cy


def render(tweet_json: Path, title: str, *, layout: str = "top", point: str | None = None,
           highlight: str = "dual") -> tuple[Image.Image, dict]:
    tweet = json.loads(tweet_json.read_text(encoding="utf-8-sig"))
    status = (tweet.get("items") or [{}])[0]
    seed = str(status.get("id") or tweet_json)
    accent = cinema.highlight_color(cinema.resolve_highlight(highlight, seed))
    when = card.posted_at(status)
    label = (when or __import__("datetime").datetime.now()).strftime("%d %b %Y").upper()
    title_layout = fit_title(title)
    canvas = cinema.card_backdrop(seed, 1)
    anchors: dict = {}
    phrase = point if point not in (None, "media", "quote") else None
    info = {"layout": layout, "title": title, "point": point, "pointed": False}

    if layout == "bottom":
        title_top = cinema.BLOCK_BOTTOM - title_layout.height
        date_middle = title_top - 40
        budget = round(date_middle - 50 - card.TOP_LIMIT)
        panel = card.render_post_card(status, tweet_json.parent, budget=budget, anchors=anchors, phrase=phrase)
        top = card.TOP_LIMIT + max(0, (budget - panel.height) // 2)
        place_panel(canvas, panel, top)
        draw_title(canvas, title_layout, date_middle, label, accent, seed)
    else:
        date_middle = cinema.MASTHEAD_BOTTOM + 36
        title_bottom = date_middle + 40 + title_layout.height
        top = round(title_bottom + (70 if layout == "arrow" else 44))
        budget = FOOT - top
        panel = card.render_post_card(status, tweet_json.parent, budget=budget, anchors=anchors, phrase=phrase)
        place_panel(canvas, panel, top)
        draw_title(canvas, title_layout, date_middle, label, accent, seed)
        target = anchors.get("phrase") if phrase else anchors.get(point or "")
        if layout == "arrow" and target:
            box = (target[0] + card.PANEL_LEFT, target[1] + top, target[2] + card.PANEL_LEFT, target[3] + top)
            if phrase:
                end = hand_circle(canvas, box, accent, seed)
            else:
                # Into the top-right corner of the photos or the quoted post.
                end = (box[2] - 40, box[1] + 46)
            last = title_layout.lines[-1]
            last_middle = date_middle + 40 + title_layout.height - title_layout.line_height / 2
            start_x = cinema.MARGIN + title_layout.font.getlength(last) + 34
            # A long last line leaves no room beside it, so the arrow starts just under its end instead.
            start = (start_x, last_middle) if start_x < WIDTH - 120 else (WIDTH - 110, last_middle + title_layout.line_height * 0.62)
            hand_arrow(canvas, start, end, accent, seed, gutter=WIDTH - 40)
            info["pointed"] = True
    cinema.draw_chrome(canvas, accent, swipe_hint=False)
    return canvas.convert("RGB"), info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--layout", choices=("top", "bottom", "arrow"), default="top")
    parser.add_argument("--point")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    image, info = render(args.tweet_json, args.title, layout=args.layout, point=args.point)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
