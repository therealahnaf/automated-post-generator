"""Split a tagged story and generate distinct Qwen scenes in a shared style.

Examples (use the repository virtual environment):
    python experiments/drama3_tts/generate_story_images.py --story-file story.txt --dry-run
    python experiments/drama3_tts/generate_story_images.py --story-file story.txt --limit 2
    python experiments/drama3_tts/generate_story_images.py --story-file story.txt

Every square-bracket direction and supported opening/closing delivery tag is a
boundary. Tag-only beats are retained. segments.json records the ordered list,
original spans, prompts, image paths and request status. Completed images are
reused on subsequent runs. Interrupted/failed requests require --retry-failed.
Use a new output directory after changing the story, prompts or reference.

Every request attaches only the user's original bundled style reference.
Generated frames are never attached to later requests. Each segment receives
a fresh scene matching its spoken context. Requests run serially using only
qwen/qwen-image-2.1-turbo and OPENROUTER_API_KEY from the root .env. There are
no automatic retries, model fallbacks, audio generation or publication steps.
Images default to landscape via OpenRouter's aspect_ratio: "16:9" parameter.

API: https://openrouter.ai/docs/guides/overview/multimodal/image-generation
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path
from string import Template
from typing import Any

import requests
from PIL import Image
from dotenv import load_dotenv


DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]
PROMPTS_DIRECTORY = ROOT / "content_api" / "story_video" / "prompts"
MODEL = "qwen/qwen-image-2.1-turbo"
ENDPOINT = "https://openrouter.ai/api/v1/images"
DEFAULT_REFERENCE = DIRECTORY / "assets" / "style-reference.png"
TAG_PATTERN = re.compile(
    r"\[[^\[\]\r\n]+\]|</?(?:whisper|emphasis|soft|fast|slow|stress|forceful)>|<\|speaker:\d+\|>",
    re.IGNORECASE,
)
FORMATS = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "WEBP": ("image/webp", ".webp")}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_story(story: str) -> list[dict[str, Any]]:
    """Preserve every recognized tag boundary and all original text in order."""
    if not story.strip():
        raise ValueError("Story is empty")
    matches = list(TAG_PATTERN.finditer(story))
    spans: list[tuple[int, int, str | None]] = []
    prefix = matches[0].start() if matches else len(story)
    if story[:prefix].strip() or not matches:
        spans.append((0, prefix, None))
    for index, match in enumerate(matches):
        start = match.start() if spans else 0
        end = matches[index + 1].start() if index + 1 < len(matches) else len(story)
        spans.append((start, end, match.group()))
    active: list[str] = []
    direction: str | None = None
    segments = []
    for start, end, tag in spans:
        raw = story[start:end]
        text = raw.strip()
        kind = "untagged"
        if tag:
            text = text[len(tag):].strip()
            if tag.startswith("["):
                direction = tag
                kind = "direction"
            elif tag.startswith("<|"):
                kind = "speaker"
            elif tag.startswith("</"):
                kind = "delivery_close"
                opening = tag.replace("</", "<", 1).lower()
                if not active or active[-1].lower() != opening:
                    raise ValueError(f"Unmatched or misnested delivery tag: {tag}")
                active.pop()
            else:
                kind = "delivery_open"
                active.append(tag)
        segments.append({
            "index": len(segments) + 1, "tag": tag, "kind": kind,
            "active_tags": ([direction] if direction else []) + list(active),
            "text": text, "tag_only": not bool(text), "raw_text": raw,
            "source_start": start, "source_end": end,
        })
    if active:
        raise ValueError(f"Unclosed delivery tag: {active[-1]}")
    return segments


def visual_context(segment: dict[str, Any] | None) -> str:
    fields = ("index", "tag", "kind", "active_tags", "text", "tag_only")
    return json.dumps({key: segment[key] for key in fields} if segment else None, ensure_ascii=False)


def image_info(data: bytes) -> tuple[str, str]:
    with Image.open(io.BytesIO(data)) as image:
        image_format = image.format
        image.verify()
    if image_format not in FORMATS:
        raise ValueError(f"Unsupported image format: {image_format}")
    return FORMATS[image_format]


def render_prompt(templates: tuple[str, str], story: str, segments: list[dict], position: int, ratio: str) -> str:
    roles = "Image 1 is the user's ORIGINAL STYLE REFERENCE ONLY. No preceding or first generated image is attached."
    values = {
        "story": json.dumps(story, ensure_ascii=False),
        "current_segment": visual_context(segments[position]),
        "previous_segment": visual_context(segments[position - 1] if position else None),
        "next_segment": visual_context(segments[position + 1] if position + 1 < len(segments) else None),
        "reference_roles": roles, "aspect_ratio": ratio,
    }
    return Template(templates[0 if position == 0 else 1]).substitute(values)


def generate_image(prompt: str, references: list[Path], api_key: str, ratio: str, resolution: str) -> tuple[bytes, dict]:
    inputs = []
    for path in references:
        data = path.read_bytes()
        media_type, _ = image_info(data)
        inputs.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64," + base64.b64encode(data).decode("ascii")}})
    response = requests.post(
        ENDPOINT, headers={"Authorization": f"Bearer {api_key}"},
        json={"model": MODEL, "prompt": prompt, "n": 1, "resolution": resolution,
              "aspect_ratio": ratio, "input_references": inputs, "provider": {"allow_fallbacks": False}},
        timeout=(10, 240), allow_redirects=False,
    )
    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text.replace(api_key, '[REDACTED]')[:1000]}")
    result = response.json()
    if not isinstance(result, dict) or result.get("error"):
        raise RuntimeError("OpenRouter returned an image-generation error")
    outputs = result.get("data")
    if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict):
        raise RuntimeError("Expected exactly one generated image")
    encoded = outputs[0].get("b64_json")
    if not isinstance(encoded, str):
        raise RuntimeError("OpenRouter returned no base64 image")
    data = base64.b64decode(encoded, validate=True)
    image_info(data)
    # Persist only billing/count metadata, never request headers or image payloads.
    usage = result.get("usage") or {}
    metadata = {"generation_id": response.headers.get("X-Generation-Id"),
                "usage": {key: usage[key] for key in ("cost", "prompt_tokens", "completion_tokens", "total_tokens") if key in usage}}
    return data, metadata


def save_manifest(path: Path, document: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(story_file: Path, reference: Path, output_dir: Path, *, ratio: str = "16:9", resolution: str = "1K",
        dry_run: bool = False, limit: int | None = None, retry_failed: bool = False) -> dict:
    story = story_file.read_text(encoding="utf-8-sig")
    segments = split_story(story)
    templates = ((PROMPTS_DIRECTORY / "image_first.md").read_text(encoding="utf-8"),
                 (PROMPTS_DIRECTORY / "image_next.md").read_text(encoding="utf-8"))
    reference_data = reference.read_bytes()
    image_info(reference_data)
    prompts = [render_prompt(templates, story, segments, i, ratio) for i in range(len(segments))]
    settings = {"model": MODEL, "reference_mode": "original_style_only", "story_sha256": digest(story.encode()), "reference_sha256": digest(reference_data),
                "prompts_sha256": digest("\n".join(prompts).encode()), "aspect_ratio": ratio, "resolution": resolution}
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not dry_run and not api_key:
        raise ValueError("Set OPENROUTER_API_KEY in the environment or root .env")
    output_dir.mkdir(parents=True, exist_ok=True)
    lock = output_dir / ".generation.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("This output directory is locked by another run; do not start duplicate generations") from exc
    os.close(descriptor)
    try:
        manifest = output_dir / "segments.json"
        if manifest.exists():
            document = json.loads(manifest.read_text(encoding="utf-8"))
            if document.get("settings") != settings:
                raise ValueError("Story, reference or settings changed; use a new --output-dir")
        else:
            document = {"schema_version": 2, "settings": settings, "story_file": str(story_file.resolve()),
                        "style_reference": str(reference.resolve()), "segments": []}
            for index, segment in enumerate(segments):
                document["segments"].append(dict(segment, status="pending", image=None, prompt=prompts[index],
                    reference_segments=[], reference_source="style_reference"))
            save_manifest(manifest, document)
        if dry_run:
            return document
        entries = document["segments"]
        for position, entry in enumerate(entries[:limit] if limit is not None else entries):
            if entry["status"] == "completed":
                image_path = output_dir / entry["image"]
                if digest(image_path.read_bytes()) != entry.get("image_sha256"):
                    raise ValueError(f"Saved image for segment {position + 1} changed; use a new output directory")
                continue
            if entry["status"] != "pending" and not retry_failed:
                raise RuntimeError(f"Segment {position + 1} was interrupted or failed. Review the result, then use --retry-failed to retry explicitly")
            references = [reference]
            stem = f"segment-{position + 1:03d}"
            if any(output_dir.glob(stem + ".*")):
                raise RuntimeError(f"Unrecorded output exists for {stem}; review it before any paid retry")
            entry["status"] = "in_progress"
            save_manifest(manifest, document)
            print(f"Generating image {position + 1}/{len(entries)} with {len(references)} reference(s)...", file=sys.stderr, flush=True)
            try:
                data, metadata = generate_image(entry["prompt"], references, api_key, ratio, resolution)
                _, suffix = image_info(data)
                image_path = output_dir / (stem + suffix)
                with image_path.open("xb") as file:
                    file.write(data)
                entry.update(status="completed", image=image_path.name, image_sha256=digest(data), **metadata)
                entry.pop("error", None)
                save_manifest(manifest, document)
            except Exception as exc:
                entry["status"] = "failed"
                entry["error"] = str(exc).replace(api_key, "[REDACTED]")[:1000]
                save_manifest(manifest, document)
                raise
        return document
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--story-file", type=Path, required=True)
    parser.add_argument("--reference-image", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--aspect-ratio", choices=("4:5", "9:16", "16:9", "1:1", "3:4", "4:3"), default="16:9", help="Output framing (default: 16:9 landscape)")
    parser.add_argument("--resolution", choices=("1K", "2K"), default="1K")
    parser.add_argument("--limit", type=int, help="Generate only the first N segments; resume later with the same directory")
    parser.add_argument("--dry-run", action="store_true", help="Save the segment list and prompts, with no paid calls")
    parser.add_argument("--retry-failed", action="store_true", help="Explicitly retry an interrupted or failed image request")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    directory = args.output_dir or args.story_file.parent / (args.story_file.stem + "-images-style-only")
    try:
        document = run(args.story_file, args.reference_image, directory, ratio=args.aspect_ratio,
                       resolution=args.resolution, dry_run=args.dry_run, limit=args.limit, retry_failed=args.retry_failed)
    except (OSError, ValueError, KeyError, RuntimeError, requests.RequestException) as exc:
        key = os.getenv("OPENROUTER_API_KEY", "")
        message = str(exc).replace(key, "[REDACTED]") if key else str(exc)
        print(f"Story image generation failed: {message}", file=sys.stderr)
        return 1
    print(json.dumps({"manifest": str((directory / 'segments.json').resolve()), "segments": document["segments"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
