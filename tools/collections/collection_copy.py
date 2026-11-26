#!/usr/bin/env python3
"""Write a collection post's title and caption from all of its posts and their images.

The title follows tools/viral/meme_copy.py: candidates, hard checks, then a judge (rules: prompts/collection.md).
The caption is one to three sentences of context, then a fixed credit line naming every poster, then hashtags.
Feedback "title: ..." or "caption: ..." sets that text exactly; other feedback rewrites with it as an instruction.

  python tools/collections/collection_copy.py --collection <id> --output <copy.json> [--feedback "<text>"]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collections import slides, store  # noqa: E402
from tools.news import generate_description as news_description  # noqa: E402
from tools.news_v2 import headline as headline_rules  # noqa: E402
from tools.viral import meme_copy, x_post_card  # noqa: E402

RULES = (Path(__file__).resolve().parent / "prompts" / "collection.md").read_text(encoding="utf-8")
CANDIDATES = 6
MAX_IMAGES = 8
MAX_EXPLANATION = 500

JUDGE = """You are the senior editor of The Bits Today. Pick the cover title that best makes a reader scrolling
Instagram want to swipe through these collected posts. Judge, in order:
1. It says what the set of posts shows as a whole, at once.
2. Accurate: nothing the posts and their images do not show.
3. Confident and, where the posts are jokes, dryly funny; never bait.
4. Short and clean.
Return only JSON: {"best": <candidate number>, "reason": "<one sentence>"}."""

CAPTION = """Write the caption under a The Bits Today carousel that collects these X posts. In one to three plain
sentences (at most 480 characters) give the context a reader needs: what the posts are about and what they show
together. State only what the posts and their images show, plus widely known background. Start with the
substance, never with "These posts" or "This carousel". No emoji, no questions to the reader, no "link in bio", no
credit line (it is added for you). Then give 3-5 hashtags without the # sign.
Return only JSON: {"explanation": "...", "hashtags": ["AI", "..."]}."""


def posts_block(items: list[store.Item]) -> str:
    blocks = [f"POST {index}\n{meme_copy.post_block(item.status())}" for index, item in enumerate(items, start=1)]
    return "COLLECTED POSTS START\n" + "\n\n".join(blocks) + "\nCOLLECTED POSTS END"


def images_of(items: list[store.Item]) -> list[Path]:
    """One image per post where it has one (a video's first frame for videos), at most MAX_IMAGES."""
    found = []
    for item in items:
        photo = slides.photos(item)
        if photo:
            found.append(photo[0])
        elif slides.main_videos(item):
            frame = item.folder / "videos" / "first-frame.jpg"
            if not frame.is_file():
                slides.frame(slides.main_videos(item)[0], frame)
            found.append(frame)
        if len(found) == MAX_IMAGES:
            break
    return found


def credit_line(items: list[store.Item]) -> str:
    handles = list(dict.fromkeys("@" + slides.handle(item) for item in items if slides.handle(item)))
    if len(handles) == 1:
        return f"Post by {handles[0]} on X"
    if len(handles) > 6:
        return f"Posts by {', '.join(handles[:6])} and others on X"
    return f"Posts by {', '.join(handles[:-1])} and {handles[-1]} on X"


def caption_text(copy: dict, items: list[store.Item]) -> str:
    tags = " ".join("#" + tag for tag in copy["hashtags"])
    return f"{copy['explanation']}\n\n{credit_line(items)}\n\n{tags}\n"


def write_title(client: Any, story: str, images: list[Path], feedback: str = "", previous: str = "") -> dict:
    steer = (f"\n\nThe current title is: {previous}\nThe editor asked for this change: {feedback}\n"
             "Every candidate must follow that request.") if feedback else ""
    request = (f"{story}{steer}\n\nWrite {CANDIDATES} different candidate cover titles for this collection, each "
               "following every rule; vary the angle. Return only JSON: {\"candidates\": [\"...\"]}.")
    values = headline_rules.parse_json(meme_copy.ask(client, RULES, request, images)).get("candidates", [])
    checked = [{"title": str(value).strip().strip('"'), "issues": meme_copy.check_title(str(value).strip().strip('"'), story)}
               for value in values if str(value).strip()]
    passing = [item for item in checked if not item["issues"]]
    if not passing:
        problems = "\n".join(f"- {item['title']}: {', '.join(item['issues'])}" for item in checked)
        retry = headline_rules.parse_json(meme_copy.ask(client, RULES, f"{request}\n\nThese failed the hard rules; write new ones:\n{problems}", images))
        extra = [{"title": str(value).strip().strip('"')} for value in retry.get("candidates", [])]
        for item in extra:
            item["issues"] = meme_copy.check_title(item["title"], story)
        checked += extra
        passing = [item for item in checked if not item["issues"]]
    if not passing:
        raise RuntimeError("No title candidate passed the hard rules.")
    numbered = "\n".join(f"{index}. {item['title']}" for index, item in enumerate(passing, start=1))
    verdict = headline_rules.parse_json(meme_copy.ask(client, JUDGE, f"{story}{steer}\n\nCANDIDATES\n{numbered}", images))
    best = int(verdict.get("best", 1)) if str(verdict.get("best", "1")).isdigit() else 1
    best = best if 1 <= best <= len(passing) else 1
    return {"title": passing[best - 1]["title"], "reason": verdict.get("reason", ""), "candidates": checked}


def write_caption(client: Any, story: str, images: list[Path], title: str, feedback: str = "") -> dict:
    steer = f"\n\nThe editor asked for this change to the caption: {feedback}" if feedback else ""
    answer = headline_rules.parse_json(meme_copy.ask(client, CAPTION, f"{story}\n\nCOVER TITLE: {title}{steer}", images))
    explanation = " ".join(str(answer.get("explanation", "")).split())
    if not explanation:
        raise RuntimeError("The caption call returned no explanation.")
    if len(explanation) > MAX_EXPLANATION:
        explanation = explanation[:MAX_EXPLANATION].rsplit(". ", 1)[0].rstrip(".") + "."
    return {"explanation": explanation, "hashtags": meme_copy.clean_hashtags(answer.get("hashtags"))}


def generate(client: Any, items: list[store.Item], *, feedback: str = "", previous: dict | None = None) -> dict:
    previous = previous or {}
    feedback = feedback.strip()
    exact = re.match(r"^(title|headline|caption)\s*:\s*(.+)$", feedback, re.I | re.S)
    if exact and previous:
        result = dict(previous)
        key = "explanation" if exact.group(1).lower() == "caption" else "title"
        result[key] = " ".join(exact.group(2).split()).strip('"')
        return result
    story, images = posts_block(items), images_of(items)
    about_caption = bool(re.search(r"\b(caption|description)\b", feedback, re.I))
    about_title = bool(re.search(r"\b(title|headline)\b", feedback, re.I))
    if previous and feedback and about_caption and not about_title:
        title = {key: previous.get(key) for key in ("title", "reason", "candidates")}
    else:
        title = write_title(client, story, images, feedback, previous.get("title", ""))
    if previous and feedback and not about_caption and previous.get("explanation"):
        caption = {"explanation": previous["explanation"], "hashtags": previous.get("hashtags", ["AI"])}
    else:
        caption = write_caption(client, story, images, title["title"], feedback if about_caption else "")
    return {**title, **caption, "model": meme_copy.MODEL}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--feedback", default="")
    args = parser.parse_args(argv)
    try:
        items = store.items(store.connect(), args.collection)
        if not items:
            raise ValueError("The collection is empty.")
        previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.is_file() else None
        client = None
        if not (previous and re.match(r"^(title|headline|caption)\s*:", args.feedback.strip(), re.I)):
            news_description.require_api_key()
            client = news_description.make_client()
        result = generate(client, items, feedback=args.feedback, previous=previous)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["title"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
