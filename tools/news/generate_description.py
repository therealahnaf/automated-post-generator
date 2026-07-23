#!/usr/bin/env python3
"""Generate a high-stakes news-style social description from source text."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from .post_language import POST_LANGUAGES, read_post_language
except ImportError:
    from post_language import POST_LANGUAGES, read_post_language


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


TEXT_GENERATION_MODEL = "gpt-5.6-luna"
DESCRIPTION_SEPARATOR = "---"
MAX_ENGLISH_DESCRIPTION_CHARACTERS = 1300
MAX_BANGLA_DESCRIPTION_CHARACTERS = 700
MAX_COMBINED_DESCRIPTION_CHARACTERS = 2200

SYSTEM_INSTRUCTIONS = """You are a high-stakes newsroom copy editor for Bits Today.
Write urgent, dramatic, consequence-first social post descriptions from only the
supplied source material. Make the story feel important and hard to ignore, but
do not add facts, dates, allegations, figures, locations, background context, or
quotes that are not present in the source material. Preserve attribution and
uncertainty. If the source is short, incomplete, or truncated, write a shorter
description and do not complete unfinished clauses. Output plain paragraphs
only, with no headings, labels, bullets, hashtags, or markdown.
"""

BANGLA_SYSTEM_INSTRUCTIONS = """You are the Bangla-language copy editor for Bits Today.
Translate and summarize the supplied English news description into natural,
clear Bangla. Preserve the central actor, action, important names, numbers,
attribution, and uncertainty. Use only facts present in the English description.
Do not add background, implications, predictions, certainty, or dramatic claims.
Write one or two concise paragraphs that are shorter than the English version.
Use Bangla script for the prose while retaining proper nouns in English when
that is clearer. Output only the Bangla copy, with no heading, language label,
separator, bullets, hashtags, or markdown.
"""

FEW_SHOT_EXAMPLES = [
    {
        "source": (
            "Trump said Netanyahu will not be arrested if he visits the United "
            "States. Mamdani said he was looking into whether New York City "
            "could arrest Netanyahu during a September UN General Assembly "
            "visit. Netanyahu has been the subject of an ICC arrest warrant "
            "since November 2024 over alleged war crimes in Gaza. The US does "
            "not recognise ICC jurisdiction."
        ),
        "description": """President Donald Trump has said Israeli Prime Minister Benjamin Netanyahu "will not be arrested" if he visits the United States. Trump made the comment in a Truth Social post on Monday, days after New York City Mayor Zohran Mamdani said he was looking into whether his administration could arrest Netanyahu if he visits the city in September for the UN General Assembly. Netanyahu has been the subject of an International Criminal Court arrest warrant since November 2024 over alleged war crimes in Gaza. Trump said Netanyahu "will not be arrested, in any way, shape, or form while in the United States of America," adding that Netanyahu is "fighting against the Islamic Republic of Iran."

Mamdani had earlier told the New York Times that Netanyahu "belongs in The Hague," calling him "a war criminal who has been charged by the international criminal court." Mamdani said he was in "an active conversation" with the city's law department about his legal authority to direct police to detain Netanyahu, adding, "Whatever the law allows me to do in New York City, that's what we will do." He also said, "But we won't be writing our own laws to that end." During his mayoral campaign, Mamdani had pledged to arrest Netanyahu if he came to New York.

The US does not recognise the ICC's jurisdiction and is not a party to the court. Other Israeli officials, including former defence minister Yoav Gallant, and Hamas military leader Mohammed Deif have also faced ICC arrest warrants over alleged crimes in Gaza.""",
    },
    {
        "source": (
            "The UK government is providing GBP 355,000, around BDT 5.7 crore, "
            "to help more than 55,000 flood-affected people in Bangladesh. The "
            "aid covers cash, food, and hygiene supplies in six districts and "
            "adds to earlier 2026 flood support. The UK also linked Met Office "
            "data to Bangladesh's forecasting system."
        ),
        "description": """The UK government is providing GBP 355,000, approximately BDT 5.7 crore, to help more than 55,000 people affected by flooding in Bangladesh. The money will go to cash aid, food and hygiene supplies in six districts: Cox's Bazar, Bandarban, Rangamati, Chittagong, Khagrachhari and Moulvibazar. The funds are managed by Start Network and given out through local and national NGOs.

This adds to GBP 245,000, approximately BDT 3.9 crore, given in May 2026 for flood-hit communities in Sylhet. In total, the UK has now given more than GBP 600,000, approximately BDT 9.6 crore, in disaster aid to Bangladesh this year. The UK is also giving GBP 438,348, approximately BDT 7.2 crore, through the Red Cross and Red Crescent's Disaster Response Emergency Fund, covering 10 districts.

The UK has also helped link Met Office data to Bangladesh's forecasting system, giving earlier flood warnings. British High Commissioner Sarah Cooke said, "The UK stands with the people of Bangladesh affected by these devastating floods. This humanitarian assistance will help provide vital support to more than 55,000 people across some of the worst-affected areas in southeast and northeast Bangladesh." """,
    },
]


