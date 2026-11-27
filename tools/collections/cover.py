"""The collection cover: a fanned stack of the collection's own cards over the Cinema title, with a loop arrow.

The stack shows up to three cards, one per post first: Media stacks photos and video frames, Tweets stacks X post
screenshots, Media + Tweets puts a post screenshot in front of media and a second post. The title's highlighted
first line holds one to three words and is sized to one fixed width (what "The Strange Jensen" measures at full
headline size), so the loop arrow, which starts just right of the middle of the highlight's right edge, lands in the
same place on every cover. The arrow is drawn under the stack. There is no "Swipe For More" on this cover.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from tools.collections import slides as slide_kit
from tools.collections.store import Item
from tools.news import cinema_post as cinema
from tools.news import generate_post
from tools.viral import x_post_card as card

WIDTH, HEIGHT = cinema.WIDTH, cinema.HEIGHT
ARROW = generate_post.PROJECT_ROOT / "assets" / "collections" / "loop-arrow.png"  # drawn from loop-arrow.svg
STACK = 3
# Back to front: (rotation in degrees, x offset, y offset) for 3, 2 and 1 cards.
FANS = {
    3: [(-8, -135, -30), (7, 135, -40), (-1.5, 0, 34)],
    2: [(-7, -95, -30), (3, 70, 30)],
    1: [(-2, 0, 0)],
}
MEDIA_CARD = (500, 625)  # 4:5, like the carousel itself
POST_WIDTH = 640
POST_BUDGET = 760  # height a post screenshot may use before X's own layout shortens it
EDGE = (255, 255, 255, 60)
MEDIA_KINDS = ("photo", "video")

REFERENCE_LEAD = "The Strange Jensen"
FIRST_LINE_WORDS = 3
LEAD_SCALE = (0.8, 1.7)  # the first line may be this much smaller or larger than the lines under it
ARROW_CAPS = 1.5  # the arrow's height, in capital heights at full headline size
ARROW_GAP = 0.14  # space between the highlight's edge and the arrow's tail, in full headline sizes


# ------------------------------------------------------------------------------------------------ the stack
def first_per_post(candidates: list[slide_kit.Slide]) -> list[slide_kit.Slide]:
    chosen, seen = [], set()
    for slide in candidates:
        if slide.item.id not in seen:
            chosen.append(slide)
            seen.add(slide.item.id)
    return chosen + [slide for slide in candidates if slide not in chosen]


def pick(kind: str, planned: list[slide_kit.Slide]) -> list[slide_kit.Slide]:
    """The stack's cards, front first: Media only media, Tweets only post screenshots, Media + Tweets both."""
    media = first_per_post([slide for slide in planned if slide.kind in MEDIA_KINDS])
    posts = first_per_post([slide for slide in planned if slide.kind not in MEDIA_KINDS])
    if kind == "media":
        return (media or posts)[:STACK]
    if kind == "tweets":
        return posts[:STACK]
    if not media or not posts:
        return first_per_post(planned)[:STACK]
    third = [posts[1]] if len(posts) > 1 else [media[1]] if len(media) > 1 else []
    return [posts[0], media[0], *third]


def card_image(slide: slide_kit.Slide, work: Path) -> Image.Image:
    if slide.kind == "photo":
        return cinema.open_flat(slide.media).convert("RGBA")
    if slide.kind == "video":
        return cinema.open_flat(slide_kit.frame(slide.media, work / f"stack-frame-{slide.item.id}.jpg")).convert("RGBA")
    return card.render_post_card(slide.item.status(), slide.item.folder, budget=POST_BUDGET)


def framed(image: Image.Image, size: tuple[int, int], radius: int = 22) -> Image.Image:
    image = cinema.rounded(image.resize(size, Image.Resampling.LANCZOS), radius)
    ImageDraw.Draw(image).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, outline=EDGE, width=3)
    return image


def sized(source: Image.Image, slide: slide_kit.Slide, max_h: int) -> Image.Image:
    """Media is cropped to one 4:5 card size; a post screenshot keeps its shape at one width."""
    if slide.kind in MEDIA_KINDS:
        width, height = MEDIA_CARD
        scale = min(1.0, max_h / height)
        size = (round(width * scale), round(height * scale))
        return framed(ImageOps.fit(source, size, Image.Resampling.LANCZOS, centering=(0.5, 0.3)), size)
    scale = min(POST_WIDTH / source.width, max_h / source.height)
    return framed(source, (round(source.width * scale), round(source.height * scale)))


