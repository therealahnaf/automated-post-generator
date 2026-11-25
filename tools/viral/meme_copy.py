#!/usr/bin/env python3
"""Write a meme post's title and caption from the fetched X post and its photos.

Title: several candidates, the news headline's hard checks, then a second call picks the best (rules:
prompts/meme.md). Caption: one or two sentences that explain the joke, then a fixed credit line and hashtags,
assembled here so the credit is always exact. `--feedback` steers a rewrite; feedback that starts with
"title:" or "caption:" sets that text exactly instead.

  python tools/viral/meme_copy.py --tweet-json <tweet.json> --output <meme.json> [--feedback "<text>"]
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

from tools.news import generate_description as news_description  # noqa: E402
from tools.news import generate_carousel_copy  # noqa: E402
from tools.news_v2 import headline as headline_rules  # noqa: E402
from tools.viral import x_post_card  # noqa: E402

MODEL = "gpt-6-luna"
CANDIDATES = 6
MAX_TITLE = 75
MAX_EXPLANATION = 420
RULES = (Path(__file__).resolve().parent / "prompts" / "meme.md").read_text(encoding="utf-8")
EXTRA_BAIT = re.compile(r"\bwait for it\b|\bbreaking\b|\bspeechless\b|\bjust in\b", re.I)

JUDGE = """You are the senior editor of The Bits Today. Pick the title that makes this X post land best for a
reader scrolling Instagram. Judge, in order:
1. A reader who has never seen the post gets the joke or the point at once.
2. Funny in a dry, confident way; never bait, never cringe.
3. Accurate: nothing the post and its images do not show.
4. Short and clean.
Return only JSON: {"best": <candidate number>, "reason": "<one sentence>"}."""

CAPTION = """Write the caption under a The Bits Today meme post. In one or two plain sentences (at most 400
characters) explain the joke or give the context a reader needs: what the post shows and why it is funny or
notable. State only what the post and its images show, plus widely known background. No emoji, no questions to
the reader, no "link in bio", no credit line (it is added for you). Start with the substance, never with
"The post", "The images show" or "The joke is". Then give 3-5 hashtags without the # sign.
Return only JSON: {"explanation": "...", "hashtags": ["AI", "..."]}."""


def post_block(status: dict) -> str:
    author = status.get("author") or {}
    text = f"POST by {author.get('name', '')} (@{author.get('screen_name', '')})\n{status.get('text', '').strip()}"
    quote = status.get("quote") if isinstance(status.get("quote"), dict) else None
    if quote:
        quoted = quote.get("author") or {}
        text += (f"\n\nQUOTING {quoted.get('name', '')} (@{quoted.get('screen_name', '')})\n"
                 f"{(quote.get('text') or '').strip()}")
    return f"X POST START\n{text}\nX POST END"


def post_images(status: dict, tweet_dir: Path) -> list[Path]:
    """The post's photos and the quoted post's, as local files (video frames for videos)."""
    downloaded = x_post_card.local_media(status, tweet_dir)
    paths = []
    for source in (status, status.get("quote") or {}):
        for entry in ((source.get("media") or {}).get("all") or [])[:4]:
            url = entry.get("thumbnail_url") if entry.get("type") in ("video", "gif") else entry.get("url")
            path = downloaded.get(entry.get("url", "")) or (x_post_card.cached_download(url, "x-media") if url else None)
            if path:
                paths.append(Path(path))
    return paths[:6]


def ask(client: Any, system: str, text: str, images: list[Path], effort: str = "medium") -> str:
    content: Any = [{"type": "input_text", "text": text}]
    for index, path in enumerate(images, start=1):
        content += [{"type": "input_text", "text": f"Image {index} from the post:"}, generate_carousel_copy.image_part(path)]
    response = client.responses.create(model=MODEL, input=[{"role": "system", "content": system},
                                                            {"role": "user", "content": content}],
                                       reasoning={"effort": effort}, max_output_tokens=4000)
    return news_description.extract_response_text(response)


# Memes may open on the person: "Someone ...", "A developer ...". The news rules count those as hedges.
PERSON_OPENER = re.compile(r"^(someone|a user|one user|an? \w+ user|a developer|a researcher|one developer)\b", re.I)


def check_title(title: str, source_text: str) -> list[str]:
    issues = headline_rules.check(title, source_text)
    if PERSON_OPENER.match(title):
        issues = [issue for issue in issues if issue != "opens with a hedge"]
    if len(title) > MAX_TITLE:
        issues.append(f"longer than {MAX_TITLE} characters")
    if EXTRA_BAIT.search(title):
        issues.append("bait phrase")
    return issues


def write_title(client: Any, status: dict, images: list[Path], feedback: str = "", previous: str = "") -> dict:
    story = post_block(status)
    source_text = story
    steer = ""
    if feedback:
        steer = (f"\n\nThe current title is: {previous}\nThe editor asked for this change: {feedback}\n"
                 "Every candidate must follow that request.")
    request = (f"{story}{steer}\n\nWrite {CANDIDATES} different candidate titles for this meme post, each following "
               "every rule; vary the angle and the joke. Return only JSON: {\"candidates\": [\"...\"]}.")
    candidates = [str(value).strip().strip('"') for value in headline_rules.parse_json(ask(client, RULES, request, images)).get("candidates", [])]
    checked = [{"title": value, "issues": check_title(value, source_text)} for value in candidates if value]
    passing = [item for item in checked if not item["issues"]]
    if not passing:
        problems = "\n".join(f"- {item['title']}: {', '.join(item['issues'])}" for item in checked)
        retry = headline_rules.parse_json(ask(client, RULES, f"{request}\n\nThese failed the hard rules; write new ones:\n{problems}", images))
        extra = [{"title": str(value).strip().strip('"')} for value in retry.get("candidates", [])]
        for item in extra:
            item["issues"] = check_title(item["title"], source_text)
        checked += extra
        passing = [item for item in checked if not item["issues"]]
    if not passing:
        raise RuntimeError("No title candidate passed the hard rules.")
    numbered = "\n".join(f"{index}. {item['title']}" for index, item in enumerate(passing, start=1))
    verdict = headline_rules.parse_json(ask(client, JUDGE, f"{story}{steer}\n\nCANDIDATES\n{numbered}", images))
    best = int(verdict.get("best", 1)) if str(verdict.get("best", "1")).isdigit() else 1
    best = best if 1 <= best <= len(passing) else 1
    return {"title": passing[best - 1]["title"], "reason": verdict.get("reason", ""), "candidates": checked}


def credit_line(status: dict) -> str:
    author = (status.get("author") or {}).get("screen_name", "")
    quote = status.get("quote") if isinstance(status.get("quote"), dict) else None
    quoted = ((quote or {}).get("author") or {}).get("screen_name", "")
    if quoted and quoted.lower() != author.lower():
        return f"Post by @{author}, quoting @{quoted} on X"
    return f"Post by @{author} on X"


def clean_hashtags(values: Any) -> list[str]:
    tags = []
    for value in values if isinstance(values, list) else []:
        tag = re.sub(r"[^A-Za-z0-9_]", "", str(value).lstrip("#"))
        if tag and tag.lower() not in {existing.lower() for existing in tags}:
            tags.append(tag)
    return (tags or ["AI"])[:5]


def write_caption(client: Any, status: dict, images: list[Path], title: str, feedback: str = "") -> dict:
    steer = f"\n\nThe editor asked for this change to the caption: {feedback}" if feedback else ""
    answer = headline_rules.parse_json(ask(client, CAPTION, f"{post_block(status)}\n\nTITLE ON THE IMAGE: {title}{steer}", images))
    explanation = " ".join(str(answer.get("explanation", "")).split())
    if not explanation:
        raise RuntimeError("The caption call returned no explanation.")
    if len(explanation) > MAX_EXPLANATION:
        explanation = explanation[:MAX_EXPLANATION].rsplit(". ", 1)[0].rstrip(".") + "."
    return {"explanation": explanation, "hashtags": clean_hashtags(answer.get("hashtags"))}


def caption_text(meme: dict, status: dict) -> str:
    tags = " ".join("#" + tag for tag in meme["hashtags"])
    return f"{meme['explanation']}\n\n{credit_line(status)}\n\n{tags}\n"


def generate(client: Any, status: dict, tweet_dir: Path, *, feedback: str = "", previous: dict | None = None) -> dict:
    """The whole meme copy. With feedback, "title: ..." / "caption: ..." set that text exactly; anything else
    rewrites the title (and the caption, if the feedback mentions it) with the feedback as an instruction."""
    images = post_images(status, tweet_dir)
    previous = previous or {}
    feedback = feedback.strip()
    exact = re.match(r"^(title|headline|caption)\s*:\s*(.+)$", feedback, re.I | re.S)
    if exact and previous:
        result = dict(previous)
        if exact.group(1).lower() == "caption":
            result["explanation"] = " ".join(exact.group(2).split())
        else:
            result["title"] = " ".join(exact.group(2).split()).strip('"')
        return result
    caption_only = bool(feedback) and bool(previous) and re.search(r"\bcaption|description\b", feedback, re.I) \
        and not re.search(r"\btitle|headline\b", feedback, re.I)
    title = {"title": previous.get("title", ""), "reason": previous.get("reason", ""), "candidates": previous.get("candidates", [])} \
        if caption_only else write_title(client, status, images, feedback, previous.get("title", ""))
    caption = write_caption(client, status, images, title["title"], feedback if caption_only else "") \
        if caption_only or not previous or re.search(r"\bcaption|description\b", feedback, re.I) or not previous.get("explanation") \
        else {"explanation": previous["explanation"], "hashtags": previous.get("hashtags", ["AI"])}
    return {**title, **caption, "model": MODEL}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--feedback", default="")
    args = parser.parse_args(argv)
    try:
        status = (json.loads(args.tweet_json.read_text(encoding="utf-8-sig")).get("items") or [{}])[0]
        previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.is_file() else None
        client = None
        if not re.match(r"^(title|headline|caption)\s*:", args.feedback.strip(), re.I) or not previous:
            news_description.require_api_key()
            client = news_description.make_client()
        result = generate(client, status, args.tweet_json.parent, feedback=args.feedback, previous=previous)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["title"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
