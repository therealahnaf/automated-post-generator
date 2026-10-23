"""Generate an expressive MP3 with Fish Audio Drama 3 through OpenRouter.

Install dependencies: py -m pip install requests python-dotenv
Run the built-in emotional sample:
    py experiments/drama3_tts/generate_audio.py
Or supply your own direction-tagged text:
    py experiments/drama3_tts/generate_audio.py --text "[excited] It worked! [laughs] Finally!"
Use --text-file script.txt for longer scripts, --voice for a Fish voice ID,
--speed 1.15 for 15% faster speech, and --dry-run to inspect the payload
without calling the API. Speed defaults to 1.0. Numeric speed is sent as
Fish Audio's prosody.speed through OpenRouter's provider options; it is not
converted into a natural-language direction or applied by local processing.

Directions belong inline: [excited], [laughs], [sighs], [short pause].
Word-level delivery supports paired tags, e.g. <whisper>listen closely</whisper>.
The key is read from OPENROUTER_API_KEY in the environment or root .env.
The model is fixed; there are no automatic retries or fallback models.

References:
https://docs.fish.audio/features/text-to-speech/drama-3
https://openrouter.ai/docs/guides/overview/multimodal/tts
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://openrouter.ai/api/v1/audio/speech"
MODEL = "fish-audio/drama-3"
# Adrian: public EN-US voice featured in Fish Audio's Drama 3 documentation.
DEFAULT_VOICE = "bf322df2096a46f18c579d0baa36f41d"
DEFAULT_TEXT = (
    "[excited, warm and conversational] We finally got it working! "
    "[laughs] I was starting to think the computer was playing a joke on us. "
    "[short pause] [quiet, curious] Now, <whisper>listen closely.</whisper> "
    "[confident, upbeat] This is only the beginning."
)


def generate_mp3(payload: dict, output: Path, api_key: str) -> tuple[int, str | None]:
    """Make one request; expose the final file only after valid audio arrives."""
    temporary: Path | None = None
    try:
        with requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "audio/mpeg"},
            json=payload,
            stream=True,
            timeout=(10, 180),
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                message = response.text.replace(api_key, "[REDACTED]")[:1200]
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {message}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type not in {"audio/mpeg", "audio/mp3"}:
                raise RuntimeError(f"Expected MP3 audio; received {content_type or 'no Content-Type'}")
            output.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            prefix = bytearray()
            with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".part", delete=False) as file:
                temporary = Path(file.name)
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        file.write(chunk)
                        size += len(chunk)
                        if len(prefix) < 3:
                            prefix.extend(chunk[: 3 - len(prefix)])
            mp3_header = prefix.startswith(b"ID3") or (
                len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0
            )
            if not mp3_header:
                raise RuntimeError("Response has no recognizable MP3 header; final file was not saved")
            temporary.rename(output)
            temporary = None
            return size, response.headers.get("X-Generation-Id")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def speed_multiplier(value: str) -> float:
    """Reject invalid multipliers before making a billable request."""
    try:
        speed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Speed must be a positive number") from exc
    if not math.isfinite(speed) or speed <= 0:
        raise argparse.ArgumentTypeError("Speed must be a positive finite number")
    return speed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="Text containing inline performance directions")
    source.add_argument("--text-file", type=Path, help="UTF-8 file containing the text to speak")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Fish Audio public voice ID (default: Adrian)")
    parser.add_argument("--instructions", help="Optional overall delivery direction, e.g. warm and conversational")
    parser.add_argument("--speed", type=speed_multiplier, default=1.0, help="Numeric speed multiplier (default: 1.0; 1.15 = 15%% faster)")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "output" / "sample.mp3")
    parser.add_argument("--dry-run", action="store_true", help="Print the payload without an API call")
    args = parser.parse_args()

    try:
        text = args.text_file.read_text(encoding="utf-8-sig") if args.text_file else (
            args.text if args.text is not None else DEFAULT_TEXT
        )
    except OSError as exc:
        parser.error(f"Cannot read input file: {exc}")
    if not text.strip():
        parser.error("Input text cannot be empty")
    output = args.output.resolve()
    if output.suffix.lower() != ".mp3":
        parser.error("Output must have an .mp3 extension")
    if output.exists():
        parser.error("Output already exists; choose another --output path")
    payload = {"model": MODEL, "input": text, "voice": args.voice, "response_format": "mp3"}
    if args.instructions:
        payload["instructions"] = args.instructions
    if args.speed != 1.0:
        payload["provider"] = {"options": {"fish-audio": {"prosody": {"speed": args.speed}}}}
    if args.dry_run:
        print(json.dumps({"endpoint": ENDPOINT, "payload": payload}, indent=2, ensure_ascii=True))
        return 0

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        parser.error("Set OPENROUTER_API_KEY in the environment or root .env")
    try:
        size, generation_id = generate_mp3(payload, output, api_key)
    except (requests.RequestException, RuntimeError, OSError) as exc:
        message = str(exc).replace(api_key, "[REDACTED]")
        print(f"Audio generation failed: {message}", file=sys.stderr)
        return 1
    print(f"Saved MP3: {output} ({size:,} bytes)")
    print(f"Requested speed: {args.speed:g}x")
    if generation_id:
        print(f"Generation ID: {generation_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
