#!/usr/bin/env python3
"""Validate the research session's research-brief.json for news v2.

    {
      "summary": "Two to four plain sentences: what happened, who did it, why it matters.",
      "facts": [{"fact": "One verified fact.", "source": "https://... or tweet"}],
      "source_urls": ["https://..."],      # research sources actually relied on, in caption order
      "media_pages": ["https://..."],      # primary pages showing the story's subject: release, bio, product, repo
      "notes": "optional: anything the copy must be careful about"
    }

Every later step reads only this file and the fetched tweet, so it must be complete and accurate.
Exit 0 and print "valid" when it passes; exit 1 with every problem otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

MAX_FACTS = 15
MAX_SOURCES = 8
MAX_MEDIA_PAGES = 6


def is_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def problems(brief: object) -> list[str]:
    found: list[str] = []
    if not isinstance(brief, dict):
        return ["The brief must be a JSON object."]
    allowed = {"summary", "facts", "source_urls", "media_pages", "notes"}
    extra = set(brief) - allowed
    if extra:
        found.append(f"Unknown fields: {', '.join(sorted(extra))}.")
    summary = brief.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 40:
        found.append("summary must be two to four sentences (at least 40 characters).")
    facts = brief.get("facts")
    if not isinstance(facts, list) or not facts:
        found.append("facts must be a non-empty list.")
    else:
        if len(facts) > MAX_FACTS:
            found.append(f"At most {MAX_FACTS} facts.")
        for number, item in enumerate(facts, start=1):
            if not isinstance(item, dict) or set(item) != {"fact", "source"}:
                found.append(f"Fact {number} must be an object with exactly fact and source.")
                continue
            if not isinstance(item["fact"], str) or len(item["fact"].strip()) < 10:
                found.append(f"Fact {number} is empty or too short.")
            if item["source"] != "tweet" and not is_url(item["source"]):
                found.append(f"Fact {number} source must be a URL or \"tweet\".")
    for field, limit in (("source_urls", MAX_SOURCES), ("media_pages", MAX_MEDIA_PAGES)):
        values = brief.get(field, [])
        if not isinstance(values, list):
            found.append(f"{field} must be a list.")
            continue
        if len(values) > limit:
            found.append(f"At most {limit} {field}.")
        for value in values:
            if not is_url(value):
                found.append(f"{field} contains a non-URL: {value!r}.")
    if "notes" in brief and not isinstance(brief["notes"], str):
        found.append("notes must be a string.")
    return found


def load(path: Path) -> dict:
    brief = json.loads(path.read_text(encoding="utf-8-sig"))
    issues = problems(brief)
    if issues:
        raise ValueError(" ".join(issues))
    return brief


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: brief.py <research-brief.json>", file=sys.stderr)
        return 2
    try:
        brief = json.loads(Path(args[0]).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"invalid: {exc}")
        return 1
    issues = problems(brief)
    if issues:
        print("invalid:\n- " + "\n- ".join(issues))
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
