#!/usr/bin/env python3
"""Pick the news carousel's media plan: one vision call that sees the labelled contact sheet, then the validator.

If the model's plan fails validation it gets one retry with the error; if that fails too, a deterministic default
plan is used (real photos only, logos and duplicates skipped), so this step always produces a valid plan.

  python tools/news_v2/pick_media.py --source-media <source-media.json> --headline <text> --brief <brief.json>
      --output <media-plan.json>
Writes media-plan.json and media-plan.resolved.json beside it.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.news import generate_description as news_description  # noqa: E402
from tools.news import media_plan  # noqa: E402
from tools.news_v2.headline import parse_json  # noqa: E402

MODEL = "gpt-6-luna"


def rules() -> str:
    """The media-plan rules from the news workflow, so v1's agent and v2's picker follow the same ones."""
    text = (ROOT / "tools" / "news" / "WORKFLOW.md").read_text(encoding="utf-8")
    start = text.index("Look at the contact sheet and write `media-plan.json`")
    end = text.index("Run `tools/news/media_plan.py", start)
    return text[start:end].strip()


def describe(manifest: dict) -> str:
    lines = []
    for item in manifest.get("tweet_photos", []):
        flags = (" LOGO" if item.get("logo") else "") + (f" same picture as {','.join(item['similar_to'])}" if item.get("similar_to") else "")
        lines.append(f"{item['id']}: photo from the X post, {item['size'][0]}x{item['size'][1]}{flags}")
    for item in manifest.get("images", []):
        flags = (" LOGO" if item.get("logo") else "") + (f" same picture as {','.join(item['similar_to'])}" if item.get("similar_to") else "")
        about = item.get("caption") or item.get("alt") or ""
        lines.append(f"{item['id']}: from {item['publisher']} ({item.get('page_url', '')}), "
                     f"{item['size'][0]}x{item['size'][1]}{flags}{' - ' + about if about else ''}")
    quotes = [f"{item['id']} ({item['publisher']}): {item['text']}" for item in manifest.get("quotes", [])[:25]]
    return "IMAGES\n" + "\n".join(lines or ["(none)"]) + "\n\nQUOTABLE SENTENCES\n" + "\n".join(quotes or ["(none)"])


def default_plan(manifest: dict) -> dict:
    """Real photos only: the tweet's own non-logo photos as story slides; with none, the best source image leads."""
    tweet = [item for item in manifest.get("tweet_photos", []) if not item.get("logo")]
    sources = [item for item in manifest.get("images", []) if not item.get("logo")]
    used: set[str] = set()

    def fresh(item: dict) -> bool:
        return item["id"] not in used and not (set(item.get("similar_to", [])) & used)

    cover = None
    if not tweet and sources:
        best = max(sources, key=lambda item: item["size"][0] * item["size"][1])
        cover = best["id"]
        used.add(cover)
    slides = []
    for item in tweet[1:] if tweet else []:
        if fresh(item):
            slides.append({"image": item["id"], "text": True})
            used.add(item["id"])
    if tweet:
        used.add(tweet[0]["id"])
    for item in sources:
        if len(slides) >= 4:
            break
        if fresh(item):
            slides.append({"image": item["id"], "text": True})
            used.add(item["id"])
    return {"cover": cover, "slides": slides}


def pick(client: Any, manifest: dict, headline: str, brief: dict) -> tuple[dict, str]:
    sheet = manifest.get("contact_sheet")
    content: list[dict] = [{"type": "input_text", "text": (
        f"HEADLINE\n{headline}\n\nSTORY\n{brief.get('summary', '')}\n\n{describe(manifest)}\n\n"
        "Write the media plan for this post following the rules. Return only the JSON plan object.")}]
    if sheet and Path(sheet).is_file():
        data = base64.b64encode(Path(sheet).read_bytes()).decode()
        content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}"})
    messages = [{"role": "system", "content": "You choose the real images for a The Bits Today news carousel.\n\n" + rules()},
                {"role": "user", "content": content}]
    for attempt in range(2):
        response = client.responses.create(model=MODEL, input=messages, reasoning={"effort": "medium"},
                                           max_output_tokens=3000)
        plan = parse_json(news_description.extract_response_text(response))
        try:
            media_plan.resolve(plan, manifest)
            return plan, "model"
        except ValueError as exc:
            messages.append({"role": "assistant", "content": json.dumps(plan)})
            messages.append({"role": "user", "content": f"That plan is invalid: {exc} Return a corrected plan."})
    return default_plan(manifest), "default after invalid model plans"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-media", type=Path, required=True)
    parser.add_argument("--headline", required=True)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = media_plan.load_manifest(args.source_media)
    brief = json.loads(args.brief.read_text(encoding="utf-8-sig"))
    try:
        news_description.require_api_key()
        plan, origin = pick(news_description.make_client(), manifest, args.headline, brief)
    except Exception as exc:  # The plan is never allowed to stop the post.
        plan, origin = default_plan(manifest), f"default after error: {type(exc).__name__}"
    resolved = media_plan.resolve(plan, manifest)
    args.output.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    args.output.with_name("media-plan.resolved.json").write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n",
                                                                  encoding="utf-8")
    print(json.dumps({"origin": origin, "plan": plan}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
