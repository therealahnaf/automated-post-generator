#!/usr/bin/env python3
"""The Giant Mark model-release carousel, rendered deterministically from the validated copy and tweet photos.

* Cover: a darkened news backdrop, the releasing company's logo large behind a big left-aligned "Meet <model>"
  headline on the hand-cut marker, then "by <logo> <company>" and the date. A company with no logo on file gets two
  or three large letters in its place.
* Each landscape photo becomes a two-slide spread, so a benchmark can be shown about twice as large. Spreads
  alternate between Lead + Wide (the whole caption on the first slide, the image across most of the second) and
  Adaptive Max (the image as large as the spread allows, the caption halves wherever that leaves room).
* Portrait or square-ish photos, and any photo beyond what fits in ten slides, use the single Cinema detail card.
* Without photos, the two or three description segments use the Cinema summary card.
* The marker colour alternates coral and mint from section to section, starting from a stable per-post choice.

Every choice (backdrop, colour, marker shape, grain) is seeded from the post, so a re-render is identical.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

try:
    from ..news import cinema_post as cp
    from ..news import generate_post as gp
    from ..news.local_backgrounds import stable_seed
except ImportError:
    from tools.news import cinema_post as cp
    from tools.news import generate_post as gp
    from tools.news.local_backgrounds import stable_seed

W, H = cp.WIDTH, cp.HEIGHT
SPREAD_W = W * 2
M = cp.MARGIN
WHITE, INK = gp.WHITE, gp.INK
LOGO_DIR = gp.PROJECT_ROOT / "assets" / "models" / "logos"
MAX_SLIDES = 10
# A photo narrower than this (width / height) gains little from spanning two slides, so it stays on one.
SPREAD_MIN_ASPECT = 1.0
FILLER_WORDS = {"the", "of", "and", "for", "inc", "ltd", "co"}


class UnsupportedText(ValueError):
    """The model or company name has nothing the Giant Mark fonts can set; the caller uses the classic design."""


# ---------------------------------------------------------------------------------------------------------- logos
@dataclass(frozen=True)
class Brand:
    accent: tuple[int, int, int]
    mark: Image.Image           # white mono mark, or the fallback letters
    color: Image.Image          # colour mark (or the mono mark)
    known: bool


def _logos() -> dict:
    return json.loads((LOGO_DIR / "logos.json").read_text(encoding="utf-8"))


def _load(path: str | None) -> Image.Image | None:
    return Image.open(LOGO_DIR / path).convert("RGBA") if path else None


def logo_slug(company: str) -> str | None:
    """The logo for the releasing company only, matched on whole-word aliases; never guessed from the model name."""
    key = re.sub(r"\s+", " ", company.lower()).strip()
    slug, best = None, 0
    for candidate, entry in _logos().items():
        for alias in entry["aliases"]:
            if key == alias or re.search(rf"(?<![\w.]){re.escape(alias)}(?![\w.])", key):
                if len(alias) > best:
                    slug, best = candidate, len(alias)
    return slug


def fallback_letters(company: str) -> str:
    """Initials of a multi-word name (at most three), else the first two letters of a one-word name."""
    words = [word for word in re.split(r"[\s\-_.]+", company)
             if word and word[0].isalnum() and word.lower() not in FILLER_WORDS]
    if len(words) >= 2:
        letters = "".join(word[0] for word in words[:3])
    else:
        letters = re.sub(r"[^0-9A-Za-z]", "", words[0] if words else "")[:2]
    letters = cp.displayable(letters.upper(), cp.INTER_TIGHT).strip()
    return letters or "AI"


def letters_glyph(company: str) -> Image.Image:
    letters = fallback_letters(company)
    font = cp.inter(700, 900)
    widths = [font.getlength(character) for character in letters]
    image = Image.new("RGBA", (round(sum(widths) + 200), 1000), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x = 100
    for character, width in zip(letters, widths):
        draw.text((x, 820), character, font=font, fill=WHITE, anchor="ls")
        x += width - 18
    return image.crop(image.getchannel("A").getbbox())


def resolve_brand(company: str, seed: str) -> Brand:
    slug = logo_slug(company)
    if slug is None:
        accent = gp.BRAND_MINT[:3] if stable_seed("model-monogram", seed) % 2 else gp.BRAND_CORAL[:3]
        glyph = letters_glyph(company)
        return Brand(accent, glyph, glyph, False)
    entry = _logos()[slug]
    mark = _load(entry["mark"])
    accent = tuple(int(entry["accent"][index:index + 2], 16) for index in (1, 3, 5))
    return Brand(accent, mark, _load(entry.get("color")) or mark, True)


# -------------------------------------------------------------------------------------------------------- helpers
def fit(image: Image.Image, box: tuple[float, float], max_scale: float | None = None) -> Image.Image:
    scale = min(box[0] / image.width, box[1] / image.height)
    if max_scale is not None:
        scale = min(scale, max_scale)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                        Image.Resampling.LANCZOS)


def grain(size: tuple[int, int], seed: str, strength: int = 10) -> Image.Image:
    """Fine monochrome grain from a seeded generator, so a re-render is byte-identical."""
    small = (size[0] // 2, size[1] // 2)
    rng = random.Random(stable_seed("model-grain", seed))
    noise = Image.frombytes("L", small, rng.randbytes(small[0] * small[1]))
    noise = noise.filter(ImageFilter.GaussianBlur(0.6)).resize(size, Image.Resampling.BICUBIC)
    layer = Image.new("RGBA", size, (255, 255, 255, 0))
    layer.putalpha(noise.point(lambda value: abs(value - 128) * strength // 64))
    return layer


def shadowed(canvas: Image.Image, layer: Image.Image, **kwargs) -> None:
    canvas.alpha_composite(cp.text_shadow(layer, **kwargs))
    canvas.alpha_composite(layer)


def other(highlight: str) -> str:
    return "cyan" if highlight == "red" else "red"


def section_highlight(seed: str, section: int) -> str:
    """Section 0 is the cover; each later section (a spread or a single card) switches colour."""
    first = cp.resolve_highlight("dual", seed)
    return first if section % 2 == 0 else other(first)


@dataclass
class Title:
    font: object
    size: int
    lines: list[str]

    @property
    def lead(self) -> float:
        return self.size * 1.30

    @property
    def pitch(self) -> float:
        return self.size * 1.10

    @property
    def height(self) -> float:
        return self.lead + (len(self.lines) - 1) * self.pitch


def split_wide(words: list[str], font, max_width: int) -> list[str]:
    """Break a word wider than the column after its hyphens or underscores, then its dots (repo-style names)."""
    out: list[str] = []
    for word in words:
        if font.getlength(word) <= max_width:
            out.append(word)
            continue
        pieces: list[str] = []
        for chunk in re.findall(r"[^-_]+[-_]?|[-_]", word):
            pieces += [chunk] if font.getlength(chunk) <= max_width else re.findall(r"[^.]+\.?|\.", chunk)
        current = ""
        for piece in pieces:
            if current and font.getlength(current + piece) > max_width:
                out.append(current)
                current = piece
            else:
                current += piece
        out.append(current)
    return out


def fit_title(text: str, max_width: int, max_size: int, max_height: int, max_lines: int, min_size: int = 54) -> Title:
    """The largest size that fits; "Meet" is never left alone on the marker line at large sizes."""
    for size in range(max_size, min_size - 1, -2):
        font = cp.load_face(cp.HEADLINE_FACE, size)
        lines = [" ".join(line) for line in cp.balanced_lines(split_wide(text.split(), font, max_width), font, max_width)]
        title = Title(font, size, lines)
        lone = len(lines[0].split()) == 1 and len(lines) > 1 and size > 96
        if (not lone and len(lines) <= max_lines and title.height <= max_height
                and all(font.getlength(line) <= max_width for line in lines)):
            return title
    font = cp.load_face(cp.HEADLINE_FACE, min_size)
    return Title(font, min_size, [" ".join(line) for line in
                                  cp.balanced_lines(split_wide(text.split(), font, max_width), font, max_width)])


def marked(layer: Image.Image, text: str, font, x: float, baseline: float, accent, seed: str) -> None:
    _, top, _, bottom = font.getbbox("Hgjpy", anchor="ls")
    pad = max(5, round(font.size * 0.11))
    height = round(bottom - top + pad * 2)
    layer.alpha_composite(cp.marker_highlight(round(font.getlength(text) + 32), height, accent, seed),
                          (round(x - 16 - height), round(baseline + top - pad - height)))
    ImageDraw.Draw(layer).text((x, baseline), text, font=font, fill=INK, anchor="ls")


def draw_title(layer: Image.Image, title: Title, top: float, accent, seed: str) -> float:
    _, gtop, _, gbottom = title.font.getbbox("Hgjpy", anchor="ls")
    draw = ImageDraw.Draw(layer)
    for index, line in enumerate(title.lines):
        slot_top = top if index == 0 else top + title.lead + (index - 1) * title.pitch
        slot = title.lead if index == 0 else title.pitch
        baseline = slot_top + (slot - (gbottom - gtop)) / 2 - gtop
        if index == 0:
            marked(layer, line, title.font, M, baseline, accent, seed)
        else:
            draw.text((M, baseline), line, font=title.font, fill=WHITE, anchor="ls")
    return top + title.height


def swipe_hint(canvas: Image.Image, accent, label: str) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    right, middle, arrow = W - 58, cp.FOOTER_HINT_MIDDLE, 34
    draw.text((right - arrow - 14, middle), label, font=cp.inter(24, 600), fill=WHITE, anchor="rm")
    draw.line((right - arrow, middle, right, middle), fill=accent, width=4)
    draw.line((right - 11, middle - 10, right, middle, right - 11, middle + 10), fill=accent, width=4, joint="curve")
    shadowed(canvas, layer, spread=5, blur=10, strength=120)


# ---------------------------------------------------------------------------------------------------------- cover
def render_cover(model_name: str, company_name: str, post_date: date, *, seed: str, has_photos: bool
                 ) -> tuple[Image.Image, Path]:
    """The first slide; returns it with the backdrop it used. Raises ValueError if the name cannot be set."""
    model = cp.displayable(model_name, cp.BBH_BOGLE).strip()
    company = cp.displayable(company_name, cp.INTER_TIGHT).strip()
    if not model or not company:
        raise UnsupportedText("Model or company name has no characters the Giant Mark fonts can set.")
    accent = cp.highlight_color(section_highlight(seed, 0))
    brand = resolve_brand(company, seed)
    backdrop = cp.pick_backdrop(seed)
    with Image.open(backdrop) as source:
        canvas = cp.dark_backdrop(ImageOps.exif_transpose(source).convert("RGB")).convert("RGBA")
    canvas.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 70)))

    title = fit_title(f"Meet {model}", W - M * 2, 138, 470, max_lines=4)
    credit_gap, date_gap = 58, 64
    block = title.height + credit_gap + date_gap
    top = max(cp.MASTHEAD_BOTTOM + 260, min(H * 0.66 - block / 2, cp.BLOCK_BOTTOM - block))
    room = top - 150 - 30
    wide = brand.mark.width > brand.mark.height * 1.6
    if not brand.known:
        big = fit(brand.mark, (840, min(620, room + 150)))
    elif wide:
        big = fit(brand.mark, (980, min(560, room)))
    else:
        big = fit(brand.mark, (780, min(700, room + 160)))
    light = tuple(min(255, round(channel * 1.1 + 35)) for channel in brand.accent)
    dark = tuple(round(channel * 0.5) for channel in brand.accent)
    ramp = Image.linear_gradient("L").resize(big.size).rotate(35)
    filled = Image.composite(Image.new("RGBA", big.size, dark + (255,)), Image.new("RGBA", big.size, light + (255,)), ramp)
    filled.putalpha(big.getchannel("A").point(lambda value: value * 175 // 255))
    center_x = W - 60 - big.width / 2 if big.width < 900 else W / 2
    center_y = 150 + big.height / 2 + 20
    if not brand.known:
        # Anchored by its foot, so the headline always overlaps the letters a little, as it does a logo.
        center_y = max(170 + big.height / 2, top + 80 - big.height / 2)
    canvas.alpha_composite(filled, (round(center_x - big.width / 2), round(center_y - big.height / 2)))
    canvas.alpha_composite(cp.vertical_gradient((W, H), [(0.0, 0), (0.42, 0), (0.62, 150), (1.0, 225)]))
    canvas.alpha_composite(cp.vertical_gradient((W, H), [(0.0, 140), (0.12, 0), (1.0, 0)]))
    canvas.alpha_composite(grain((W, H), seed))

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    credit_middle = draw_title(layer, title, top, accent, f"{seed}-cover") + credit_gap
    by_font, name_font = cp.inter(34, 500), cp.inter(34, 700)
    draw.text((M, credit_middle), "by", font=by_font, fill=(205, 205, 205), anchor="lm")
    x = M + by_font.getlength("by ") + 2
    if brand.known and not wide:
        chip = fit(brand.color, (38 * 3, 38))
        layer.alpha_composite(chip, (round(x), round(credit_middle - chip.height / 2)))
        x += chip.width + 14
    while name_font.getlength(company) > W - M - x and name_font.size > 20:
        name_font = cp.inter(name_font.size - 2, 700)
    draw.text((x, credit_middle), company, font=name_font, fill=WHITE, anchor="lm")
    cp.draw_date(draw, credit_middle + date_gap, post_date.strftime("%d %b %Y").upper())
    shadowed(canvas, layer)
    cp.draw_chrome(canvas, accent, False)
    swipe_hint(canvas, accent, "Swipe For Benchmarks" if has_photos else "Swipe For More")
    return canvas.convert("RGB"), backdrop


# --------------------------------------------------------------------------------------------------------- spreads
def clean(text: str) -> str:
    return cp.displayable(gp.normalize_news_text(text), cp.INTER_TIGHT)


def split_caption(text: str) -> tuple[str, str]:
    """Two halves for the two slides: at the sentence break nearest the middle, else a semicolon, else a comma,
    else between words. A trailing comma or semicolon is dropped from the first half."""
    text = clean(text)
    middle = len(text) / 2
    for pattern in (r"(?<=[.!?])\s+(?=[A-Z0-9$])", r"(?<=;)\s+", r"(?<=,)\s+"):
        cuts = [match for match in re.finditer(pattern, text) if 0.25 * len(text) < match.start() < 0.75 * len(text)]
        if cuts:
            cut = min(cuts, key=lambda match: abs(match.start() - middle))
            return text[:cut.start()].strip().rstrip(",;"), text[cut.end():].strip()
    words = text.split()
    half = max(1, len(words) // 2)
    return " ".join(words[:half]), " ".join(words[half:])


def fit_text(text: str, max_width: int, max_height: int, max_size: int, min_size: int, weight: int = 700):
    for size in range(max_size, min_size - 1, -2):
        font = cp.inter(size, weight)
        lines = [" ".join(line) for line in cp.balanced_lines(text.split(), font, max_width)]
        if len(lines) * size * 1.18 <= max_height and all(font.getlength(line) <= max_width for line in lines):
            return font, lines, round(size * 1.18)
    font = cp.inter(min_size, weight)
    return font, [" ".join(line) for line in cp.balanced_lines(text.split(), font, max_width)], round(min_size * 1.18)


def text_height(text: str, max_width: int, max_height: int, size=(50, 34)) -> int:
    if not text:
        return 0
    _, lines, step = fit_text(text, max_width, max_height, *size)
    return len(lines) * step


def draw_text(layer: Image.Image, text: str, x: float, top: float, max_width: int, max_height: int, *,
              size=(50, 34), align="left", marker=None, seed="") -> float:
    if not text:
        return top
    font, lines, step = fit_text(text, max_width, max_height, *size)
    draw = ImageDraw.Draw(layer)
    _, gtop, _, gbottom = font.getbbox("Hgjpy", anchor="ls")
    for row, line in enumerate(lines):
        left = x if align == "left" else x - font.getlength(line)
        baseline = top + row * step + (step - (gbottom - gtop)) / 2 - gtop
        if row == 0 and marker is not None:
            marked(layer, line, font, left, baseline, marker, seed)
        else:
            draw.text((left, baseline), line, font=font, fill=WHITE, anchor="ls")
    return top + len(lines) * step


def spread_backdrop(seed: str) -> tuple[Image.Image, Path]:
    """One news backdrop across both slides, darkened like the cover, so the join is invisible."""
    path = cp.pick_backdrop(seed)
    with Image.open(path) as source:
        image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), (SPREAD_W, H), method=Image.Resampling.LANCZOS)
    image = Image.blend(image, Image.new("RGB", (SPREAD_W, H), cp.SHADE), 0.52).convert("RGBA")
    image.alpha_composite(Image.new("RGBA", (SPREAD_W, H), (0, 0, 0, 60)))
    image.alpha_composite(grain((SPREAD_W, H), seed, 9))
    return image, path


def drop_shadow(canvas: Image.Image, shape: Image.Image, xy: tuple[int, int]) -> None:
    blur, pad = 26, 78
    shade = Image.new("RGBA", (shape.width + pad * 2, shape.height + pad * 2), (0, 0, 0, 0))
    silhouette = Image.new("RGBA", shape.size, (0, 0, 0, 0))
    silhouette.putalpha(shape.getchannel("A").point(lambda value: value * 200 // 255))
    shade.alpha_composite(silhouette, (pad, pad))
    canvas.alpha_composite(shade.filter(ImageFilter.GaussianBlur(blur)), (xy[0] - pad, xy[1] - pad + 16))


def fit_photo(photo: Image.Image, box_w: float, box_h: float) -> Image.Image:
    return fit(photo, (box_w, box_h), max_scale=2.2)


def place(canvas: Image.Image, photo: Image.Image, box: tuple[float, float, float, float],
          center_x: float | None = None) -> tuple[int, int, int, int]:
    """The whole photo, as large as `box` allows, centred on `center_x` (the join by default), rounded, shadowed."""
    left, top, right, bottom = box
    center_x = SPREAD_W / 2 if center_x is None else center_x
    image = cp.rounded(fit_photo(photo, min(center_x - left, right - center_x) * 2, bottom - top), 22)
    x, y = round(center_x - image.width / 2), round(top + (bottom - top - image.height) / 2)
    drop_shadow(canvas, image, (x, y))
    canvas.alpha_composite(image, (x, y))
    return x, y, x + image.width, y + image.height


def finish(spread: Image.Image, text: Image.Image, accent, last: bool) -> list[Image.Image]:
    shadowed(spread, text, spread=9, blur=16, strength=200)
    slides = []
    for index in range(2):
        slide = spread.crop((index * W, 0, index * W + W, H))
        cp.draw_chrome(slide, accent, not (last and index == 1))
        slides.append(slide.convert("RGB"))
    return slides


def open_photo(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGBA")


def lead_and_wide(photo_path: Path, caption: str, *, seed: str, highlight: str, last: bool
                  ) -> tuple[list[Image.Image], Path]:
    """The whole caption large on the first slide; the image starts beside it and takes most of the second."""
    accent = cp.highlight_color(highlight)
    spread, backdrop = spread_backdrop(seed)
    text = Image.new("RGBA", (SPREAD_W, H), (0, 0, 0, 0))
    column = 440
    caption = clean(caption)
    height = text_height(caption, column, 760, size=(60, 40))
    draw_text(text, caption, M, H / 2 - height / 2, column, 760, size=(60, 40), marker=accent, seed=seed)
    left, right = M + column + 50, SPREAD_W - 40
    place(spread, open_photo(photo_path), (left, 150, right, H - 150), center_x=(left + right) / 2)
    return finish(spread, text, accent, last), backdrop


def adaptive_max(photo_path: Path, caption: str, *, seed: str, highlight: str, last: bool
                 ) -> tuple[list[Image.Image], Path]:
    """The image as large as the spread allows; the caption halves go beside a squarish image or above and below
    a wide one, whichever leaves the image bigger."""
    accent = cp.highlight_color(highlight)
    photo = open_photo(photo_path)
    first, second = split_caption(caption)
    spread, backdrop = spread_backdrop(seed)
    text = Image.new("RGBA", (SPREAD_W, H), (0, 0, 0, 0))
    top_limit, bottom_limit, column = 150, cp.FOOTER_HINT_MIDDLE - 44, 340
    side_box = (M + column + 40, top_limit, SPREAD_W - M - column - 40, bottom_limit)
    side = fit_photo(photo, side_box[2] - side_box[0], side_box[3] - side_box[1])
    caption_h = max(text_height(first, 900, 200), text_height(second, 900, 200))
    stack_box = (40, top_limit + 22 + caption_h + 40, SPREAD_W - 40, bottom_limit - caption_h - 40)
    stack = fit_photo(photo, stack_box[2] - stack_box[0], stack_box[3] - stack_box[1])
    if side.width * side.height >= stack.width * stack.height:
        _, y0, _, y1 = place(spread, photo, side_box)
        for part, x, align, marker in ((first, M, "left", accent), (second, SPREAD_W - M, "right", None)):
            height = text_height(part, column, 640, size=(46, 32))
            draw_text(text, part, x, (y0 + y1) / 2 - height / 2, column, 640, size=(46, 32), align=align,
                      marker=marker, seed=seed)
    else:
        _, y0, _, y1 = place(spread, photo, stack_box)
        draw_text(text, first, M, y0 - 40 - caption_h, 900, 200, marker=accent, seed=seed)
        draw_text(text, second, SPREAD_W - M, y1 + 40, 900, 200, align="right")
    return finish(spread, text, accent, last), backdrop


SPREAD_LAYOUTS = (("lead-and-wide", lead_and_wide), ("adaptive-max", adaptive_max))


# ------------------------------------------------------------------------------------------------------- planning
def photo_aspect(path: Path) -> float:
    with Image.open(path) as source:
        width, height = ImageOps.exif_transpose(source).size
    return width / height


def plan_sections(photos: list[Path]) -> list[str]:
    """"spread" or "single" per photo, in order: landscape photos spread while ten slides allow (the cover plus one
    slide per photo, plus one more per spread); the rest stay single."""
    extra = MAX_SLIDES - 1 - len(photos)
    plan = []
    for photo in photos:
        if extra > 0 and photo_aspect(photo) >= SPREAD_MIN_ASPECT:
            plan.append("spread")
            extra -= 1
        else:
            plan.append("single")
    return plan


@dataclass
class Slide:
    image: Image.Image
    name: str


@dataclass
class Carousel:
    slides: list[Slide]
    backdrops: list[Path]
    sections: list[dict]
    # Each spread whole, for the website archive: (index of its first slide in `slides`, the joined image).
    spreads: list[tuple[int, Image.Image]]


def join(pair: list[Image.Image]) -> Image.Image:
    whole = Image.new("RGB", (SPREAD_W, H))
    whole.paste(pair[0], (0, 0))
    whole.paste(pair[1], (W, 0))
    return whole


def render_carousel(model_name: str, company_name: str, descriptions: list[str], photos: list[Path], post_date: date,
                    *, seed: str) -> Carousel:
    cover, cover_backdrop = render_cover(model_name, company_name, post_date, seed=seed, has_photos=bool(photos))
    slides = [Slide(cover, "cover")]
    backdrops = [cover_backdrop]
    sections: list[dict] = []
    spreads: list[tuple[int, Image.Image]] = []
    if photos:
        plan = plan_sections(photos)
        total = 1 + sum(2 if kind == "spread" else 1 for kind in plan)
        spread_count = 0
        for number, (photo, caption, kind) in enumerate(zip(photos, descriptions, plan), start=1):
            highlight = section_highlight(seed, number)
            last = number == len(photos)
            if kind == "spread":
                layout, render = SPREAD_LAYOUTS[spread_count % 2]
                spread_count += 1
                pair, backdrop = render(photo, caption, seed=f"{seed}-spread-{number}", highlight=highlight, last=last)
                spreads.append((len(slides), join(pair)))
                slides += [Slide(pair[0], f"feature-{number}-left"), Slide(pair[1], f"feature-{number}-right")]
            else:
                layout, index = "single", len(slides) + 1
                slides.append(Slide(cp.render_detail(caption, photo, index=index, count=total, highlight=highlight,
                                                     seed=seed), f"feature-{number}"))
                backdrop = cp.pick_backdrop(f"{seed}-card-{index}")
            backdrops.append(backdrop)
            sections.append({"photo": str(photo.resolve()), "layout": layout, "highlight": highlight})
    else:
        total = 1 + len(descriptions)
        for number, caption in enumerate(descriptions, start=1):
            highlight = section_highlight(seed, number)
            index = len(slides) + 1
            slides.append(Slide(cp.render_summary(caption, index=index, count=total, highlight=highlight, seed=seed),
                                f"summary-{number}"))
            backdrops.append(cp.pick_backdrop(f"{seed}-card-{index}"))
            sections.append({"photo": None, "layout": "summary", "highlight": highlight})
    return Carousel(slides, backdrops, sections, spreads)
