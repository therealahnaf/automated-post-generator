#!/usr/bin/env python3
"""Collect real media for a news post from the story's primary sources, numbered for the agent's media plan.

Inputs are the fetched tweet JSON, the finalized `.sources.json` sidecar, and any official press-kit, bio or product
pages the research found (`--media-page`). Output, in --output-dir:

* source-media.json - `tweet_photos` (t1, t2 ... the tweet's own downloaded photos), `images` (s1, s2 ... images
  from primary-source pages, with page, publisher, alt text and caption) and `quotes` (q1, q2 ... sentences that
  appear word for word on those pages);
* the downloaded images;
* contact-sheet.jpg - every candidate image labelled with its ID, for the agent to look at.

Only primary sources supply images or quotes: the company, project, researcher, regulator or store behind the
story. News outlets remain text sources; their photos are licensed agency pictures. Social sites are skipped.
This step never fails a job: a page that blocks, times out or has nothing usable is recorded and skipped, and the
command still exits 0 with whatever it found.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import TextIO
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 25
MAX_IMAGES = 12
MAX_PAGES = 8
MIN_SIDE = 480
MAX_IMAGE_BYTES = 25 * 1024 * 1024
# Outlets whose pictures are agency or staff photos: cite their reporting, never lift their images or words.
NEWS_OUTLETS = {
    "apnews.com", "reuters.com", "bloomberg.com", "cnn.com", "nytimes.com", "washingtonpost.com", "wsj.com",
    "theverge.com", "techcrunch.com", "theinformation.com", "scmp.com", "techspot.com", "tomshardware.com",
    "wccftech.com", "videocardz.com", "notebookcheck.net", "gamesradar.com", "404media.co", "digitaltrends.com",
    "teslanorth.com", "newsbytesapp.com", "timesofisrael.com", "marketscreener.com", "theblock.co", "clarin.com",
    "livescience.com", "gadgetreview.com", "techpowerup.com", "xtechpioneer.com", "engadget.com", "arstechnica.com",
    "wired.com", "cnbc.com", "ft.com", "bbc.co.uk", "bbc.com", "theguardian.com", "forbes.com", "businessinsider.com",
    "techradar.com", "zdnet.com", "venturebeat.com", "axios.com", "semafor.com", "theregister.com", "9to5mac.com",
    "9to5google.com", "macrumors.com", "androidauthority.com", "pcgamer.com", "ign.com", "kotaku.com", "polygon.com",
    "eurogamer.net", "neowin.net", "windowscentral.com", "pcworld.com", "cnet.com", "gizmodo.com", "mashable.com",
    "fortune.com", "time.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com", "foxnews.com", "nypost.com",
    "dailymail.co.uk", "independent.co.uk", "telegraph.co.uk", "economist.com", "newsweek.com", "politico.com",
    "thehill.com", "yahoo.com", "msn.com", "medium.com", "substack.com",
}
SOCIAL = {"x.com", "twitter.com", "t.co", "reddit.com", "polymarket.com", "kalshi.com", "youtube.com", "youtu.be",
          "facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "threads.net", "bsky.app"}
SKIP_IMAGE = re.compile(r"logo|icon|favicon|sprite|avatar|badge|pixel|tracking|spacer|placeholder|emoji|linkedin|"
                        r"facebook|twitter-|/x-|youtube|instagram|share|gravatar|profile", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")


def registered(host: str) -> str:
    parts = host.lower().removeprefix("www.").split(".")
    if len(parts) > 2 and parts[-2] in {"co", "com", "org", "ac", "gov", "go"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_primary(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    base = registered(parsed.hostname)
    return base not in NEWS_OUTLETS and base not in SOCIAL and not parsed.path.lower().endswith(".pdf")


class PageReader(HTMLParser):
    """og:image and article images (with alt text and figure captions), the page title, site name, description
    and its body paragraphs."""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.meta_images: list[dict] = []
        self.images: list[dict] = []
        self.title = ""
        self.site = ""
        self.description = ""
        self.paragraphs: list[str] = []
        self._in_title = False
        self._figures: list[list[dict]] = []
        self._caption: list[str] | None = None
        self._paragraph: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {key: (value or "") for key, value in attrs}
        if tag in ("nav", "footer", "header", "aside", "script", "style", "noscript"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content", "")
            if key in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src") and content:
                self.meta_images.append({"url": urljoin(self.base, content), "kind": "preview"})
            elif key == "og:site_name":
                self.site = content.strip()
            elif key == "og:title" and not self.title:
                self.title = content.strip()
            elif key in ("og:description", "description") and not self.description:
                self.description = " ".join(content.split())
        if tag == "figure":
            self._figures.append([])
        if tag == "figcaption":
            self._caption = []
        if tag == "p" and not self._skip:
            self._paragraph = []
        if tag in ("img", "source") and not self._skip:
            src = a.get("src") or a.get("data-src") or a.get("data-original") or ""
            srcset = a.get("srcset") or a.get("data-srcset") or ""
            if srcset:
                candidates = [part.strip().split(" ") for part in srcset.split(",") if part.strip()]
                widest = max(candidates, key=lambda bits: int(re.sub(r"\D", "", bits[1]) or 0) if len(bits) > 1 else 0)
                src = widest[0] or src
            if src and not src.startswith("data:"):
                image = {"url": urljoin(self.base, src), "alt": a.get("alt", "").strip(), "kind": "article"}
                self.images.append(image)
                if self._figures:
                    self._figures[-1].append(image)

    def handle_endtag(self, tag):
        if tag in ("nav", "footer", "header", "aside", "script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "figcaption" and self._caption is not None:
            text = " ".join("".join(self._caption).split())
            for image in self._figures[-1] if self._figures else []:
                image["caption"] = text
            self._caption = None
        if tag == "figure" and self._figures:
            self._figures.pop()
        if tag == "p" and self._paragraph is not None:
            text = " ".join("".join(self._paragraph).split())
            if len(text) >= 60:
                self.paragraphs.append(text)
            self._paragraph = None

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._caption is not None:
            self._caption.append(data)
        if self._paragraph is not None:
            self._paragraph.append(data)


def fingerprint(image: Image.Image) -> int:
    pixels = list(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes())
    return sum(1 << bit for bit in range(64) if pixels[(bit // 8) * 9 + bit % 8] > pixels[(bit // 8) * 9 + bit % 8 + 1])


def content_fingerprint(image: Image.Image) -> int:
    """The fingerprint of the picture without its borders, so a letterboxed copy (a tweet image of a diagram on a
    plain background) matches the original."""
    gray = image.convert("L")
    corner = gray.getpixel((2, 2))
    mask = ImageChops.difference(gray, Image.new("L", gray.size, corner)).point(lambda value: 255 if value > 24 else 0)
    box = mask.getbbox()
    return fingerprint(image.crop(box) if box else image)


SIMILAR_BITS = 14
NO_COVER_HINT = (
    "No collected image can carry the cover (only logos or nothing). If the story is about a person, product or "
    "project, find its official photo page (a newsroom bio or leadership page, a press kit, the product page or the "
    "project's README) and rerun the collector with --media-page <url>."
)


def looks_like_logo(image: Image.Image) -> bool:
    """A small mark on an empty field: little content, all of it inside a small box. Charts and tables are mostly
    empty too, but their axes and rows span the frame, so they are not caught."""
    rgba = image.convert("RGBA")
    small = Image.alpha_composite(Image.new("RGBA", rgba.size, (128, 128, 128, 255)), rgba).convert("RGB").resize((96, 96))
    data = small.tobytes()
    buckets: dict[tuple[int, int, int], int] = {}
    for index in range(0, len(data), 3):
        key = (data[index] // 24, data[index + 1] // 24, data[index + 2] // 24)
        buckets[key] = buckets.get(key, 0) + 1
    top = max(buckets, key=buckets.get)
    field = tuple(min(255, channel * 24 + 12) for channel in top)
    mask = ImageChops.difference(small, Image.new("RGB", small.size, field)).convert("L").point(
        lambda value: 255 if value > 40 else 0)
    content = sum(1 for value in mask.tobytes() if value) / (96 * 96)
    box = mask.getbbox() or (0, 0, 96, 96)
    area = (box[2] - box[0]) * (box[3] - box[1]) / (96 * 96)
    return content < 0.12 and area < 0.25


def mark_similar(manifest: dict, prints: dict[str, int]) -> None:
    """List, for each image, the other candidates that show the same picture, so a plan never uses both."""
    for item in [*manifest["tweet_photos"], *manifest["images"]]:
        item["similar_to"] = [other for other, mark in prints.items()
                              if other != item["id"] and bin(mark ^ prints[item["id"]]).count("1") <= SIMILAR_BITS]


def is_flat(image: Image.Image) -> bool:
    """All black, all transparent or one solid fill: nothing to show."""
    rgba = image.convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (128, 128, 128, 255)), rgba).convert("L")
    return ImageStat.Stat(flat.resize((64, 64))).stddev[0] < 12


ABBREVIATION = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Inc|Corp|Co|Ltd|vs|No|Fig|Gen|Sen|Rep|Gov)|"
                          r"\b[A-Z]|(?:[A-Z]\.){2,}|e\.g|i\.e|etc)\.$")


def sentences(text: str) -> list[str]:
    """Whole sentences only: a split after an abbreviation such as "Mr." or "U.S." is joined back."""
    merged: list[str] = []
    for part in SENTENCE.split(text):
        part = part.strip()
        if merged and ABBREVIATION.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        elif part:
            merged.append(part)
    return [sentence for sentence in merged if 40 <= len(sentence) <= 320]


def tweet_document(tweet_json: Path) -> dict:
    return json.loads(tweet_json.read_text(encoding="utf-8-sig"))


def tweet_photos(tweet_json: Path) -> list[Path]:
    item = tweet_document(tweet_json)["items"][0]
    photos = []
    for photo in item.get("downloaded_photos") or []:
        stored = Path(str(photo.get("local_path", "")))
        for candidate in (tweet_json.parent / stored, stored, tweet_json.parent / "media" / stored.name):
            if candidate.is_file():
                photos.append(candidate.resolve())
                break
    return photos


def candidate_pages(tweet_json: Path, sources: Path | None, media_pages: list[str]) -> list[str]:
    urls = list(media_pages)
    if sources and sources.is_file():
        try:
            urls += json.loads(sources.read_text(encoding="utf-8-sig")).get("urls", [])
        except (OSError, ValueError):
            pass
    for item in tweet_document(tweet_json).get("items", []):
        card = item.get("card")
        if isinstance(card, dict) and card.get("url"):
            urls.append(str(card["url"]))
        urls += re.findall(r"https?://[^\s)\]]+", str(item.get("text", "")))
    return [url for url in dict.fromkeys(url.rstrip(".,") for url in urls) if is_primary(url)][:MAX_PAGES]


def shopify_images(session: requests.Session, url: str) -> list[dict] | None:
    """Shopify stores often block page scrapes but publish the product's images as JSON."""
    match = re.search(r"(https?://[^/]+)/(?:[a-z-]+/)?products/([^/?#]+)", url)
    if not match:
        return None
    try:
        response = session.get(f"{match.group(1)}/products/{match.group(2)}.json", timeout=TIMEOUT)
        product = response.json()["product"] if response.ok else None
    except (requests.RequestException, ValueError, KeyError):
        return None
    if not product:
        return None
    return [{"url": image["src"], "alt": image.get("alt") or "", "kind": "product"} for image in product.get("images", [])]


