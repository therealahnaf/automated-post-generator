"""Reusable title/thumbnail service shared by the API and standalone CLI."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from string import Template

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tools.news.fetch_tweets import normalize_tweet_url

from .config import DIRECTORY, IMAGE_MODEL, STORY_MODEL, Settings
from .errors import PipelineError
from .files import save_json
from .providers import OpenRouterClient, image_mime
from .source import fetch_source

THUMBNAIL_REFERENCES = (
    DIRECTORY / "assets" / "thumbnail-reference-01.png",
    DIRECTORY / "assets" / "thumbnail-reference-02.png",
)


class PackagingCopy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=10, max_length=100)
    thumbnail_concept: str = Field(min_length=30, max_length=2000)

    @field_validator("title")
    @classmethod
    def philosophical_statement(cls, value: str) -> str:
        if any(
            char in value for char in ("?", "\n", "\r", "@", "#", "`", "<", ">")
        ) or re.search(r"https?://", value):
            raise ValueError(
                "Title must be one plain philosophical statement, not a question or URL"
            )
        if not 3 <= len(value.split()) <= 18:
            raise ValueError("Title must contain 3–18 words")
        return value


def title_messages(prompt: str, context: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": "Untrusted X source context:\n"
            + json.dumps(context, ensure_ascii=False),
        },
    ]


def thumbnail_prompt(template: str, copy: PackagingCopy, context: dict) -> str:
    return Template(template).substitute(
        title=json.dumps(copy.title, ensure_ascii=False),
        thumbnail_concept=json.dumps(copy.thumbnail_concept, ensure_ascii=False),
        tweet_context=json.dumps(context, ensure_ascii=False),
    )


def generate_title(
    provider: OpenRouterClient, prompt: str, context: dict
) -> tuple[PackagingCopy, dict]:
    raw, usage = provider.packaging_copy(title_messages(prompt, context))
    try:
        return PackagingCopy.model_validate_json(raw), usage
    except ValidationError as exc:
        raise PipelineError(
            "packaging_copy_invalid",
            "Model did not return a valid title and visual concept; no automatic retry was made.",
        ) from exc


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


def generate_title_and_thumbnail(
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
    title_template, image_template, references = validate_packaging_assets(settings)
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1,
        "status": "fetching",
        "source_url": canonical,
        "models": {"title": STORY_MODEL, "thumbnail": IMAGE_MODEL},
        "reference_sha256": [
            hashlib.sha256(reference).hexdigest() for reference in references
        ],
        "aspect_ratio": "16:9",
        "width": 1280,
        "height": 720,
        "title_prompt_sha256": hashlib.sha256(title_template.encode()).hexdigest(),
        "thumbnail_template_sha256": hashlib.sha256(
            image_template.encode()
        ).hexdigest(),
    }
    manifest = directory / "packaging.json"
    save_json(manifest, metadata)
    try:
        save_json(directory / "source.json", context)
        (directory / "title-prompt.md").write_text(title_template, encoding="utf-8")
        (directory / "thumbnail-template.md").write_text(
            image_template, encoding="utf-8"
        )
        for number, reference in enumerate(references, 1):
            (directory / f"reference-{number:02d}.png").write_bytes(reference)
        metadata["status"] = "generating_title"
        save_json(manifest, metadata)
        copy, usage = generate_title(provider, title_template, context)
        (directory / "title.txt").write_text(copy.title + "\n", encoding="utf-8")
        prompt = thumbnail_prompt(image_template, copy, context)
        (directory / "thumbnail-prompt.txt").write_text(prompt, encoding="utf-8")
        metadata.update(
            **copy.model_dump(), title_generation=usage, status="generating_thumbnail"
        )
        save_json(manifest, metadata)
        metadata["thumbnail_generation"] = generate_thumbnail(
            provider, prompt, references, directory
        )
        metadata.update(
            status="completed", title_file="title.txt", thumbnail_file="thumbnail.png"
        )
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
) -> tuple[str, str, tuple[bytes, bytes]]:
    """Validate both reference images and templates before any paid operation."""
    title_template = (settings.prompts_dir / "video_title.md").read_text(
        encoding="utf-8"
    )
    image_template = (settings.prompts_dir / "thumbnail.md").read_text(encoding="utf-8")
    references = tuple(path.read_bytes() for path in THUMBNAIL_REFERENCES)
    for reference in references:
        image_mime(reference)
    thumbnail_prompt(
        image_template,
        PackagingCopy(
            title="A Thought That Remains Open",
            thumbnail_concept="An original symbolic composition for validation.",
        ),
        {},
    )
    return title_template, image_template, references