def draw_stack(canvas: Image.Image, cards: list[tuple[Image.Image, slide_kit.Slide]], box: tuple[int, int, int, int]) -> None:
    left, top, width, height = box
    max_h = height - 90
    # The first card is the front one; draw back to front.
    for (angle, dx, dy), (source, slide) in zip(FANS[len(cards)], reversed(cards)):
        rotated = sized(source, slide, max_h).rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        x = left + (width - rotated.width) // 2 + dx
        y = top + (height - rotated.height) // 2 + dy
        for offset, blur, strength in ((26, 38, 220), (8, 10, 160)):
            mask = Image.new("L", canvas.size, 0)
            mask.paste(rotated.getchannel("A").point(lambda value, strength=strength: value * strength // 255), (x, y + offset))
            shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(blur)))
            canvas.alpha_composite(shadow)
        canvas.alpha_composite(rotated, (x, y))


# ------------------------------------------------------------------------------------------------ the title
@dataclass
class Title:
    lead_font: ImageFont.FreeTypeFont
    lead: str
    body_font: ImageFont.FreeTypeFont
    rest: list[str]
    lead_height: int
    line_height: int

    @property
    def height(self) -> int:
        return self.lead_height + len(self.rest) * self.line_height


def full_size() -> int:
    return round(cinema.HEADLINE_MAX_SIZE * cinema.HEADLINE_FACE.size_scale)


def fit_title(title: str) -> Title:
    """The largest body size that fits, with a first line of 1-3 words sized to the reference width. Among word
    counts, the one that reaches the width (within LEAD_SCALE) with the least change from the body size wins."""
    face = cinema.HEADLINE_FACE
    words = cinema.displayable(generate_post.normalize_news_text(title), face.path).split()
    if not words:
        raise ValueError("The title has no characters the headline font can draw.")
    target = cinema.load_face(face, full_size()).getlength(REFERENCE_LEAD)
    width = WIDTH - cinema.MARGIN * 2
    fitted = None
    for size in range(full_size(), cinema.HEADLINE_MIN_SIZE - 1, -2):
        body = cinema.load_face(face, size)
        options = []
        for take in range(1, min(FIRST_LINE_WORDS, len(words)) + 1):
            scale = target / body.getlength(" ".join(words[:take]))
            clamped = min(max(scale, LEAD_SCALE[0]), LEAD_SCALE[1])
            options.append((abs(math.log(scale / clamped)), abs(math.log(clamped)), take, clamped))
        _, _, take, scale = min(options)
        lead_size = round(size * scale)
        lead_font = cinema.load_face(face, lead_size)
        rest = [" ".join(line) for line in cinema.balanced_lines(words[take:], body, width)] if words[take:] else []
        fitted = Title(lead_font, " ".join(words[:take]), body, rest, round(lead_size * cinema.LEAD_LINE_HEIGHT),
                       round(size * face.line_height))
        if (all(body.getlength(line) <= width for line in rest) and lead_font.getlength(fitted.lead) <= width
                and fitted.height <= cinema.HEADLINE_MAX_HEIGHT + 40):
            return fitted
    return fitted


def draw_title(overlay: Image.Image, title: Title, top: float, accent: tuple[int, ...], seed: str) -> None:
    """The first line black on the marker at its own size, the rest white, as Cinema draws them."""
    cinema.draw_marked_line(overlay, title.lead, title.lead_font, top, title.lead_height, accent, seed)
    font = title.body_font
    _, glyph_top, _, glyph_bottom = font.getbbox("Hgjpy", anchor="ls")
    glyph_height = glyph_bottom - glyph_top
    draw = ImageDraw.Draw(overlay)
    for row, text in enumerate(title.rest):
        line_top = top + title.lead_height + row * title.line_height
        draw.text((cinema.MARGIN, line_top + (title.line_height - glyph_height) / 2 - glyph_top), text, font=font,
                  fill=generate_post.WHITE, anchor="ls")


def solid(image: Image.Image) -> Image.Image:
    return image.getchannel("A").point(lambda value: 255 if value > 128 else 0)