def download(session: requests.Session, url: str) -> Image.Image | None:
    try:
        response = session.get(url, timeout=TIMEOUT, stream=True)
        if not response.ok:
            return None
        try:
            data = response.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
        except TypeError:  # a plain file-like body
            data = response.raw.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return None
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Exception:
        return None


def contact_sheet(entries: list[tuple[str, Path]], target: Path) -> None:
    if not entries:
        return
    cell, label = 300, 34
    columns = min(4, len(entries))
    rows = -(-len(entries) // columns)
    sheet = Image.new("RGB", (columns * (cell + 12) + 12, rows * (cell + label + 12) + 12), (28, 28, 30))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(str(Path(__file__).resolve().parents[2] / "assets" / "fonts" / "InterTight-Variable.ttf"), 24)
    except OSError:
        font = ImageFont.load_default()
    for index, (name, path) in enumerate(entries):
        x = 12 + (index % columns) * (cell + 12)
        y = 12 + (index // columns) * (cell + label + 12)
        with Image.open(path) as image:
            thumb = image.convert("RGBA")
            thumb.thumbnail((cell, cell))
            plate = Image.new("RGBA", thumb.size, (60, 60, 64, 255))
            plate.alpha_composite(thumb)
            sheet.paste(plate.convert("RGB"), (x + (cell - thumb.width) // 2, y + label + (cell - thumb.height) // 2))
        draw.text((x, y), name, font=font, fill=(240, 240, 240))
    sheet.save(target, quality=85)


def collect(tweet_json: Path, sources: Path | None, media_pages: list[str], output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)
    manifest: dict = {"tweet_photos": [], "images": [], "quotes": [], "pages": []}
    prints: list[int] = []
    content_prints: dict[str, int] = {}
    sheet_entries: list[tuple[str, Path]] = []
    for number, path in enumerate(tweet_photos(tweet_json), start=1):
        try:
            with Image.open(path) as image:
                prints.append(fingerprint(image))
                content_prints[f"t{number}"] = content_fingerprint(image.convert("RGB"))
                size = list(image.size)
        except OSError:
            continue
        with Image.open(path) as image:
            logo = looks_like_logo(image)
        manifest["tweet_photos"].append({"id": f"t{number}", "path": str(path), "size": size, "logo": logo})
        sheet_entries.append((f"t{number}", path))

    seen: set[str] = set()
    for page_url in candidate_pages(tweet_json, sources, media_pages):
        page = {"url": page_url}
        manifest["pages"].append(page)
        try:
            response = session.get(page_url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            page["error"] = type(exc).__name__
            continue
        candidates: list[dict] = []
        reader = PageReader(response.url)
        if response.ok and "html" in response.headers.get("content-type", ""):
            if "charset" not in response.headers.get("content-type", "").lower():
                response.encoding = "utf-8"
            reader.feed(response.text)
            candidates = [*reader.meta_images, *reader.images]
        else:
            page["error"] = f"http {response.status_code}"
        store = shopify_images(session, page_url)
        if store:
            candidates = store + candidates
            page.pop("error", None)
        publisher = reader.site or registered(urlparse(response.url).hostname or "")
        page.update(url=response.url, publisher=publisher, title=reader.title)
        for text in [reader.description, *reader.paragraphs[:6]]:
            for sentence in sentences(text):
                if all(sentence != quote["text"] for quote in manifest["quotes"]):
                    manifest["quotes"].append({"id": f"q{len(manifest['quotes']) + 1}", "text": sentence,
                                               "publisher": publisher, "page_url": response.url})
        for candidate in candidates:
            url = candidate["url"]
            if (len(manifest["images"]) >= MAX_IMAGES or url in seen or SKIP_IMAGE.search(url)
                    or urlparse(url).path.lower().endswith((".svg", ".gif", ".ico"))):
                continue
            seen.add(url)
            image = download(session, url)
            if image is None or min(image.size) < MIN_SIDE or image.width / image.height > 4 or image.height / image.width > 4:
                continue
            if is_flat(image):
                continue
            mark = fingerprint(image)
            if any(bin(mark ^ other).count("1") <= 6 for other in prints):
                continue
            prints.append(mark)
            number = len(manifest["images"]) + 1
            content_prints[f"s{number}"] = content_fingerprint(image.convert("RGB"))
            target = output_dir / f"s{number}-{hashlib.sha1(url.encode()).hexdigest()[:8]}.png"
            image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB").save(target)
            manifest["images"].append({
                "id": f"s{number}", "path": str(target.resolve()), "image_url": url, "page_url": response.url,
                "publisher": publisher, "kind": candidate["kind"], "alt": candidate.get("alt", ""),
                "caption": candidate.get("caption", ""), "size": list(image.size), "logo": looks_like_logo(image),
            })
            sheet_entries.append((f"s{number}", target))
    manifest["quotes"] = manifest["quotes"][:40]
    mark_similar(manifest, content_prints)
    similar = {item["id"]: item["similar_to"] for item in [*manifest["tweet_photos"], *manifest["images"]]}
    logos = {item["id"] for item in [*manifest["tweet_photos"], *manifest["images"]] if item.get("logo")}
    sheet_entries = [(name + (" (logo)" if name in logos else "")
                      + (f" (same as {', '.join(similar[name])})" if similar.get(name) else ""), path)
                     for name, path in sheet_entries]
    manifest["cover_candidates"] = [item["id"] for item in [*manifest["images"], *manifest["tweet_photos"]]
                                    if not item.get("logo")]
    if not manifest["cover_candidates"]:
        manifest["hint"] = NO_COVER_HINT
    contact_sheet(sheet_entries, output_dir / "contact-sheet.jpg")
    manifest["contact_sheet"] = str((output_dir / "contact-sheet.jpg").resolve()) if sheet_entries else None
    (output_dir / "source-media.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    return manifest


def configure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--sources", type=Path, help="The finalized .sources.json sidecar.")
    parser.add_argument("--media-page", action="append", default=[],
                        help="An official press-kit, bio or product page found in research; repeatable.")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8(sys.stdout)
    configure_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        manifest = collect(args.tweet_json, args.sources, args.media_page, args.output_dir)
    except Exception as exc:  # Collection is best effort: the post continues with the tweet's own media.
        args.output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"tweet_photos": [], "images": [], "quotes": [], "pages": [], "error": f"{type(exc).__name__}: {exc}"}
        (args.output_dir / "source-media.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "source_media": str((args.output_dir / "source-media.json").resolve()),
        "contact_sheet": manifest.get("contact_sheet"),
        "tweet_photos": [item["id"] for item in manifest["tweet_photos"]],
        "images": [f"{item['id']} {item['publisher']} {item['size'][0]}x{item['size'][1]} "
                   f"{(item['caption'] or item['alt'])[:60]}".strip() for item in manifest["images"]],
        "cover_candidates": manifest.get("cover_candidates", []),
        "hint": manifest.get("hint"),
        "logos": [item["id"] for item in [*manifest["tweet_photos"], *manifest["images"]] if item.get("logo")],
        "same_picture": sorted({" = ".join(sorted([item["id"], *item.get("similar_to", [])]))
                                for item in [*manifest["tweet_photos"], *manifest["images"]] if item.get("similar_to")}),
        "quotes": len(manifest["quotes"]),
        "pages": [f"{page.get('publisher') or page['url']}: {page.get('error', 'ok')}" for page in manifest["pages"]],
        "error": manifest.get("error"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