def normalize_source_text(value: str) -> str:
    """Normalize source copy while preserving ordinary punctuation."""
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set in .env or the shell.")


def make_client() -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The openai package is missing. Run: python -m pip install -r requirements.txt"
        ) from exc
    return OpenAI()


def read_tweet_text(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    items = document.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"No tweet items found in {path}.")
    tweet = items[0]
    if not isinstance(tweet, dict):
        raise ValueError(f"Invalid tweet item in {path}.")
    tweet_id = str(tweet.get("id", "")).strip()
    if not tweet_id:
        raise ValueError(f"Tweet ID is missing in {path}.")
    text = assemble_tweet_source(tweet)
    if not text:
        raise ValueError(f"Tweet text is empty in {path}.")
    return text


def tweet_author_label(tweet: dict[str, Any]) -> str:
    author = tweet.get("author")
    if not isinstance(author, dict):
        return "unknown author"
    handle = str(
        author.get("screen_name") or author.get("username") or author.get("handle") or ""
    ).strip().lstrip("@")
    name = normalize_source_text(str(author.get("name", "")))
    if handle and name:
        return f"{name} (@{handle})"
    if handle:
        return f"@{handle}"
    return name or "unknown author"


def nested_quote(tweet: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("quote", "quoted_tweet", "quotedTweet"):
        value = tweet.get(key)
        if isinstance(value, dict):
            return value
    return None


def assemble_tweet_source(tweet: dict[str, Any]) -> str:
    """Combine a same-author thread and nested quote text for description input."""
    thread = tweet.get("thread")
    statuses = thread if isinstance(thread, list) and thread else [tweet]
    valid_statuses = [status for status in statuses if isinstance(status, dict)]
    has_quote = any(nested_quote(status) is not None for status in valid_statuses)
    if len(valid_statuses) == 1 and not has_quote:
        return normalize_source_text(str(valid_statuses[0].get("text", "")))

    blocks: list[str] = []
    seen_ids: set[str] = set()

    def add_status(status: dict[str, Any], label: str, depth: int = 0) -> None:
        status_id = str(status.get("id", "")).strip()
        identity = status_id or f"object-{id(status)}"
        if identity in seen_ids:
            return
        seen_ids.add(identity)
        status_text = normalize_source_text(str(status.get("text", "")))
        if status_text:
            blocks.append(f"{label} by {tweet_author_label(status)}:\n{status_text}")
        quote = nested_quote(status)
        if quote is not None and depth < 3:
            add_status(quote, "Quoted post", depth + 1)

    for index, status in enumerate(valid_statuses):
        add_status(
            status,
            "Main post" if index == 0 else f"Thread continuation {index}",
        )
    return "\n\n".join(blocks)


def build_user_prompt(source_text: str) -> str:
    examples = []
    for index, example in enumerate(FEW_SHOT_EXAMPLES, start=1):
        examples.append(
            f"""Example {index} source:
{example["source"]}

Example {index} description:
{example["description"]}"""
        )

    return f"""Write a Bits Today description with the same paragraph structure and
source-grounded reporting discipline as the examples, but with a sharper,
higher-stakes opening. The examples teach attribution and paragraphing only. Do
not reuse their facts for the current story.

Rules for the current story:
- Use only the current source text between SOURCE START and SOURCE END.
- Treat every story as consequential. Lead with the most urgent actor, action,
  risk, conflict, number, or power shift in the source.
- The first sentence must be a high-stakes hook, not a dry restatement. Use
  strong but factual wording that makes the central move feel hard to ignore.
- Preserve named people, organizations, countries, amounts, dates, and quoted
  wording only when they appear in the current source.
- Preserve source spelling and capitalization for proper nouns.
- Keep allegations and reported claims attributed.
- Do not add background from memory or outside sources.
- Do not invent catastrophe, certainty, or consequences beyond the source. Avoid
  literal end-of-the-world wording unless the source says it.
- If the source text ends mid-thought, ignore the unfinished fragment instead
  of completing it.
- Write one to three paragraphs. Use fewer paragraphs when the source is short.
- Keep the English description under 1,300 characters so the bilingual caption
  remains publishable on every configured platform.
- Output only the description.

Few-shot examples:

{chr(10).join(examples)}

SOURCE START
{source_text}
SOURCE END"""


def build_bangla_prompt(english_description: str) -> str:
    english_description = english_description.strip()
    if not english_description:
        raise ValueError("English description cannot be empty.")
    return f"""Create a concise Bangla translation-summary of the English Bits Today
description between DESCRIPTION START and DESCRIPTION END.

Requirements:
- Preserve every important name, number, attribution, and uncertainty used in
  the English description.
- Summarize rather than translating sentence by sentence.
- Keep the Bangla version clearly shorter than the English version.
- Use one or two short paragraphs and no more than
  {MAX_BANGLA_DESCRIPTION_CHARACTERS} characters.
- Do not add a heading, language label, separator, hashtag, or markdown.
- Output only the Bangla text.

DESCRIPTION START
{english_description}
DESCRIPTION END"""


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    fragments: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if isinstance(content, dict):
                text = content.get("text")
            else:
                text = getattr(content, "text", None)
            if isinstance(text, str):
                fragments.append(text)
    if fragments:
        return "\n".join(fragments).strip()
    raise RuntimeError("OpenAI did not return description text.")


def create_description_response(
    client: Any,
    *,
    prompt: str,
    max_output_tokens: int,
) -> Any:
    request: dict[str, Any] = {
        "model": TEXT_GENERATION_MODEL,
        "input": [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ],
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": "none"},
    }
    return client.responses.create(**request)


def generate_description(
    client: Any,
    source_text: str,
    *,
    max_output_tokens: int,
) -> str:
    prompt = build_user_prompt(source_text)
    token_budgets = [max_output_tokens]
    retry_budget = min(max(max_output_tokens * 2, 1500), 4000)
    if retry_budget != max_output_tokens:
        token_budgets.append(retry_budget)

    last_error: RuntimeError | None = None
    for token_budget in token_budgets:
        response = create_description_response(
            client,
            prompt=prompt,
            max_output_tokens=token_budget,
        )
        try:
            text = extract_response_text(response)
            if len(text) > MAX_ENGLISH_DESCRIPTION_CHARACTERS:
                raise RuntimeError(
                    "OpenAI returned an English description longer than "
                    f"{MAX_ENGLISH_DESCRIPTION_CHARACTERS} characters."
                )
            return text
        except RuntimeError as exc:
            last_error = exc
    raise last_error or RuntimeError("OpenAI did not return description text.")


def contains_bangla_text(value: str) -> bool:
    return bool(re.search(r"[\u0980-\u09ff]", value))


def create_bangla_response(
    client: Any,
    *,
    prompt: str,
    max_output_tokens: int,
) -> Any:
    request: dict[str, Any] = {
        "model": TEXT_GENERATION_MODEL,
        "input": [
            {"role": "system", "content": BANGLA_SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ],
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": "none"},
    }
    return client.responses.create(**request)


def generate_bangla_summary(
    client: Any,
    english_description: str,
    *,
    max_output_tokens: int,
) -> str:
    prompt = build_bangla_prompt(english_description)
    token_budgets = [max_output_tokens]
    retry_budget = min(max(max_output_tokens * 2, 800), 2000)
    if retry_budget != max_output_tokens:
        token_budgets.append(retry_budget)

    last_error: RuntimeError | None = None
    for token_budget in token_budgets:
        response = create_bangla_response(
            client,
            prompt=prompt,
            max_output_tokens=token_budget,
        )
        try:
            text = extract_response_text(response)
            if not contains_bangla_text(text):
                raise RuntimeError("OpenAI did not return Bangla translation text.")
            if len(text) > MAX_BANGLA_DESCRIPTION_CHARACTERS:
                raise RuntimeError(
                    "OpenAI returned Bangla text longer than "
                    f"{MAX_BANGLA_DESCRIPTION_CHARACTERS} characters."
                )
            return text
        except RuntimeError as exc:
            last_error = exc
    raise last_error or RuntimeError("OpenAI did not return Bangla translation text.")


def combine_descriptions(
    english_description: str,
    bangla_description: str,
    *,
    separator: str = DESCRIPTION_SEPARATOR,
    primary_language: str = "english",
) -> str:
    english_description = english_description.strip()
    bangla_description = bangla_description.strip()
    separator = separator.strip()
    if not english_description:
        raise ValueError("English description cannot be empty.")
    if not bangla_description or not contains_bangla_text(bangla_description):
        raise ValueError("Bangla description must contain Bangla text.")
    if not separator:
        raise ValueError("Description separator cannot be empty.")
    if primary_language not in POST_LANGUAGES:
        raise ValueError(f"Unsupported primary language: {primary_language}")
    if primary_language == "bangla":
        combined = f"{bangla_description}\n\n{separator}\n\n{english_description}"
    else:
        combined = f"{english_description}\n\n{separator}\n\n{bangla_description}"
    if len(combined) > MAX_COMBINED_DESCRIPTION_CHARACTERS:
        raise ValueError(
            f"Combined bilingual description is {len(combined)} characters; "
            f"maximum is {MAX_COMBINED_DESCRIPTION_CHARACTERS}."
        )
    return combined


def generate_bilingual_description(
    client: Any,
    source_text: str,
    *,
    description_max_output_tokens: int,
    translation_max_output_tokens: int,
    primary_language: str = "english",
) -> str:
    english_description = generate_description(
        client,
        source_text,
        max_output_tokens=description_max_output_tokens,
    )
    bangla_description = generate_bangla_summary(
        client,
        english_description,
        max_output_tokens=translation_max_output_tokens,
    )
    return combine_descriptions(
        english_description,
        bangla_description,
        primary_language=primary_language,
    )



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a news-style Bits Today description from source text."
    )
    parser.add_argument("news", nargs="?", help="Source news text.")
    parser.add_argument("--input-file", type=Path, help="Read source text from a file.")
    parser.add_argument(
        "--tweet-json",
        type=Path,
        help="Read the first tweet text from fetch_tweets.py JSON output.",
    )
    parser.add_argument("--output", type=Path, help="Write the description to a file.")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=1500,
        help="Maximum output tokens for the generated description.",
    )
    parser.add_argument(
        "--translation-max-output-tokens",
        type=int,
        default=700,
        help="Maximum output tokens for the Bangla translation-summary.",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the assembled user prompt and exit without calling OpenAI.",
    )
    return parser