def arrow_tail(arrow: Image.Image) -> tuple[int, float]:
    """Where the arrow's stroke starts: the middle of its leftmost solid column."""
    alpha = solid(arrow)
    left = alpha.getbbox()[0]
    _, top, _, bottom = alpha.crop((left, 0, left + 2, arrow.height)).getbbox()
    return left, (top + bottom - 1) / 2


def draw_arrow(layer: Image.Image, title: Title, top: float, accent: tuple[int, ...], seed: str) -> None:
    """The loop at one fixed size in the highlight's colour, its tail a small gap right of the middle of the
    highlight's right edge."""
    marker = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    cinema.draw_marked_line(marker, title.lead, title.lead_font, top, title.lead_height, (255, 255, 255, 255), seed)
    alpha = solid(marker)
    _, marker_top, _, marker_bottom = alpha.getbbox()
    middle = (marker_top + marker_bottom - 1) // 2
    edge = alpha.crop((0, middle, WIDTH, middle + 1)).getbbox()[2] - 1
    cap = -cinema.load_face(cinema.HEADLINE_FACE, full_size()).getbbox("H", anchor="ls")[1]
    with Image.open(ARROW) as source:
        height = round(cap * ARROW_CAPS)
        shape = source.convert("RGBA").resize((round(source.width * height / source.height), height), Image.Resampling.LANCZOS)
    arrow = Image.new("RGBA", shape.size, tuple(accent[:3]) + (255,))
    arrow.putalpha(shape.getchannel("A"))
    tail_x, tail_y = arrow_tail(arrow)
    layer.alpha_composite(arrow, (round(edge + full_size() * ARROW_GAP - tail_x), round(middle - tail_y)))


def credit_line(items: list[Item]) -> str:
    handles = list(dict.fromkeys(slide_kit.handle(item) for item in items))
    shown = [f"@{name}" for name in handles[:2]]
    rest = len(handles) - len(shown)
    return "Via " + ", ".join(shown) + (f" +{rest} more" if rest > 0 else "")


# ------------------------------------------------------------------------------------------------ the cover
def render(title: str, post_date: date, kind: str, items: list[Item], planned: list[slide_kit.Slide], work: Path,
           seed: str) -> Image.Image:
    cards = [(card_image(slide, work), slide) for slide in pick(kind, planned)]
    accent = cinema.highlight_color(cinema.resolve_highlight("dual", seed))
    fitted = fit_title(title)
    headline_top = cinema.BLOCK_BOTTOM - fitted.height
    date_center = headline_top - 40
    block_top = date_center - 16

    canvas = Image.new("RGBA", (WIDTH, HEIGHT), cinema.SHADE + (255,))
    with Image.open(cinema.pick_backdrop(seed)) as backdrop:
        canvas.paste(cinema.dark_backdrop(ImageOps.exif_transpose(backdrop)), (0, 0))
    fade_top = max(0, block_top - 320)
    canvas.alpha_composite(cinema.vertical_gradient((WIDTH, HEIGHT - fade_top), [(0, 0), (0.4, 150), (0.75, 190), (1, 190)]),
                           (0, fade_top))
    canvas.alpha_composite(cinema.vertical_gradient((WIDTH, 220), [(0, 105), (1, 0)]), (0, 0))

    # The arrow goes down first, so the card stack covers it wherever the two meet.
    arrow_layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw_arrow(arrow_layer, fitted, headline_top, accent, seed)
    canvas.alpha_composite(cinema.text_shadow(arrow_layer))
    canvas.alpha_composite(arrow_layer)

    if cards:
        stack_top = cinema.MASTHEAD_BOTTOM + 10
        draw_stack(canvas, cards, (40, stack_top, WIDTH - 80, block_top - cinema.PHOTO_GAP + 30 - stack_top))

    cinema.draw_chrome(canvas, accent, False)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    cinema.draw_date(ImageDraw.Draw(overlay), date_center, post_date.strftime("%d %b %Y").upper())
    draw_title(overlay, fitted, headline_top, accent, seed)
    canvas.alpha_composite(cinema.text_shadow(overlay))
    canvas.alpha_composite(overlay)
    cinema.draw_credit(canvas, credit_line(items))
    return canvas.convert("RGB")
