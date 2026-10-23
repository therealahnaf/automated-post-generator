"""Turn an X status URL into a Drama 3-ready narrated story using OpenRouter.

Usage:
    python experiments/drama3_tts/generate_story.py "https://x.com/user/status/123"
    python experiments/drama3_tts/generate_story.py URL --output experiments/drama3_tts/output/story.txt
    python experiments/drama3_tts/generate_story.py URL --instructions "Keep it under 100 words."

Run with the repository virtual environment, or install root requirements.txt.

Edit content_api/story_video/prompts/story.md to change the instructions. --dry-run fetches the
X context and prints the complete prompt payload without calling OpenRouter.
stdout contains only the story (or the dry-run payload). A saved story can be
used with generate_audio.py --text-file. This script does not generate audio.

Uses the shared free FxTwitter fetcher and OPENROUTER_API_KEY from root .env.
The model is fixed; no model fallback or automatic paid retries are performed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.news.fetch_tweets import (  # noqa: E402
    DEFAULT_API_BASE,
    fetch_thread,
    normalize_tweet_url,
    status_parts,
)
from tools.news.generate_description import assemble_tweet_source  # noqa: E402


MODEL = "deepseek/deepseek-v4-flash-0731"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
PROMPT_FILE = ROOT / "content_api" / "story_video" / "prompts" / "story.md"
CONTEXT_SECTION = "\n## X tweet context\n"
CONTEXT_PLACEHOLDER = "{{tweet_context}}"


def fetch_context(url: str) -> dict[str, str]:
    """Reuse the existing thread fetcher and description source assembler."""
    canonical_url = normalize_tweet_url(url)
    _, expected_id = status_parts(canonical_url)
    tweet = fetch_thread(
        canonical_url,
        api_base=os.getenv("FXTWITTER_API_BASE", DEFAULT_API_BASE),
        timeout=30.0,
    )
    if str(tweet.get("id", "")) != expected_id:
        raise ValueError("Fetched tweet ID does not match the requested X URL")
    if not str(tweet.get("text", "")).strip():
        raise ValueError("The requested tweet contains no source text")
    source = assemble_tweet_source(tweet)
    if not source.strip():
        raise ValueError("The fetched tweet context is empty")
    return {"source_url": canonical_url, "source_text": source}


def build_messages(
    template: str, context: dict[str, str], instructions: str | None = None
) -> list[dict[str, str]]:
    """Keep trusted writing directions in system and fetched text in user."""
    if template.count(CONTEXT_SECTION) != 1:
        raise ValueError("Prompt file must contain exactly one '## X tweet context' section")
    system, context_template = template.split(CONTEXT_SECTION, 1)
    if not system.strip() or context_template.count(CONTEXT_PLACEHOLDER) != 1:
        raise ValueError("Prompt file requires instructions and exactly one {{tweet_context}} placeholder")
    if CONTEXT_PLACEHOLDER in system:
        raise ValueError("Tweet context must be in its own section, outside the instructions")
    if instructions:
        system += "\n\nAdditional instructions from the user:\n" + instructions.strip()
    user = context_template.replace(CONTEXT_PLACEHOLDER, json.dumps(context, ensure_ascii=False, indent=2))
    return [
        {"role": "system", "content": system.strip()},
        {"role": "user", "content": "## X tweet context\n" + user.strip()},
    ]


def generate_story(messages: list[dict[str, str]], api_key: str) -> str:
    """Call only the requested model and return its complete story text."""
    response = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": MODEL, "messages": messages, "max_tokens": 4096, "stream": False},
        timeout=(10, 180),
        allow_redirects=False,
    )
    if response.status_code != 200:
        message = response.text.replace(api_key, "[REDACTED]")[:1200]
        raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {message}")
    try:
        result: Any = response.json()
    except ValueError as exc:
        raise RuntimeError("OpenRouter returned a non-JSON response") from exc
    if not isinstance(result, dict):
        raise RuntimeError("OpenRouter returned an invalid completion")
    if result.get("error"):
        raise RuntimeError(f"OpenRouter error: {str(result['error']).replace(api_key, '[REDACTED]')[:1200]}")
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("OpenRouter returned no story choices")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise RuntimeError(
            f"Story did not finish normally ({choice.get('finish_reason')}); "
            "no complete story saved and no automatic retry performed"
        )
    message = choice.get("message")
    story = message.get("content") if isinstance(message, dict) else None
    if not isinstance(story, str) or not story.strip():
        raise RuntimeError("OpenRouter returned no story text")
    return story.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="Public X/Twitter status URL")
    parser.add_argument("--prompt-file", type=Path, default=PROMPT_FILE)
    parser.add_argument("--instructions", help="Optional trusted writing instructions in addition to the prompt")
    parser.add_argument("--output", type=Path, help="Save the story as UTF-8 text as well as printing it")
    parser.add_argument("--dry-run", action="store_true", help="Fetch X context and show the prompt, without an LLM call")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not args.dry_run and not api_key:
        parser.error("Set OPENROUTER_API_KEY in the environment or root .env")
    if args.output and args.output.exists():
        parser.error("Output already exists; choose a new --output path")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        template = args.prompt_file.read_text(encoding="utf-8-sig")
        # Validate the template and URL before fetching or making a paid call.
        build_messages(template, {}, args.instructions)
        normalize_tweet_url(args.url)
        if args.output and not args.dry_run:
            args.output.parent.mkdir(parents=True, exist_ok=True)
        context = fetch_context(args.url)
        messages = build_messages(template, context, args.instructions)
        if args.dry_run:
            print(json.dumps({"model": MODEL, "messages": messages}, ensure_ascii=False, indent=2))
            return 0
        story = generate_story(messages, api_key)
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as file:
                file.write(story + "\n")
        print(story)
        return 0
    except (requests.RequestException, OSError, RuntimeError, ValueError) as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        print(f"Story generation failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