def read_source(args: argparse.Namespace) -> str:
    sources = [args.news is not None, args.input_file is not None, args.tweet_json is not None]
    if sum(sources) != 1:
        raise ValueError("Provide exactly one of news, --input-file, or --tweet-json.")
    if args.tweet_json:
        text = read_tweet_text(args.tweet_json)
    elif args.input_file:
        text = normalize_source_text(args.input_file.read_text(encoding="utf-8"))
    else:
        text = normalize_source_text(args.news or "")
    if not text:
        raise ValueError("Source text cannot be empty.")
    return text


def resolve_primary_language(args: argparse.Namespace) -> str:
    if args.tweet_json:
        return read_post_language(args.tweet_json)
    return "english"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.max_output_tokens <= 0:
            raise ValueError("--max-output-tokens must be greater than zero.")
        if args.translation_max_output_tokens <= 0:
            raise ValueError(
                "--translation-max-output-tokens must be greater than zero."
            )
        source_text = read_source(args)
        primary_language = resolve_primary_language(args)
        if args.print_prompt:
            print(build_user_prompt(source_text))
            return 0

        require_api_key()
        description = generate_bilingual_description(
            make_client(),
            source_text,
            description_max_output_tokens=args.max_output_tokens,
            translation_max_output_tokens=args.translation_max_output_tokens,
            primary_language=primary_language,
        )
        if not description:
            raise RuntimeError("Generated description is empty.")
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(description + "\n", encoding="utf-8")
            print(args.output.resolve())
        else:
            print(description)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
