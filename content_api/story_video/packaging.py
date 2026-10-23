"""Source-grounded thumbnails; video titles are supplied separately in Telegram."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from string import Template

from PIL import Image

from tools.news.fetch_tweets import normalize_tweet_url

from .config import DIRECTORY, IMAGE_MODEL, Settings
from .errors import PipelineError
from .files import save_json
from .providers import OpenRouterClient, image_mime
from .source import fetch_source

THUMBNAIL_REFERENCES = (
    DIRECTORY / "assets" / "thumbnail-reference-01.png",
    DIRECTORY / "assets" / "thumbnail-reference-02.png",
)


def thumbnail_prompt(template: str, context: dict) -> str:
    return Template(template).substitute(
        tweet_context=json.dumps(context, ensure_ascii=False),
    )


def generate_thumbnail(
    provider: OpenRouterClient,
    prompt: str,
    references: tuple[bytes, bytes],
    directory: Path,
) -> dict:
    raw_file = directory / "thumbnail-generated.png"
    usage = provider.image_with_references(
        prompt, references, raw_file, resolution="2K"
    )
    with Image.open(raw_file) as image:
        if abs(image.width / image.height - 16 / 9) > 0.02:
            raise PipelineError(
                "thumbnail_ratio", "Image provider did not honor 16:9 framing."
            )
        # The model creates all artwork; this only standardizes the output size.
        with (directory / "thumbnail.png").open("xb") as output:
            image.convert("RGB").resize((1280, 720), Image.Resampling.LANCZOS).save(
                output, format="PNG"
            )
    return usage


def generate_thumbnail_from_url(
    url: str, directory: Path, settings: Settings | None = None
) -> dict:
    settings = settings or Settings.from_env()
    if not settings.api_key:
        raise PipelineError("missing_api_key", "Configure OPENROUTER_API_KEY first.")
    canonical = normalize_tweet_url(url)
    if directory.exists():
        raise FileExistsError(directory)
    validate_packaging_assets(settings)
    context = fetch_source(canonical)
    with OpenRouterClient(settings.api_key) as provider:
        return generate_packaging_from_context(context, directory, settings, provider)


def generate_packaging_from_context(
    context: dict, directory: Path, settings: Settings, provider: OpenRouterClient
) -> dict:
    """Reuse already validated source and provider session inside a video job."""
    canonical = normalize_tweet_url(context["source_url"])
    image_template, references = validate_packaging_assets(settings)
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 2,
        "status": "generating_thumbnail",
        "source_url": canonical,
        "models": {"thumbnail": IMAGE_MODEL},
        "reference_sha256": [
            hashlib.sha256(reference).hexdigest() for reference in references
        ],
        "aspect_ratio": "16:9",
        "width": 1280,
        "height": 720,
        "thumbnail_template_sha256": hashlib.sha256(
            image_template.encode()
        ).hexdigest(),
    }
    manifest = directory / "packaging.json"
    save_json(manifest, metadata)
    try:
        save_json(directory / "source.json", context)
        (directory / "thumbnail-template.md").write_text(
            image_template, encoding="utf-8"
        )
        for number, reference in enumerate(references, 1):
            (directory / f"reference-{number:02d}.png").write_bytes(reference)
        prompt = thumbnail_prompt(image_template, context)
        (directory / "thumbnail-prompt.txt").write_text(prompt, encoding="utf-8")
        metadata["thumbnail_generation"] = generate_thumbnail(
            provider, prompt, references, directory
        )
        metadata.update(status="completed", thumbnail_file="thumbnail.png")
        save_json(manifest, metadata)
        return metadata
    except Exception as exc:
        metadata.update(
            status="failed",
            error_code=exc.code
            if isinstance(exc, PipelineError)
            else "packaging_failed",
        )
        save_json(manifest, metadata)
        raise


def validate_packaging_assets(
    settings: Settings,
) -> tuple[str, tuple[bytes, bytes]]:
    """Validate both reference images and templates before any paid operation."""
    image_template = (settings.prompts_dir / "thumbnail.md").read_text(encoding="utf-8")
    references = tuple(path.read_bytes() for path in THUMBNAIL_REFERENCES)
    for reference in references:
        image_mime(reference)
    thumbnail_prompt(image_template, {})
    return image_template, references
