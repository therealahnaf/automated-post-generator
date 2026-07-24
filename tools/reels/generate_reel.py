#!/usr/bin/env python3
"""Download an X video and render a branded Bits Today 9:16 reel."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
FPS = 30
MAX_DURATION = 59.5
OUTRO_DURATION = 3.0
OUTRO_MINIMUM_SOURCE_DURATION = 15.0
MIN_VIDEO_DURATION = 4.0
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
OUTRO_TITLE = "Full Video Linked in Description"
OUTRO_DETAIL = "Stay ahead with Bits Today"


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
            "format=duration:stream=index,codec_type,width,height,r_frame_rate",
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
    return {
        "duration": duration,
        "width": int(video_streams[0].get("width") or 0),
        "height": int(video_streams[0].get("height") or 0),
        "has_audio": any(
            stream.get("codec_type") == "audio"
            for stream in payload.get("streams") or []
        ),
    }


def reel_timing(source_duration: float) -> tuple[float, float]:
    total = min(source_duration, MAX_DURATION)
    if total < MIN_VIDEO_DURATION:
        raise ValueError(f"Source video must be at least {MIN_VIDEO_DURATION:.0f} seconds.")
    if source_duration < OUTRO_MINIMUM_SOURCE_DURATION:
        return round(total, 3), round(total, 3)
    outro = min(OUTRO_DURATION, total - 1.0)
    return round(total - outro, 3), round(total, 3)


def make_layers(
    directory: Path,
    *,
    headline: str,
    post_date: date,
    highlight: str,
    include_outro: bool = True,
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
        "Bits Today",
        post_date,
        None,
        highlight,
        top_y=210,
    )
    generate_post.paste_brand_logo(overlay, generate_post.DEFAULT_BRAND_LOGO)
    overlay_path = directory / "headline-overlay.png"
    overlay.save(overlay_path, "PNG", optimize=True)
    layers = {"headline": overlay_path}
    if not include_outro:
        return layers

    red = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ImageDraw.Draw(red).polygon(
        ((0, 0), (1080, 0), (1080, 360), (0, 650)),
        fill=generate_post.BRAND_CORAL,
    )
    red_path = directory / "outro-coral.png"
    red.save(red_path, "PNG", optimize=True)

    mint = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ImageDraw.Draw(mint).polygon(
        ((0, 1500), (1080, 1180), (1080, 1920), (0, 1920)),
        fill=generate_post.BRAND_MINT,
    )
    mint_path = directory / "outro-mint.png"
    mint.save(mint_path, "PNG", optimize=True)

    center = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ImageDraw.Draw(center).polygon(
        ((0, 650), (1080, 360), (1080, 1180), (0, 1500)),
        fill=(25, 29, 31, 210),
    )
    with Image.open(generate_post.DEFAULT_BRAND_LOGO) as logo_source:
        logo = logo_source.convert("RGBA")
        alpha_box = logo.getchannel("A").getbbox()
        if alpha_box:
            logo = logo.crop(alpha_box)
        logo.thumbnail((260, 260), Image.Resampling.LANCZOS)
        center.alpha_composite(logo, ((1080 - logo.width) // 2, 620))
    center_path = directory / "outro-center.png"
    center.save(center_path, "PNG", optimize=True)

    frames = directory / "typeout"
    frames.mkdir()
    title_font = generate_post.load_roboto_font(size=58, bold=True)
    detail_font = generate_post.load_roboto_font(size=42, bold=False)
    measure = ImageDraw.Draw(Image.new("RGBA", CANVAS))
    title_x = (1080 - generate_post.text_width(measure, OUTRO_TITLE, title_font)) // 2
    detail_x = (1080 - generate_post.text_width(measure, OUTRO_DETAIL, detail_font)) // 2
    for frame_index in range(round(OUTRO_DURATION * FPS)):
        elapsed = frame_index / FPS
        title_progress = max(0.0, min(1.0, (elapsed - 0.9) / 0.9))
        detail_progress = max(0.0, min(1.0, (elapsed - 1.8) / 0.8))
        visible_title = OUTRO_TITLE[: round(len(OUTRO_TITLE) * title_progress)]
        visible_detail = OUTRO_DETAIL[: round(len(OUTRO_DETAIL) * detail_progress)]
        frame = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        draw.text((title_x, 940), visible_title, font=title_font, fill=generate_post.WHITE)
        draw.text(
            (detail_x, 1080),
            visible_detail,
            font=detail_font,
            fill=generate_post.BRAND_MINT,
        )
        if title_progress < 1:
            cursor_x = title_x + generate_post.text_width(draw, visible_title, title_font)
            cursor_y, cursor_height = 950, 64
        elif detail_progress < 1:
            cursor_x = detail_x + generate_post.text_width(draw, visible_detail, detail_font)
            cursor_y, cursor_height = 1088, 48
        else:
            cursor_x, cursor_y, cursor_height = -10, 0, 0
        if cursor_height and frame_index % 12 < 8:
            draw.rounded_rectangle(
                (cursor_x + 5, cursor_y, cursor_x + 10, cursor_y + cursor_height),
                radius=2,
                fill=generate_post.BRAND_CORAL,
            )
        frame.save(frames / f"frame-{frame_index:03d}.png", "PNG", optimize=True)
    return {
        **layers,
        "coral": red_path,
        "mint": mint_path,
        "center": center_path,
        "frames": frames,
    }


def render_reel(
    source: Path,
    output: Path,
    layers: dict[str, Path],
    *,
    content_end: float,
    total_duration: float,
    has_audio: bool,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    start = content_end
    has_outro = content_end < total_duration
    base_filter = (
        "[0:v]trim=duration={total},setpts=PTS-STARTPTS,split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=35,eq=brightness=-0.20:saturation=0.85[bg2];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];"
    ).format(total=total_duration)
    if has_outro:
        filter_complex = base_filter + (
            "[1:v]format=rgba[headline];"
        "[base][headline]overlay=0:0:enable='lt(t,{start})'[v1];"
        "[2:v]format=rgba[coral];"
        "[v1][coral]overlay=0:'if(lt(t,{start}),-650,min(0,max(-650,-650+650*(t-{start})/0.55)))':"
        "enable='gte(t,{start})'[v2];"
        "[3:v]format=rgba[mint];"
        "[v2][mint]overlay=0:'if(lt(t,{start}),650,max(0,650-650*(t-{start})/0.55))':"
        "enable='gte(t,{start})'[v3];"
        "[4:v]format=rgba,fade=t=in:st={fade_start}:d=0.55:alpha=1[center];"
        "[v3][center]overlay=0:0:enable='gte(t,{start})'[v4];"
        "[5:v]setpts=PTS-STARTPTS+{start}/TB[text];"
        "[v4][text]overlay=0:0:eof_action=pass:enable='gte(t,{start})',"
        "fps=30,setsar=1,format=yuv420p[vout]"
        ).format(start=start, fade_start=start + 0.35)
    else:
        filter_complex = base_filter + (
            "[1:v]format=rgba[headline];"
            "[base][headline]overlay=0:0,"
            "fps=30,setsar=1,format=yuv420p[vout]"
        )
    command = ["ffmpeg", "-y", "-i", str(source)]
    layer_keys = (
        ("headline", "coral", "mint", "center")
        if has_outro
        else ("headline",)
    )
    for key in layer_keys:
        command.extend(["-loop", "1", "-i", str(layers[key])])
    if has_outro:
        command.extend(
            [
                "-framerate",
                str(FPS),
                "-i",
                str(layers["frames"] / "frame-%03d.png"),
            ]
        )
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
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
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


def parse_date(value: str | None) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else date.today()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tweet-json", required=True, type=Path)
    parser.add_argument("--headline", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-video", type=Path)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today.")
    parser.add_argument("--highlight", choices=("cyan", "red", "dual"))
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
        selected_format: dict[str, Any] | None = None
        if args.source_video:
            source = args.source_video.resolve()
            if not source.is_file():
                raise ValueError(f"Source video does not exist: {source}")
        else:
            selected_format = choose_video_format(tweet)
            source = output.with_name(f"{tweet['id']}-source.mp4")
            download_video(str(selected_format["url"]), source)
        source_info = probe_video(source)
        content_end, total_duration = reel_timing(float(source_info["duration"]))
        has_outro = content_end < total_duration
        with tempfile.TemporaryDirectory(prefix="bits-today-reel-") as temp_dir:
            layers = make_layers(
                Path(temp_dir),
                headline=headline,
                post_date=parse_date(args.date),
                highlight=highlight,
                include_outro=has_outro,
            )
            render_reel(
                source,
                output,
                layers,
                content_end=content_end,
                total_duration=total_duration,
                has_audio=bool(source_info["has_audio"]),
            )
        rendered_info = probe_video(output)
        metadata = {
            "workflow_type": "reel",
            "tweet_id": str(tweet["id"]),
            "source_url": str(tweet.get("url") or ""),
            "headline": headline,
            "headline_highlight": highlight,
            "source_video": str(source),
            "source_video_sha256": sha256_file(source),
            "selected_format": selected_format,
            "content_duration": content_end,
            "outro_duration": round(total_duration - content_end, 3),
            "outro_enabled": has_outro,
            "duration": rendered_info["duration"],
            "width": rendered_info["width"],
            "height": rendered_info["height"],
            "fps": FPS,
            "outro_title": OUTRO_TITLE if has_outro else None,
            "outro_detail": OUTRO_DETAIL if has_outro else None,
            "output": str(output),
            "output_sha256": sha256_file(output),
        }
        metadata_path = output.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
