#!/usr/bin/env python3
"""Validate the agent's media plan for a news carousel and resolve it into paths and credits.

The agent writes media-plan.json after looking at the collector's contact sheet, using its IDs:

    {
      "cover": "s1",                          # a tweet photo (t#) or source image (s#); null keeps today's cover;
                                              # {"image": "s1", "fill": true} crops a portrait to fill the card
      "slides": [
        {"image": "t2", "text": true},        # detail card: a story segment over the image
        {"image": "s3"},                      # photo card: a real image that speaks for itself
        {"quote": "q4"}                       # quote card: a primary-source sentence, verbatim
      ]
    }

This script checks it and writes media-plan.resolved.json, which generate_post.py, generate_carousel_copy.py and
generate_carousel.py read. Credits are written here from each item's provenance, never by the agent:
"Image via X" for the tweet's media (poster handles stay out, per the poster-identity policy), "Image: <publisher>"
for source images and "Source: <publisher>" for quotes. Any problem exits 1 with the reason, and the workflow then
continues without a plan, exactly as before.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

MAX_SLIDES = 9  # the headline card plus nine stays within Instagram's ten
MAX_QUOTES = 2
TWEET_CREDIT = "Image via X"


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("source-media.json must contain an object.")
    return manifest


def image_index(manifest: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for item in manifest.get("tweet_photos", []):
        index[item["id"]] = {"path": item["path"], "credit": TWEET_CREDIT, "context": "a photo from the X post",
                             "origin": "tweet"}
    for item in manifest.get("images", []):
        context = item.get("caption") or item.get("alt") or f"an image from {item.get('publisher')}"
        index[item["id"]] = {"path": item["path"], "credit": f"Image: {item.get('publisher') or 'source'}",
                             "context": context, "origin": "source", "page_url": item.get("page_url")}
    return index


def resolve(plan: dict, manifest: dict, *, text_on_images: bool = False) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("The media plan must be a JSON object.")
    unknown = set(plan) - {"cover", "slides", "notes"}
    if unknown:
        raise ValueError(f"Unknown media plan fields: {', '.join(sorted(unknown))}.")
    images = image_index(manifest)
    quotes = {item["id"]: item for item in manifest.get("quotes", [])}
    similar = {item["id"]: set(item.get("similar_to", []))
               for item in [*manifest.get("tweet_photos", []), *manifest.get("images", [])]}
    logos = {item["id"] for item in [*manifest.get("tweet_photos", []), *manifest.get("images", [])] if item.get("logo")}

    def check_not_logo(image_id: str, where: str) -> None:
        if image_id in logos:
            raise ValueError(f"{where}: {image_id} is a logo, which tells the reader nothing; leave it out.")
    used: set[str] = set()

    def check_new(image_id: str, where: str) -> None:
        repeats = similar.get(image_id, set()) & used
        if repeats:
            raise ValueError(f"{where}: {image_id} is the same picture as {', '.join(sorted(repeats))}, already used.")

    cover = plan.get("cover")
    fill = False
    if isinstance(cover, dict):
        if set(cover) - {"image", "fill"} or "image" not in cover:
            raise ValueError('An object cover takes only "image" and "fill".')
        fill = cover.get("fill", False)
        if not isinstance(fill, bool):
            raise ValueError("cover.fill must be true or false.")
        cover = cover["image"]
    resolved_cover = None
    if cover is not None:
        if cover not in images:
            raise ValueError(f"Cover {cover!r} is not a collected image ID.")
        if not Path(images[cover]["path"]).is_file():
            raise ValueError(f"Cover image file is missing: {images[cover]['path']}")
        check_not_logo(cover, "Cover")
        used.add(cover)
        resolved_cover = {"id": cover, "fill": fill, **images[cover]}

    slides = plan.get("slides", [])
    if not isinstance(slides, list):
        raise ValueError("slides must be a list.")
    if len(slides) > MAX_SLIDES:
        raise ValueError(f"At most {MAX_SLIDES} slides follow the headline card; the plan has {len(slides)}.")
    resolved_slides: list[dict[str, Any]] = []
    quote_count = 0
    for number, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict) or not (("image" in slide) ^ ("quote" in slide)):
            raise ValueError(f"Slide {number} needs exactly one of 'image' or 'quote'.")
        extra = set(slide) - {"image", "text", "quote"}
        if extra:
            raise ValueError(f"Slide {number} has unknown fields: {', '.join(sorted(extra))}.")
        if "quote" in slide:
            quote_id = slide["quote"]
            if quote_id not in quotes:
                raise ValueError(f"Slide {number}: {quote_id!r} is not a collected quote ID.")
            if quote_id in used:
                raise ValueError(f"Slide {number}: quote {quote_id} is used twice.")
            quote_count += 1
            used.add(quote_id)
            quote = quotes[quote_id]
            resolved_slides.append({"kind": "quote", "id": quote_id, "text": quote["text"],
                                    "credit": f"Source: {quote.get('publisher') or 'source'}",
                                    "page_url": quote.get("page_url")})
            continue
        image_id = slide["image"]
        if image_id not in images:
            raise ValueError(f"Slide {number}: {image_id!r} is not a collected image ID.")
        if image_id in used:
            raise ValueError(f"Slide {number}: image {image_id} already appears in the carousel.")
        check_new(image_id, f"Slide {number}")
        check_not_logo(image_id, f"Slide {number}")
        if not Path(images[image_id]["path"]).is_file():
            raise ValueError(f"Slide {number}: image file is missing: {images[image_id]['path']}")
        used.add(image_id)
        text = slide.get("text", False)
        if not isinstance(text, bool):
            raise ValueError(f"Slide {number}: text must be true or false.")
        text = text or text_on_images
        resolved_slides.append({"kind": "detail" if text else "photo", "id": image_id, **images[image_id]})
    if quote_count > MAX_QUOTES:
        raise ValueError(f"At most {MAX_QUOTES} quote slides.")
    return {"cover": resolved_cover, "slides": resolved_slides}


def configure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


def read_resolved(path: Path) -> dict:
    """For the renderers: a resolved plan, rechecked so a hand-edited file cannot point at missing images."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or "slides" not in payload or "cover" not in payload:
        raise ValueError(f"{path} is not a resolved media plan; run media_plan.py first.")
    for item in [payload["cover"], *payload["slides"]]:
        if item and item.get("kind") != "quote" and not Path(item["path"]).is_file():
            raise ValueError(f"Media plan image is missing: {item['path']}")
    return payload


def main(argv: list[str] | None = None) -> int:
    configure_utf8(sys.stdout)
    configure_utf8(sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-media", type=Path, required=True, help="The collector's source-media.json.")
    parser.add_argument("--plan", type=Path, required=True, help="The agent's media-plan.json.")
    parser.add_argument("--output", type=Path, help="Defaults to media-plan.resolved.json beside the plan.")
    parser.add_argument("--allow-photo-slides", action="store_true",
                        help="Honour photo-only slides. By default every image slide gets a story segment, as news "
                             "v1 always had (the user wants a heading under every image).")
    args = parser.parse_args(argv)
    try:
        resolved = resolve(json.loads(args.plan.read_text(encoding="utf-8-sig")), load_manifest(args.source_media),
                           text_on_images=not args.allow_photo_slides)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    output = args.output or args.plan.with_name("media-plan.resolved.json")
    output.write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = [f"cover: {resolved['cover']['id'] if resolved['cover'] else 'default'}"] + [
        f"{index}: {slide['kind']} {slide['id']} ({slide['credit']})" for index, slide in enumerate(resolved["slides"], start=2)]
    print(json.dumps({"resolved_plan": str(output.resolve()), "carousel": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
