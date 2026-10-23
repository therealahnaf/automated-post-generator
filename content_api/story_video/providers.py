"""Explicit OpenRouter adapters: fixed models, no paid retries or fallbacks."""

from __future__ import annotations

import base64
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Self

import requests
from PIL import Image

from .config import AUDIO_MODEL, IMAGE_MODEL, STORY_MODEL, VOICE
from .errors import PipelineError

BASE_URL = "https://openrouter.ai/api/v1"
MAX_AUDIO_BYTES = 64 * 1024 * 1024
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def image_mime(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as image:
            mime = FORMATS.get(image.format)
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise PipelineError(
            "invalid_image", "Image bytes could not be decoded."
        ) from exc
    if not mime:
        raise PipelineError(
            "invalid_image", "Only PNG, JPEG and WebP images are supported."
        )
    return mime


def usage_metadata(response: requests.Response, result: dict | None = None) -> dict:
    # Whitelist scalar usage metadata, not request bodies, headers or credentials.
    usage = (result or {}).get("usage") or {}
    return {
        "generation_id": response.headers.get("X-Generation-Id"),
        "usage": {
            name: value
            for name, value in usage.items()
            if name in {"cost", "prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(value, (int, float))
        },
    }


class OpenRouterClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args):
        self.session.close()

    def _post(
        self, endpoint: str, payload: dict, *, stream: bool = False
    ) -> requests.Response:
        try:
            response = self.session.post(
                BASE_URL + endpoint,
                json=payload,
                stream=stream,
                timeout=(10, 360),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise PipelineError(
                "provider_network",
                "Provider request failed; it was not automatically retried.",
            ) from exc
        if response.status_code != 200:
            code = response.status_code
            response.close()
            raise PipelineError(
                f"provider_http_{code}",
                "Provider rejected the request; it was not automatically retried.",
            )
        return response

    @staticmethod
    def _json(response: requests.Response) -> dict:
        try:
            document = response.json()
        except ValueError as exc:
            raise PipelineError(
                "provider_response", "Provider returned invalid JSON."
            ) from exc
        if not isinstance(document, dict) or document.get("error"):
            raise PipelineError(
                "provider_response", "Provider returned an invalid result."
            )
        return document

    def story(self, messages: list[dict]) -> tuple[str, dict]:
        return self._text(messages, max_tokens=12288, purpose="story")

    def packaging_copy(self, messages: list[dict]) -> tuple[str, dict]:
        return self._text(messages, max_tokens=4096, purpose="packaging")

    def _text(
        self, messages: list[dict], *, max_tokens: int, purpose: str
    ) -> tuple[str, dict]:
        payload = {
            "model": STORY_MODEL,
            "messages": messages,
            # DeepSeek defaults to high reasoning, which shares the output budget.
            # Bound it for narration so planning cannot crowd out the story.
            "max_tokens": max_tokens,
            "reasoning": {"effort": "low", "exclude": True},
            "stream": False,
            "provider": {"allow_fallbacks": False},
        }
        with self._post("/chat/completions", payload) as response:
            document = self._json(response)
            choices = document.get("choices") or []
            if (
                not choices
                or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason") != "stop"
            ):
                raise PipelineError(
                    f"{purpose}_incomplete",
                    "The text response was truncated or did not finish normally.",
                )
            text = (choices[0].get("message") or {}).get("content")
            if not isinstance(text, str) or not text.strip():
                raise PipelineError(f"{purpose}_empty", "Provider returned no text.")
            return text.strip(), usage_metadata(response, document)

    def audio(self, text: str, speed: float, destination: Path) -> dict:
        payload = {
            "model": AUDIO_MODEL,
            "input": text,
            "voice": VOICE,
            "response_format": "mp3",
            "provider": {
                "allow_fallbacks": False,
                "options": {"fish-audio": {"prosody": {"speed": speed}}},
            },
        }
        temporary = destination.with_suffix(".part")
        try:
            with self._post("/audio/speech", payload, stream=True) as response:
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if mime not in {"audio/mpeg", "audio/mp3"}:
                    raise PipelineError(
                        "audio_type", "Provider did not return MP3 audio."
                    )
                size, prefix = 0, bytearray()
                with temporary.open("xb") as output:
                    for chunk in response.iter_content(65536):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAX_AUDIO_BYTES:
                            raise PipelineError(
                                "audio_limit",
                                "A narration clip exceeded the audio size limit.",
                            )
                        if len(prefix) < 3:
                            prefix.extend(chunk[: 3 - len(prefix)])
                        output.write(chunk)
                header = prefix.startswith(b"ID3") or (
                    len(prefix) >= 2 and prefix[0] == 255 and prefix[1] & 224 == 224
                )
                if not header:
                    raise PipelineError(
                        "audio_invalid", "Provider returned invalid MP3 bytes."
                    )
                metadata = usage_metadata(response)
            temporary.replace(destination)
            return metadata
        except requests.RequestException as exc:
            raise PipelineError(
                "provider_network",
                "Audio streaming was interrupted; no automatic retry was made.",
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    def image(self, prompt: str, reference: bytes, destination: Path) -> dict:
        return self.image_with_references(prompt, [reference], destination)

    def image_with_references(
        self,
        prompt: str,
        references: Sequence[bytes],
        destination: Path,
        *,
        resolution: str = "1K",
    ) -> dict:
        if not 1 <= len(references) <= 4 or resolution not in {"1K", "2K"}:
            raise ValueError(
                "Qwen requests need 1–4 references and 1K or 2K resolution"
            )
        inputs = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_mime(reference)};base64,"
                    + base64.b64encode(reference).decode("ascii"),
                },
            }
            for reference in references
        ]
        payload = {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "n": 1,
            "resolution": resolution,
            "aspect_ratio": "16:9",
            "provider": {"allow_fallbacks": False},
            "input_references": inputs,
        }
        with self._post("/images", payload) as response:
            document = self._json(response)
            outputs = document.get("data")
            if (
                not isinstance(outputs, list)
                or len(outputs) != 1
                or not isinstance(outputs[0], dict)
            ):
                raise PipelineError(
                    "image_response", "Expected exactly one generated image."
                )
            encoded = outputs[0].get("b64_json")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise PipelineError(
                    "image_response", "Provider returned no valid base64 image."
                ) from exc
            image_mime(data)
            # Normalize the container format; keep the returned landscape artwork intact.
            with (
                Image.open(io.BytesIO(data)) as image,
                destination.open("xb") as output,
            ):
                if image.width <= image.height:
                    raise PipelineError(
                        "image_aspect_ratio",
                        "Provider ignored the requested landscape framing.",
                    )
                image.convert("RGB").save(output, format="PNG")
            return usage_metadata(response, document)
