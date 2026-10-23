"""Measured PCM timing and browser-compatible H.264/AAC video assembly."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

from PIL import Image

from .config import DEFAULT_LOGO
from .errors import PipelineError

SAMPLE_RATE = 48000
FPS = 24
WIDTH, HEIGHT = 1280, 720
LOGO_WIDTH = 80
LOGO_PADDING = 24


class MediaRenderer:
    def __init__(self):
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")

    def preflight(self) -> None:
        if not self.ffmpeg or not self.ffprobe:
            raise PipelineError(
                "ffmpeg_missing",
                "Install ffmpeg and ffprobe on PATH before generating a video.",
            )
        result = self._run([self.ffmpeg, "-hide_banner", "-encoders"], timeout=20)
        if "libx264" not in result.stdout or " aac " not in result.stdout:
            raise PipelineError(
                "encoder_missing", "FFmpeg must include libx264 and AAC encoders."
            )

    @staticmethod
    def _run(arguments: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                arguments,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PipelineError(
                "media_processing",
                "FFmpeg processing failed; partial assets were retained.",
            ) from exc

    def decode_audio(self, source: Path, destination: Path) -> int:
        self._run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                "-i",
                str(source),
                "-vn",
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(destination),
            ]
        )
        with wave.open(str(destination), "rb") as audio:
            samples = audio.getnframes()
            if (
                audio.getframerate() != SAMPLE_RATE
                or audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or samples == 0
            ):
                raise PipelineError(
                    "audio_invalid", "Decoded narration has an invalid PCM format."
                )
        return samples

    @staticmethod
    def join_audio(sources: list[Path], destination: Path) -> None:
        if destination.exists():
            raise PipelineError(
                "output_exists", "Refusing to overwrite an existing narration."
            )
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            for source in sources:
                with wave.open(str(source), "rb") as audio:
                    if (
                        audio.getnchannels(),
                        audio.getsampwidth(),
                        audio.getframerate(),
                    ) != (1, 2, SAMPLE_RATE):
                        raise PipelineError(
                            "audio_invalid",
                            "Narration clips must share the PCM format.",
                        )
                    while chunk := audio.readframes(SAMPLE_RATE):
                        output.writeframesraw(chunk)

    def probe(self, path: Path) -> dict:
        result = self._run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            timeout=30,
        )
        return json.loads(result.stdout)

    def preview(self, source: Path, destination: Path) -> None:
        """Create a bounded Telegram review clip; never replace the full video."""
        self._run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-n",
                "-i",
                str(source),
                "-t",
                "30",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-threads",
                "2",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            timeout=180,
        )
        if (
            float(self.probe(destination)["format"]["duration"]) > 30.1
            or destination.stat().st_size > 49 * 1024 * 1024
        ):
            raise PipelineError(
                "preview_invalid",
                "Telegram preview exceeded its duration or size limit.",
            )

    @staticmethod
    def logo_filter(logo: Path) -> str:
        """Trim transparent margins, preserve alpha and keep the original ratio."""
        try:
            with Image.open(logo) as image:
                bounds = image.convert("RGBA").getchannel("A").getbbox()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise PipelineError(
                "invalid_logo", "The video logo is missing or invalid."
            ) from exc
        if not bounds:
            raise PipelineError("invalid_logo", "The video logo is fully transparent.")
        left, top, right, bottom = bounds
        return f"format=rgba,crop={right - left}:{bottom - top}:{left}:{top},scale={LOGO_WIDTH}:-1"

    def render(
        self, directory: Path, segments: list[dict], *, logo: Path = DEFAULT_LOGO
    ) -> float:
        logo_filter = self.logo_filter(logo)
        duration = sum(item["samples"] for item in segments) / SAMPLE_RATE
        if not math.isfinite(duration) or duration <= 0:
            raise PipelineError(
                "audio_invalid", "The combined audio duration is invalid."
            )
        lines = ["ffconcat version 1.0"]
        for item in segments:
            # All names are application-generated, not user-supplied paths or URLs.
            lines.extend(
                [
                    f"file 'images/segment-{item['index']:03d}.png'",
                    "option framerate 1000",
                    f"duration {item['samples'] / SAMPLE_RATE:.9f}",
                ]
            )
        lines.extend(
            [
                f"file 'images/segment-{segments[-1]['index']:03d}.png'",
                "option framerate 1000",
            ]
        )
        timeline = directory / "frames.ffconcat"
        timeline.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output, temporary = directory / "video.mp4", directory / "video.part.mp4"
        if output.exists() or temporary.exists():
            raise PipelineError(
                "output_exists", "Refusing to overwrite an existing video."
            )
        try:
            self._run(
                [
                    self.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-n",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(timeline),
                    "-i",
                    str(directory / "narration.wav"),
                    "-i",
                    str(logo),
                    "-filter_complex_threads",
                    "2",
                    "-map",
                    "[video]",
                    "-map",
                    "1:a:0",
                    "-t",
                    f"{duration:.9f}",
                    "-filter_complex",
                    (
                        f"[0:v]fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[base];"
                        f"[2:v]{logo_filter}[brand];"
                        f"[base][brand]overlay=x=main_w-overlay_w-{LOGO_PADDING}:"
                        f"y=main_h-overlay_h-{LOGO_PADDING}:eof_action=repeat:repeatlast=1:"
                        "format=auto,format=yuv420p[video]"
                    ),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-threads",
                    "2",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    str(temporary),
                ]
            )
            info = self.probe(temporary)
            streams = info.get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), {})
            audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
            actual_duration = float(info.get("format", {}).get("duration", 0))
            if (
                video.get("codec_name") != "h264"
                or audio.get("codec_name") != "aac"
                or (video.get("width"), video.get("height")) != (WIDTH, HEIGHT)
                or abs(actual_duration - duration) > 0.25
            ):
                raise PipelineError(
                    "video_invalid",
                    "Rendered video failed stream or duration validation.",
                )
            temporary.replace(output)
            return duration
        finally:
            temporary.unlink(missing_ok=True)
