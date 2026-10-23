"""Application service orchestrating source, copy, audio, illustration and video."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from uuid import UUID

from .config import (
    AUDIO_MODEL,
    IMAGE_MODEL,
    MAX_SEGMENTS,
    MAX_WORDS,
    MIN_WORDS,
    STORY_MODEL,
    Settings,
)
from .errors import PipelineError
from .files import save_json
from .media import (
    FPS,
    HEIGHT,
    LOGO_PADDING,
    LOGO_WIDTH,
    SAMPLE_RATE,
    WIDTH,
    MediaRenderer,
)
from .packaging import generate_packaging_from_context, validate_packaging_assets
from .prompts import PromptSet
from .providers import OpenRouterClient, image_mime
from .schemas import CreateVideo
from .segmentation import narration_segments, spoken_word_count
from .source import fetch_source

Progress = Callable[[str, int, int], None]


class StoryVideoService:
    def __init__(self, settings: Settings, renderer: MediaRenderer | None = None):
        self.settings = settings
        self.renderer = renderer or MediaRenderer()

    def preflight(self) -> PromptSet:
        if not self.settings.api_key:
            raise PipelineError(
                "missing_api_key",
                "Configure OPENROUTER_API_KEY before submitting a job.",
            )
        self.renderer.preflight()
        try:
            prompts = PromptSet.load(self.settings.prompts_dir)
            prompts.story_messages({}, None)
            # Validate both image templates before any paid operation.
            samples = [
                {
                    "index": i,
                    "text": "check",
                    "tag": None,
                    "kind": "narration",
                    "active_tags": [],
                    "tag_only": False,
                }
                for i in (1, 2)
            ]
            prompts.image_prompt(samples, 0)
            prompts.image_prompt(samples, 1)
            image_mime(self.settings.reference_image.read_bytes())
            self.renderer.logo_filter(self.settings.logo_image)
            validate_packaging_assets(self.settings)
        except (OSError, ValueError, KeyError) as exc:
            raise PipelineError(
                "invalid_assets",
                "Prompt templates or the style reference are missing or invalid.",
            ) from exc
        return prompts

    def generate(self, job_id: str, request: CreateVideo, progress: Progress) -> float:
        prompts = self.preflight()
        directory = self.settings.output_dir / str(UUID(job_id))
        # An interrupted directory is kept for manual review, never implicitly reused.
        directory.mkdir(parents=True, exist_ok=False)
        for name in ("audio", "images", "prompts"):
            (directory / name).mkdir()
        for name, text in (
            ("story.md", prompts.story),
            ("image_first.md", prompts.first_image),
            ("image_next.md", prompts.next_image),
        ):
            (directory / "prompts" / name).write_text(text, encoding="utf-8")
        reference = self.settings.reference_image.read_bytes()
        (directory / "style-reference.png").write_bytes(reference)
        logo = self.settings.logo_image.read_bytes()
        logo_file = directory / "brand-logo.png"
        logo_file.write_bytes(logo)
        document = {
            "schema_version": 1,
            "job_id": job_id,
            "request": request.model_dump(mode="json"),
            "models": {
                "story": STORY_MODEL,
                "audio": AUDIO_MODEL,
                "image": IMAGE_MODEL,
            },
            "prompts_sha256": prompts.hashes(),
            "reference_sha256": hashlib.sha256(reference).hexdigest(),
            "logo_file": "brand-logo.png",
            "logo_sha256": hashlib.sha256(logo).hexdigest(),
            "logo_overlay": {
                "width_px": LOGO_WIDTH,
                "right_padding_px": LOGO_PADDING,
                "bottom_padding_px": LOGO_PADDING,
            },
            "reference_mode": "original_style_only",
            "aspect_ratio": "16:9",
            "width": WIDTH,
            "height": HEIGHT,
            "fps": FPS,
            "segments": [],
        }
        manifest = directory / "manifest.json"
        save_json(manifest, document)

        progress("fetching", 0, 0)
        source = fetch_source(request.x_url)
        save_json(directory / "source.json", source)
        progress("story", 0, 0)
        with OpenRouterClient(self.settings.api_key) as provider:
            story, metadata = provider.story(
                prompts.story_messages(source, request.instructions)
            )
            (directory / "story.txt").write_text(story + "\n", encoding="utf-8")
            words = spoken_word_count(story)
            document.update(
                story_file="story.txt", word_count=words, story_generation=metadata
            )
            save_json(manifest, document)
            if not MIN_WORDS <= words <= MAX_WORDS:
                raise PipelineError(
                    "story_length",
                    f"Story must contain {MIN_WORDS}–{MAX_WORDS} spoken words; no paid retry was made.",
                )
            try:
                boundaries, segments = narration_segments(story)
            except ValueError as exc:
                raise PipelineError(
                    "story_tags", "Story delivery tags are invalid."
                ) from exc
            if not 1 <= len(segments) <= MAX_SEGMENTS:
                raise PipelineError(
                    "segment_limit",
                    f"Story exceeds the {MAX_SEGMENTS}-segment safety limit.",
                )
            document.update(boundaries=boundaries, segments=segments)
            progress("packaging", 0, len(segments))
            packaging = generate_packaging_from_context(
                source, directory / "packaging", self.settings, provider
            )
            description = (
                packaging["title"]
                + "\n\n"
                + " ".join(segments[0]["text"].split()[:160])
                + "\n\nA philosophical reflection from Tokens For Thought. Narration and illustrations are AI-generated."
                + "\n\nSource:\n"
                + request.x_url
            )
            (directory / "description.txt").write_text(
                description + "\n", encoding="utf-8"
            )
            document.update(
                title=packaging["title"],
                title_file="packaging/title.txt",
                thumbnail_file="packaging/thumbnail.png",
                description_file="description.txt",
            )
            save_json(manifest, document)
            elapsed_samples = 0
            for position, segment in enumerate(segments):
                number, total = position + 1, len(segments)
                stem = f"segment-{number:03d}"
                progress("audio", position, total)
                segment["status"] = "generating_audio"
                save_json(manifest, document)
                mp3, wav = (
                    directory / "audio" / (stem + ".mp3"),
                    directory / "audio" / (stem + ".wav"),
                )
                segment["audio_generation"] = provider.audio(
                    segment["tts_input"], request.speech_speed, mp3
                )
                samples = self.renderer.decode_audio(mp3, wav)
                segment.update(
                    samples=samples,
                    start_seconds=elapsed_samples / SAMPLE_RATE,
                    duration_seconds=samples / SAMPLE_RATE,
                    audio_file=f"audio/{stem}.mp3",
                    pcm_file=f"audio/{stem}.wav",
                )
                elapsed_samples += samples
                segment["end_seconds"] = elapsed_samples / SAMPLE_RATE
                segment["status"] = "generating_image"
                prompt = prompts.image_prompt(segments, position)
                (directory / "prompts" / (stem + ".txt")).write_text(
                    prompt, encoding="utf-8"
                )
                segment["image_prompt_file"] = f"prompts/{stem}.txt"
                save_json(manifest, document)
                progress("images", position, total)
                segment["image_generation"] = provider.image(
                    prompt, reference, directory / "images" / (stem + ".png")
                )
                segment.update(image_file=f"images/{stem}.png", status="completed")
                save_json(manifest, document)
                progress("segments", number, total)

        progress("rendering", len(segments), len(segments))
        self.renderer.join_audio(
            [directory / item["pcm_file"] for item in segments],
            directory / "narration.wav",
        )
        duration = self.renderer.render(directory, segments, logo=logo_file)
        self.renderer.preview(directory / "video.mp4", directory / "preview.mp4")
        document.update(
            duration_seconds=duration,
            video_file="video.mp4",
            audio_file="narration.wav",
            status="completed",
            preview_file="preview.mp4",
        )
        save_json(manifest, document)
        return duration
