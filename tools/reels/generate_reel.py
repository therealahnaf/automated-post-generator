#!/usr/bin/env python3
"""Download an X video and render a branded 9:16 reel for The Bits Today."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

import requests
from PIL import Image, ImageDraw

try:
    from tools.news import generate_post
except ImportError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.news import generate_post


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANVAS = (1080, 1920)
RENDER_VERSION = 7
MIN_FRAME_RATE = Fraction(1, 1)
MAX_FRAME_RATE = Fraction(240, 1)
# Leave one source frame of headroom: FFmpeg timestamps a frame on its end
# boundary, so a nominal 59.5-second output can probe a few milliseconds over.
MAX_DURATION = 59.45
OUTRO_DURATION = 3.0
OUTRO_MINIMUM_SOURCE_DURATION = 15.0
# Short source clips retain their natural duration without an outro. A single
# valid video frame is enough for that path; workflow policy does not impose a
# four-second minimum.
MIN_VIDEO_DURATION = 0.001
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
# Baked into the Board outro assets; re-render tools/reels/outro to change it.
OUTRO_TITLE = "Follow The Bits Today For More"
OUTRO_COLOR = Path(__file__).resolve().parent / "assets" / "outro-board-color.mp4"
OUTRO_ALPHA = Path(__file__).resolve().parent / "assets" / "outro-board-alpha.mp4"


def load_tweet(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items")
    tweet = items[0] if isinstance(items, list) and items else payload
    if not isinstance(tweet, dict) or not str(tweet.get("id", "")).strip():
        raise ValueError("Tweet JSON does not contain a valid primary item.")
    return payload, tweet


def video_candidates(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    media = tweet.get("media")
    if not isinstance(media, dict):
        return []
    videos = media.get("videos")
    if not isinstance(videos, list):
        videos = [
            item
            for item in media.get("all") or []
            if isinstance(item, dict) and item.get("type") in {"video", "animated_gif"}
        ]
    candidates: list[dict[str, Any]] = []
    for video_index, video in enumerate(videos):
        if not isinstance(video, dict):
            continue
        formats = video.get("formats")
        if isinstance(formats, list):
            for item in formats:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                if item.get("container") == "mp4" and valid_x_video_url(url):
                    candidates.append(
                        {
                            "url": url,
                            "bitrate": int(item.get("bitrate") or 0),
                            "video_index": video_index,
                        }
                    )
        direct_url = str(video.get("url", "")).strip()
        if valid_x_video_url(direct_url):
            candidates.append(
                {
                    "url": direct_url,
                    "bitrate": int(video.get("bitrate") or 0),
                    "video_index": video_index,
                }
            )
    unique: dict[str, dict[str, Any]] = {str(item["url"]): item for item in candidates}
    return list(unique.values())


def valid_x_video_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "video.twimg.com" or host.endswith(".video.twimg.com")
    ) and parsed.path.lower().endswith(".mp4")


def choose_video_format(tweet: dict[str, Any]) -> dict[str, Any]:
    candidates = video_candidates(tweet)
    if not candidates:
        raise ValueError("The selected X post does not contain a downloadable MP4 video.")
    preferred = [item for item in candidates if int(item["bitrate"]) <= 5_000_000]
    return max(preferred or candidates, key=lambda item: int(item["bitrate"]))


def download_video(url: str, destination: Path, *, timeout: int = 180) -> Path:
    if not valid_x_video_url(url):
        raise ValueError("Refusing to download a video outside video.twimg.com.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    total = 0
    with requests.get(
        url,
        headers={"User-Agent": "bits-today-reel-generator/1.0"},
        stream=True,
        timeout=(15, timeout),
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type and content_type not in {"video/mp4", "application/octet-stream"}:
            raise RuntimeError(f"X returned unexpected video content type {content_type}.")
        with temporary.open("wb") as target:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("X video exceeds the 300 MB download safety limit.")
                target.write(chunk)
    if total == 0:
        raise RuntimeError("X returned an empty video.")
    temporary.replace(destination)
    return destination


def probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream="
            "index,codec_type,width,height,avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe could not inspect the video.")
    payload = json.loads(result.stdout)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    video_streams = [
        stream for stream in payload.get("streams") or []
        if stream.get("codec_type") == "video"
    ]
    if not video_streams or duration < MIN_VIDEO_DURATION:
        raise ValueError(f"Source video must be at least {MIN_VIDEO_DURATION:.0f} seconds.")
    video_stream = video_streams[0]
    frame_rate, fps = read_stream_frame_rate(video_stream)
    return {
        "duration": duration,
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "frame_rate": frame_rate,
        "fps": fps,
        "has_audio": any(
            stream.get("codec_type") == "audio"
            for stream in payload.get("streams") or []
        ),
    }


def normalize_frame_rate(value: Any) -> tuple[str, float]:
    """Validate and normalize an FFmpeg frame-rate expression."""
    try:
        rate = Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid video frame rate: {value!r}.") from exc
    if not MIN_FRAME_RATE <= rate <= MAX_FRAME_RATE:
        raise ValueError(
            "Video frame rate must be between "
            f"{float(MIN_FRAME_RATE):g} and {float(MAX_FRAME_RATE):g} fps."
        )
    return f"{rate.numerator}/{rate.denominator}", float(rate)


def read_stream_frame_rate(stream: dict[str, Any]) -> tuple[str, float]:
    """Prefer the measured average rate, falling back to the nominal rate."""
    errors: list[ValueError] = []
    for field in ("avg_frame_rate", "r_frame_rate"):
        raw_value = stream.get(field)
        if raw_value in (None, ""):
            continue
        try:
            return normalize_frame_rate(raw_value)
        except ValueError as exc:
            errors.append(exc)
    if errors:
        raise ValueError("FFprobe returned no usable video frame rate.") from errors[-1]
    raise ValueError("FFprobe did not return a video frame rate.")


def frame_rates_match(left: str, right: str) -> bool:
    """Compare FFprobe rates without rejecting equivalent muxer time bases."""
    _, left_fps = normalize_frame_rate(left)
    _, right_fps = normalize_frame_rate(right)
    return abs(left_fps - right_fps) <= 0.001


def reel_timing(source_duration: float, *, include_outro: bool = True) -> tuple[float, float]:
    total = min(source_duration, MAX_DURATION)
    if total < MIN_VIDEO_DURATION:
        raise ValueError(f"Source video must be at least {MIN_VIDEO_DURATION:.0f} seconds.")
    if not include_outro or source_duration < OUTRO_MINIMUM_SOURCE_DURATION:
        return round(total, 3), round(total, 3)
    outro = min(OUTRO_DURATION, total - 1.0)
    return round(total - outro, 3), round(total, 3)


def make_layers(
    directory: Path,
    *,
    headline: str,
    post_date: date,
    highlight: str,
) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    overlay = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    gradient = Image.new("L", CANVAS, 0)
    gradient_pixels = gradient.load()
    for y in range(760):
        alpha = round(205 * (1 - y / 760) ** 1.6)
        for x in range(CANVAS[0]):
            gradient_pixels[x, y] = alpha
    overlay.paste(Image.new("RGBA", CANVAS, (0, 0, 0, 255)), (0, 0), gradient)
    generate_post.draw_brand_block(
        ImageDraw.Draw(overlay),
        headline,
        "The Bits Today",
        post_date,
        None,
        highlight,
        top_y=210,
    )
    generate_post.paste_brand_logo(overlay, generate_post.DEFAULT_BRAND_LOGO)
    overlay_path = directory / "headline-overlay.png"
    overlay.save(overlay_path, "PNG", optimize=True)
    return {"headline": overlay_path}


def render_reel(
    source: Path,
    output: Path,
    layers: dict[str, Path],
    *,
    content_end: float,
    total_duration: float,
    has_audio: bool,
    frame_rate: str,
) -> None:
    frame_rate, _ = normalize_frame_rate(frame_rate)
    output.parent.mkdir(parents=True, exist_ok=True)
    start = content_end
    has_outro = content_end < total_duration
    base_filter = (
        "[0:v]trim=duration={total},setpts=PTS-STARTPTS,split=2[bg][fg];"
        "[bg]fps={fps},"
        "scale=270:480:force_original_aspect_ratio=increase,"
        "crop=270:480,boxblur=10:2,scale=1080:1920,"
        "eq=brightness=-0.20:saturation=0.85[bg2];"
        "[fg]fps={fps},scale=1080:1920:force_original_aspect_ratio=decrease[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];"
    ).format(total=total_duration, fps=frame_rate)
    if has_outro:
        filter_complex = base_filter + (
            "[1:v]format=rgba,fade=t=out:st={start}:d=0.25:alpha=1[headline];"
            "[base][headline]overlay=0:0[v1];"
            # Copy the matte's luma plane directly so no scaler can remap its range.
            "[3:v]extractplanes=y[matte];"
            "[2:v][matte]alphamerge,format=rgba,fps={fps},setpts=PTS-STARTPTS+{start}/TB[outro];"
            "[v1][outro]overlay=0:0,"
            "fps={fps},setsar=1,format=yuv420p[vout]"
        ).format(start=start, fps=frame_rate)
    else:
        filter_complex = base_filter + (
            "[1:v]format=rgba[headline];"
            "[base][headline]overlay=0:0,"
            "fps={fps},setsar=1,format=yuv420p[vout]"
        ).format(fps=frame_rate)
    command = ["ffmpeg", "-y", "-i", str(source), "-loop", "1", "-i", str(layers["headline"])]
    if has_outro:
        command.extend(["-i", str(OUTRO_COLOR), "-i", str(OUTRO_ALPHA)])
    command.extend(["-filter_complex", filter_complex, "-map", "[vout]"])
    if has_audio:
        command.extend(
            [
                "-map",
                "0:a:0",
                "-af",
                f"atrim=duration={total_duration},asetpts=PTS-STARTPTS,"
                f"afade=t=out:st={max(0, total_duration - 0.5)}:d=0.5",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
            ]
        )
    else:
        command.append("-an")
    command.extend(
        [
            "-t",
            str(total_duration),
            "-r",
            frame_rate,
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:] or "ffmpeg failed to render the reel.")


@contextmanager
def output_lock(output: Path) -> Iterator[None]:
    """Serialize renderers targeting the same final MP4."""
    lock_path = output.with_suffix(output.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(
                    f"Waiting for the active reel render to finish: {output}",
                    file=sys.stderr,
                    flush=True,
                )
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        else:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                print(
                    f"Waiting for the active reel render to finish: {output}",
                    file=sys.stderr,
                    flush=True,
                )
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            else:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def atomic_render_reel(
    source: Path,
    output: Path,
    layers: dict[str, Path],
    *,
    content_end: float,
    total_duration: float,
    has_audio: bool,
    frame_rate: str,
) -> None:
    """Render beside the destination and expose it only after FFmpeg succeeds."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.stem}.",
        suffix=".incomplete.mp4",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        render_reel(
            source,
            temporary,
            layers,
            content_end=content_end,
            total_duration=total_duration,
            has_audio=has_audio,
            frame_rate=frame_rate,
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def parse_date(value: str | None) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else date.today()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def outro_sha256() -> list[str]:
    return [sha256_file(OUTRO_COLOR), sha256_file(OUTRO_ALPHA)]


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=".incomplete.json",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=False, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reusable_render(
    output: Path,
    *,
    tweet_id: str,
    headline: str,
    highlight: str,
    post_date: date,
    source: Path | None,
    selected_format: dict[str, Any] | None,
    include_outro: bool,
) -> dict[str, Any] | None:
    metadata_path = output.with_suffix(".json")
    if not output.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            return None
        expected = {
            "render_version": RENDER_VERSION,
            "outro_sha256": outro_sha256(),
            "workflow_type": "reel",
            "tweet_id": tweet_id,
            "headline": headline,
            "headline_highlight": highlight,
            "post_date": post_date.isoformat(),
            "outro_requested": include_outro,
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            return None
        if selected_format is not None:
            existing_format = metadata.get("selected_format")
            if not isinstance(existing_format, dict) or (
                existing_format.get("url") != selected_format.get("url")
            ):
                return None
        if source is not None:
            if metadata.get("source_video") != str(source):
                return None
            if metadata.get("source_video_sha256") != sha256_file(source):
                return None
        if metadata.get("output_sha256") != sha256_file(output):
            return None
        rendered_info = probe_video(output)
        if (
            rendered_info["width"] != CANVAS[0]
            or rendered_info["height"] != CANVAS[1]
            or rendered_info["duration"] > MAX_DURATION + 0.05
            or not frame_rates_match(rendered_info["frame_rate"], str(metadata.get("frame_rate") or ""))
            or not frame_rates_match(
                str(metadata.get("source_frame_rate") or ""),
                str(metadata.get("frame_rate") or ""),
            )
        ):
            return None
        return metadata
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tweet-json", required=True, type=Path)
    parser.add_argument("--headline", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-video", type=Path)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today.")
    parser.add_argument("--highlight", choices=("cyan", "red", "dual"))
    parser.add_argument(
        "--no-outro",
        action="store_true",
        help="Keep the full source video and omit the branded outro.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            raise RuntimeError("ffmpeg and ffprobe are required.")
        payload, tweet = load_tweet(args.tweet_json)
        headline = generate_post.normalize_news_text(args.headline)
        if not headline:
            raise ValueError("--headline cannot be empty.")
        highlight = args.highlight or str(payload.get("headline_highlight") or "dual")
        if highlight not in {"cyan", "red", "dual"}:
            raise ValueError("Tweet JSON contains an invalid headline_highlight.")
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        post_date = parse_date(args.date)
        selected_format: dict[str, Any] | None = None
        if args.source_video:
            source = args.source_video.resolve()
            if not source.is_file():
                raise ValueError(f"Source video does not exist: {source}")
        else:
            selected_format = choose_video_format(tweet)
            source = output.with_name(f"{tweet['id']}-source.mp4")
        with output_lock(output):
            existing = reusable_render(
                output,
                tweet_id=str(tweet["id"]),
                headline=headline,
                highlight=highlight,
                post_date=post_date,
                source=source if args.source_video else None,
                selected_format=selected_format,
                include_outro=not args.no_outro,
            )
            if existing is not None:
                print(json.dumps(existing, ensure_ascii=False, indent=2))
                return 0
            if not args.source_video:
                download_video(str(selected_format["url"]), source)
            source_info = probe_video(source)
            content_end, total_duration = reel_timing(
                float(source_info["duration"]), include_outro=not args.no_outro
            )
            has_outro = content_end < total_duration
            with tempfile.TemporaryDirectory(prefix="bits-today-reel-") as temp_dir:
                layers = make_layers(
                    Path(temp_dir),
                    headline=headline,
                    post_date=post_date,
                    highlight=highlight,
                )
                atomic_render_reel(
                    source,
                    output,
                    layers,
                    content_end=content_end,
                    total_duration=total_duration,
                    has_audio=bool(source_info["has_audio"]),
                    frame_rate=str(source_info["frame_rate"]),
                )
            rendered_info = probe_video(output)
            if not frame_rates_match(rendered_info["frame_rate"], source_info["frame_rate"]):
                raise RuntimeError(
                    "Rendered reel frame rate does not match the source: "
                    f"{rendered_info['frame_rate']} != {source_info['frame_rate']}."
                )
            metadata = {
                "render_version": RENDER_VERSION,
                "outro_sha256": outro_sha256(),
                "workflow_type": "reel",
                "tweet_id": str(tweet["id"]),
                "source_url": str(tweet.get("url") or ""),
                "headline": headline,
                "headline_highlight": highlight,
                "post_date": post_date.isoformat(),
                "source_video": str(source),
                "source_video_sha256": sha256_file(source),
                "selected_format": selected_format,
                "content_duration": content_end,
                "outro_duration": round(total_duration - content_end, 3),
                "outro_enabled": has_outro,
                "outro_requested": not args.no_outro,
                "duration": rendered_info["duration"],
                "width": rendered_info["width"],
                "height": rendered_info["height"],
                "source_frame_rate": source_info["frame_rate"],
                "frame_rate": rendered_info["frame_rate"],
                "fps": rendered_info["fps"],
                "outro_title": OUTRO_TITLE if has_outro else None,
                "output": str(output),
                "output_sha256": sha256_file(output),
            }
            write_json_atomic(output.with_suffix(".json"), metadata)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
