#!/usr/bin/env python3
"""Write the news headline: several candidates, hard checks, then a second call picks the best.

Inputs: the fetched tweet JSON and the research brief. Output: headline.json with the chosen headline, every
candidate, which checks each failed, and why the winner was picked. Rules: prompts/headline.md.

  python tools/news_v2/headline.py --tweet-json <tweet.json> --brief <research-brief.json> --output <headline.json>
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

MODEL = "gpt-6-luna"
CANDIDATES = 6
MAX_CHARACTERS = 90
RULES = (Path(__file__).resolve().parent / "prompts" / "headline.md").read_text(encoding="utf-8")
BANNED = re.compile(r"you won.t believe|\binsane\b|\bmind[- ]blowing\b|\bjaw[- ]dropping\b", re.I)
HEDGE = re.compile(r"^(a post|the post|a report|reports?\b|reportedly|someone|a user|an? x user|according to|"
                   r"here.s|this is|breaking|just in)", re.I)
EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")

JUDGE = """You are the senior editor of The Bits Today. Pick the strongest headline for this story from the
numbered candidates. Judge, in order:
1. Accurate to the sources: nothing the tweet or the facts do not support; opinions keep their speaker.
2. The hook comes first, in the first three or four words.
3. Names what readers own, play or use, by the name they know (not an internal model code).
4. States the real stakes and the hardest number.
5. Bold and consequence-first without clickbait; plain English a non-specialist understands.
Return only JSON: {"best": <candidate number>, "reason": "<one sentence>"}."""


def check(headline: str, source_text: str) -> list[str]:
    """Hard rules every published headline must pass."""
    issues = []
    words = headline.split()
    if not 3 <= len(words) <= 16:
        issues.append("needs 3-16 words")
    if len(headline) > MAX_CHARACTERS:
        issues.append(f"longer than {MAX_CHARACTERS} characters")
    if "!" in headline or "?" in headline:
        issues.append("no exclamation or question marks")
    if EMOJI.search(headline):
        issues.append("no emoji")
    if BANNED.search(headline):
        issues.append("banned clickbait phrase")
    if HEDGE.search(headline):
        issues.append("opens with a hedge")
    for word in re.findall(r"[A-Za-z][A-Za-z'’.-]*", headline):
        bare = re.sub(r"[^A-Za-z]", "", word)
        # All-caps is fine for acronyms and brand names the sources write that way (GPU, NVIDIA, PHP).
        if len(bare) >= 5 and bare.isupper() and bare not in source_text:
            issues.append(f"all-caps word {word}")
    return issues


def parse_json(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"[\[{][\s\S]*[\]}]", cleaned)
        if not match:
            raise RuntimeError("The model did not return JSON.")
        return json.loads(match.group(0))


def ask(client: Any, system: str, user: str, effort: str = "medium") -> str:
    response = client.responses.create(
        model=MODEL,
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        reasoning={"effort": effort},
        max_output_tokens=4000,
    )
    return news_description.extract_response_text(response)


def story_block(tweet_text: str, brief: dict) -> str:
    facts = "\n".join(f"- {item['fact']}" for item in brief.get("facts", []))
    notes = f"\nEDITOR NOTES\n{brief['notes']}" if brief.get("notes") else ""
    return (f"TWEET AND THREAD START\n{tweet_text}\nTWEET AND THREAD END\n\n"
            f"RESEARCH SUMMARY\n{brief.get('summary', '')}\n\nVERIFIED FACTS\n{facts}{notes}")


def generate(client: Any, tweet_text: str, brief: dict, count: int = CANDIDATES) -> dict:
    story = story_block(tweet_text, brief)
    source_text = f"{tweet_text}\n{brief.get('summary', '')}\n" + "\n".join(item["fact"] for item in brief.get("facts", []))
    request = (f"{story}\n\nWrite {count} different candidate headlines for this story, each following every rule. "
               "Vary the angle: lead with different hooks. Return only JSON: {\"candidates\": [\"...\"]}.")
    candidates = [str(value).strip().strip('"') for value in parse_json(ask(client, RULES, request)).get("candidates", [])]
    checked = [{"headline": value, "issues": check(value, source_text)} for value in candidates if value]
    passing = [item for item in checked if not item["issues"]]
    if not passing:
        feedback = "\n".join(f"- {item['headline']}: {', '.join(item['issues'])}" for item in checked)
        retry = parse_json(ask(client, RULES, f"{request}\n\nThese failed the hard rules; write new ones:\n{feedback}"))
        extra = [{"headline": str(value).strip().strip('"'), "issues": []} for value in retry.get("candidates", [])]
        for item in extra:
            item["issues"] = check(item["headline"], source_text)
        checked += extra
        passing = [item for item in checked if not item["issues"]]
    if not passing:
        raise RuntimeError("No headline candidate passed the hard rules.")
    numbered = "\n".join(f"{index}. {item['headline']}" for index, item in enumerate(passing, start=1))
    verdict = parse_json(ask(client, JUDGE, f"{story}\n\nCANDIDATES\n{numbered}"))
    best = int(verdict.get("best", 1))
    if not 1 <= best <= len(passing):
        best = 1
    return {"headline": passing[best - 1]["headline"], "reason": verdict.get("reason", ""),
            "candidates": checked, "model": MODEL}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tweet-json", type=Path, required=True)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        brief = json.loads(args.brief.read_text(encoding="utf-8-sig"))
        tweet_text = news_description.read_tweet_text(args.tweet_json)
        news_description.require_api_key()
        result = generate(news_description.make_client(), tweet_text, brief)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["headline"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
