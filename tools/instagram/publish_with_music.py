#!/usr/bin/env python3
"""Select Instagram feed music and publish an approved photo or carousel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=True)

MAX_CAPTION_CHARACTERS = 2200
MAX_CAROUSEL_ITEMS = 10
DEFAULT_RECEIPT_DIR = (
    PROJECT_ROOT / ".automation" / "instagram-private-publish"
)
DEFAULT_SESSION_FILE = (
    PROJECT_ROOT / ".automation" / "instagram-private" / "session.json"
)
DEFAULT_OVERLAP_DURATION_MS = 30000
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
MAX_DOWNLOAD_REDIRECTS = 5
FACEBOOK_IMAGE_HOST_SUFFIXES = ("fbcdn.net", "fbsbx.com")
TRACK_FIELDS = (
    "audio_asset_id",
    "audio_cluster_id",
    "display_artist",
    "duration_in_ms",
    "highlight_start_times_in_ms",
    "id",
    "is_explicit",
    "is_instrumental",
    "music_canonical_id",
    "subtitle",
    "title",
)


@dataclass(frozen=True)
class PrivateInstagramConfig:
    expected_username: str
    session_id: str
    session_file: Path = DEFAULT_SESSION_FILE


def require_publish_confirmation(publish: bool, confirmation: str | None) -> None:
    if publish and confirmation != "yes":
        raise RuntimeError(
            "Publishing requires both --publish and the exact argument --confirm yes."
        )


def make_client(session_file: Path = DEFAULT_SESSION_FILE) -> Any:
    try:
        from instagrapi import Client
    except ImportError as exc:
        raise RuntimeError(
            "instagrapi is missing. Run: python -m pip install -r requirements.txt"
        ) from exc
    client = Client()
    if session_file.is_file():
        client.load_settings(session_file)
    return client


def load_config(
    *,
    expected_username: str = "",
) -> PrivateInstagramConfig:
    username = (
        expected_username.strip()
        or os.getenv("INSTAGRAM_PRIVATE_USERNAME", "").strip()
    )
    if not username:
        raise RuntimeError(
            "Set INSTAGRAM_PRIVATE_USERNAME or pass --expected-username."
        )
    session_id = os.getenv("INSTAGRAM_PRIVATE_SESSIONID", "").strip()
    if not session_id:
        raise RuntimeError("Set INSTAGRAM_PRIVATE_SESSIONID in .env.")
    return PrivateInstagramConfig(
        expected_username=username,
        session_id=session_id,
    )


def load_overlap_duration_ms() -> int:
    raw = os.getenv(
        "INSTAGRAM_MUSIC_OVERLAP_DURATION_MS",
        str(DEFAULT_OVERLAP_DURATION_MS),
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            "INSTAGRAM_MUSIC_OVERLAP_DURATION_MS must be a positive integer."
        ) from exc
    if value <= 0:
        raise RuntimeError(
            "INSTAGRAM_MUSIC_OVERLAP_DURATION_MS must be a positive integer."
        )
    return value


def login_client(client: Any, config: PrivateInstagramConfig) -> Any:
    # A browser cookie must be paired with the same persisted private-API
    # device/UUID profile on every run. Re-import only when the environment
    # contains a newer cookie than the loaded settings.
    loaded_session_id = current_client_session_id(client)
    if loaded_session_id != config.session_id:
        if not client.login_by_sessionid(config.session_id):
            raise RuntimeError("Instagram did not accept INSTAGRAM_PRIVATE_SESSIONID.")
    account = client.account_info()
    authenticated_username = str(getattr(account, "username", ""))
    if authenticated_username.casefold() != config.expected_username.casefold():
        raise RuntimeError(
            "Authenticated Instagram account does not match the expected username."
        )
    return account


def current_client_session_id(client: Any) -> str:
    """Return the newest private cookie Instagram supplied to this client."""
    cookies = getattr(client, "cookie_dict", {})
    if isinstance(cookies, dict):
        session_id = str(cookies.get("sessionid", "") or "").strip()
        if session_id:
            return session_id
    return str(getattr(client, "sessionid", "") or "").strip()


def synchronize_client_authorization(client: Any) -> str:
    """Keep header authentication aligned when Instagram rotates sessionid."""
    session_id = current_client_session_id(client)
    authorization_data = getattr(client, "authorization_data", None)
    if session_id and isinstance(authorization_data, dict):
        if authorization_data.get("sessionid") != session_id:
            authorization_data["sessionid"] = session_id
            private = getattr(client, "private", None)
            headers = getattr(private, "headers", None)
            if headers is not None:
                headers.update({"Authorization": client.authorization})
    return session_id


def persist_private_session_id(
    session_id: str,
    *,
    env_path: Path = PROJECT_ROOT / ".env",
) -> bool:
    """Persist a rotated private session without printing it or touching source."""
    session_id = session_id.strip()
    if not session_id:
        return False
    content = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    line = f"INSTAGRAM_PRIVATE_SESSIONID={session_id}"
    pattern = re.compile(r"^INSTAGRAM_PRIVATE_SESSIONID=.*$", re.MULTILINE)
    if pattern.search(content):
        updated = pattern.sub(line, content, count=1)
    else:
        separator = "" if not content or content.endswith("\n") else "\n"
        updated = f"{content}{separator}{line}\n"
    if updated == content:
        os.environ["INSTAGRAM_PRIVATE_SESSIONID"] = session_id
        return False
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = env_path.with_name(f".{env_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(env_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    os.environ["INSTAGRAM_PRIVATE_SESSIONID"] = session_id
    return True


def synchronize_and_persist_session(
    client: Any,
    *,
    session_file: Path = DEFAULT_SESSION_FILE,
    env_path: Path = PROJECT_ROOT / ".env",
) -> str:
    """Persist the complete stable client identity and any rotated cookie."""
    session_id = synchronize_client_authorization(client)
    if session_id:
        persist_private_session_id(session_id, env_path=env_path)

    session_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = session_file.with_name(
        f".{session_file.name}.{os.getpid()}.tmp"
    )
    try:
        client.dump_settings(temporary)
        temporary.replace(session_file)
        try:
            session_file.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()
    return session_id


def validate_caption(caption: str) -> str:
    caption = caption.strip()
    if not caption:
        raise ValueError("The Instagram caption cannot be empty.")
    if len(caption) > MAX_CAPTION_CHARACTERS:
        raise ValueError(
            f"Instagram caption is {len(caption)} characters; "
            f"maximum is {MAX_CAPTION_CHARACTERS}."
        )
    return caption


def read_caption(path: Path | None) -> str:
    if path is None or not path.is_file():
        raise ValueError("--caption-file must reference an existing UTF-8 file.")
    return validate_caption(path.read_text(encoding="utf-8"))


def validate_image_paths(
    values: list[Path],
) -> tuple[list[Path], list[tuple[int, int]]]:
    if not 1 <= len(values) <= MAX_CAROUSEL_ITEMS:
        raise ValueError(
            f"Instagram music publishing requires 1 to {MAX_CAROUSEL_ITEMS} images."
        )

    paths: list[Path] = []
    dimensions: list[tuple[int, int]] = []
    for value in values:
        path = value.resolve()
        if not path.is_file():
            raise ValueError(f"Image does not exist: {path}")
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                dimensions.append(image.size)
        except Exception as exc:
            raise ValueError(f"Invalid image file: {path}") from exc
        paths.append(path)

    first_width, first_height = dimensions[0]
    first_ratio = first_width / first_height
    for path, (width, height) in zip(paths[1:], dimensions[1:]):
        if abs((width / height) - first_ratio) > 0.001:
            raise ValueError(
                "Every carousel image must share one aspect ratio; "
                f"{path.name} is {width}x{height}, expected "
                f"{first_width}:{first_height}."
            )
    return paths, dimensions


def flatten_browser_tracks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        playlist = item.get("playlist")
        if not isinstance(playlist, dict):
            continue
        for preview in playlist.get("preview_items", []):
            if not isinstance(preview, dict):
                continue
            track = preview.get("track")
            if not isinstance(track, dict):
                continue
            if not (track.get("audio_asset_id") or track.get("id")):
                continue
            if not track.get("audio_cluster_id"):
                continue
            tracks.append(track)
    return tracks


def is_instrumental(track: dict[str, Any]) -> bool:
    if track.get("is_instrumental") is True:
        return True
    searchable = " ".join(
        str(track.get(field, "") or "")
        for field in ("title", "display_artist", "subtitle")
    )
    return bool(re.search(r"\binstrumental\b", searchable, flags=re.IGNORECASE))


def select_random_instrumental(
    tracks: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    eligible = [
        track
        for track in tracks
        if is_instrumental(track) and not bool(track.get("is_explicit"))
    ]
    if not eligible:
        raise RuntimeError(
            "Instagram returned no non-explicit tracks explicitly labeled instrumental."
        )
    selected = secrets.choice(eligible)
    return selected, len(eligible)


def eligible_instrumentals(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        track
        for track in tracks
        if is_instrumental(track) and not bool(track.get("is_explicit"))
    ]
    if not eligible:
        raise RuntimeError(
            "Instagram returned no non-explicit tracks explicitly labeled instrumental."
        )
    return sorted(
        eligible,
        key=lambda track: (
            str(track.get("audio_asset_id") or track.get("id") or ""),
            str(track.get("audio_cluster_id") or ""),
            str(track.get("title") or ""),
            str(track.get("display_artist") or ""),
        ),
    )


def select_deterministic_instrumental(
    tracks: list[dict[str, Any]],
    seed: str,
) -> tuple[dict[str, Any], int]:
    eligible = eligible_instrumentals(tracks)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(eligible)
    return eligible[index], len(eligible)


def portable_track(track: dict[str, Any]) -> dict[str, Any]:
    return {
        field: track.get(field)
        for field in TRACK_FIELDS
        if track.get(field) not in (None, "", [])
    }


def track_summary(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(track.get("title", "") or ""),
        "artist": str(track.get("display_artist", "") or ""),
        "duration_ms": track.get("duration_in_ms"),
        "explicit": bool(track.get("is_explicit")),
        "music_canonical_id": str(track.get("music_canonical_id", "") or ""),
        "audio_asset_id": str(
            track.get("audio_asset_id") or track.get("id") or ""
        ),
    }


def save_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def select_and_save_instrumental(
    client: Any,
    output: Path,
) -> dict[str, Any]:
    browser = client.music_in_feed_audio_browser()
    tracks = flatten_browser_tracks(browser)
    selected, eligible_count = select_random_instrumental(tracks)
    selection = {
        "selection_version": 1,
        "selected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selection_rule": "random_non_explicit_instrumental",
        "browser_track_count": len(tracks),
        "eligible_instrumental_count": eligible_count,
        "track": portable_track(selected),
    }
    save_json_atomic(output, selection)
    return selection


def source_fingerprint(
    image_urls: list[str],
    caption: str,
    *,
    overlap_duration_ms: int,
) -> str:
    payload = {
        "image_urls": image_urls,
        "caption": caption,
        "overlap_duration_ms": overlap_duration_ms,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def default_url_receipt_path(fingerprint: str) -> Path:
    job_id = os.getenv("TELEGRAM_WATCHER_JOB_ID", "").strip()
    stem = f"job-{job_id}" if job_id.isdigit() else fingerprint
    return DEFAULT_RECEIPT_DIR / f"{stem}.json"


def load_url_receipt(path: Path, fingerprint: str) -> dict[str, Any]:
    if not path.is_file():
        return {
            "version": 1,
            "publish_method": "music",
            "source_fingerprint": fingerprint,
        }
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("source_fingerprint") != fingerprint:
        raise RuntimeError(
            f"Instagram music receipt belongs to different content: {path}"
        )
    if receipt.get("publish_method") != "music":
        raise RuntimeError(f"Instagram music receipt has the wrong backend: {path}")
    return receipt


def load_music_selection(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        raise ValueError("--music-selection must reference an existing JSON file.")
    selection = json.loads(path.read_text(encoding="utf-8"))
    track = selection.get("track")
    if not isinstance(track, dict):
        raise ValueError("Music selection JSON contains no track object.")
    if not (track.get("audio_asset_id") or track.get("id")):
        raise ValueError("Selected music track has no audio asset ID.")
    if not track.get("audio_cluster_id"):
        raise ValueError("Selected music track has no audio cluster ID.")
    if not is_instrumental(track):
        raise ValueError("Selected music track is not labeled instrumental.")
    if bool(track.get("is_explicit")):
        raise ValueError("Selected music track is explicit.")
    return selection


def audio_start_ms(track: dict[str, Any]) -> int:
    starts = track.get("highlight_start_times_in_ms")
    if isinstance(starts, list) and starts:
        try:
            return max(0, int(starts[0]))
        except (TypeError, ValueError):
            pass
    return 0


def publish_fingerprint(
    image_paths: list[Path],
    caption: str,
    track: dict[str, Any],
    *,
    overlap_duration_ms: int,
) -> str:
    payload = {
        "images": [
            {
                "name": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in image_paths
        ],
        "caption": caption,
        "track": track_summary(track),
        "audio_start_ms": audio_start_ms(track),
        "overlap_duration_ms": overlap_duration_ms,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def default_receipt_path(fingerprint: str) -> Path:
    return DEFAULT_RECEIPT_DIR / f"{fingerprint}.json"


def load_published_receipt(
    path: Path,
    fingerprint: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("fingerprint") != fingerprint:
        raise RuntimeError(
            f"Private publish receipt belongs to different content: {path}"
        )
    return receipt if receipt.get("status") == "published" else None


def upload_with_music(
    client: Any,
    image_paths: list[Path],
    caption: str,
    track: dict[str, Any],
    *,
    overlap_duration_ms: int,
    browse_session_id: str | None = None,
    alacorn_session_id: str | None = None,
) -> Any:
    kwargs = {
        "audio_asset_start_time": audio_start_ms(track),
        "overlap_duration": overlap_duration_ms,
    }
    if browse_session_id:
        kwargs["browse_session_id"] = browse_session_id
    if alacorn_session_id:
        kwargs["alacorn_session_id"] = alacorn_session_id
    if len(image_paths) == 1:
        return client.photo_upload_with_music(
            image_paths[0],
            caption,
            track,
            **kwargs,
        )
    return client.album_upload_with_music(
        image_paths,
        caption,
        track,
        **kwargs,
    )


def media_result(media: Any) -> dict[str, Any]:
    code = str(getattr(media, "code", "") or "")
    return {
        "instagram_media_id": str(getattr(media, "id", "") or ""),
        "instagram_media_pk": str(getattr(media, "pk", "") or ""),
        "instagram_code": code,
        "instagram_permalink": (
            f"https://www.instagram.com/p/{code}/" if code else None
        ),
    }


def validate_hosted_image_url(image_url: str) -> str:
    parsed = urlparse(image_url.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not hostname:
        raise ValueError("Instagram music images must use an HTTPS Facebook CDN URL.")
    if not any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in FACEBOOK_IMAGE_HOST_SUFFIXES
    ):
        raise ValueError("Instagram music images must be hosted by Facebook's CDN.")
    return image_url.strip()


def download_image_urls(
    image_urls: list[str],
    directory: Path,
    *,
    session: requests.Session | None = None,
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    http = session or requests.Session()
    owns_session = session is None
    paths: list[Path] = []
    try:
        for index, image_url in enumerate(image_urls, start=1):
            current_url = validate_hosted_image_url(image_url)
            for redirect_count in range(MAX_DOWNLOAD_REDIRECTS + 1):
                response = http.get(
                    current_url,
                    stream=True,
                    timeout=(10, 90),
                    allow_redirects=False,
                    headers={"User-Agent": "bits-today-instagram-publisher/1.0"},
                )
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location", "").strip()
                    response.close()
                    if not location or redirect_count == MAX_DOWNLOAD_REDIRECTS:
                        raise RuntimeError("Instagram image download redirected too many times.")
                    current_url = validate_hosted_image_url(
                        urljoin(current_url, location)
                    )
                    continue
                break
            try:
                response.raise_for_status()
                raw_path = directory / f"{index:02d}.download"
                total = 0
                with raw_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError(
                                f"Instagram image exceeds {MAX_DOWNLOAD_BYTES} bytes."
                            )
                        output.write(chunk)
            finally:
                response.close()
            if total == 0:
                raise RuntimeError("Instagram image download returned an empty file.")
            destination = directory / f"{index:02d}.jpg"
            try:
                with Image.open(raw_path) as source:
                    normalized = ImageOps.exif_transpose(source).convert("RGB")
                    normalized.save(destination, "JPEG", quality=95, optimize=True)
            except Exception as exc:
                raise RuntimeError(
                    f"Instagram image URL did not return a valid image: {image_url}"
                ) from exc
            finally:
                raw_path.unlink(missing_ok=True)
            paths.append(destination)
    finally:
        if owns_session:
            http.close()
    return paths


def publish_urls_with_music(
    image_urls: list[str],
    caption: str,
    *,
    publish: bool,
    confirmation: str | None,
    receipt_file: Path | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Publish the existing URL-based workflow package through Instagrapi."""
    require_publish_confirmation(publish, confirmation)
    caption = validate_caption(caption)
    if not 1 <= len(image_urls) <= MAX_CAROUSEL_ITEMS:
        raise ValueError(
            f"Instagram music publishing requires 1 to {MAX_CAROUSEL_ITEMS} images."
        )
    image_urls = [validate_hosted_image_url(value) for value in image_urls]
    overlap_duration_ms = load_overlap_duration_ms()
    fingerprint = source_fingerprint(
        image_urls,
        caption,
        overlap_duration_ms=overlap_duration_ms,
    )
    receipt_path = receipt_file or default_url_receipt_path(fingerprint)
    receipt = load_url_receipt(receipt_path, fingerprint)
    if receipt.get("status") == "published":
        return receipt

    common = {
        "publish_method": "music",
        "source_fingerprint": fingerprint,
        "image_urls": image_urls,
        "image_count": len(image_urls),
        "caption_characters": len(caption),
        "overlap_duration_ms": overlap_duration_ms,
    }
    if not publish:
        return {"status": "validated_not_published", **common}

    config = load_config()
    instagram = client or make_client(config.session_file)
    account = login_client(instagram, config)
    synchronize_and_persist_session(
        instagram,
        session_file=config.session_file,
    )
    track = receipt.get("track")
    music_browser: dict[str, Any] | None = None
    if not isinstance(track, dict):
        music_browser = instagram.music_in_feed_audio_browser()
        synchronize_and_persist_session(
            instagram,
            session_file=config.session_file,
        )
        tracks = flatten_browser_tracks(music_browser)
        track, eligible_count = select_deterministic_instrumental(
            tracks,
            fingerprint,
        )
        track = portable_track(track)
        receipt.update(
            {
                **common,
                "selection_rule": "content_fingerprint_non_explicit_instrumental",
                "browser_track_count": len(tracks),
                "eligible_instrumental_count": eligible_count,
                "track": track,
            }
        )
        save_json_atomic(receipt_path, receipt)
    elif not is_instrumental(track) or bool(track.get("is_explicit")):
        raise RuntimeError(f"Instagram music receipt contains an invalid track: {receipt_path}")

    if music_browser is None:
        music_browser = instagram.music_in_feed_audio_browser()
        synchronize_and_persist_session(
            instagram,
            session_file=config.session_file,
        )
    browse_session_id = str(
        music_browser.get("browse_session_id", "") or ""
    ).strip()
    alacorn_session_id = str(
        music_browser.get("alacorn_session_id", "") or ""
    ).strip()

    try:
        with tempfile.TemporaryDirectory(prefix="bits-instagram-music-") as directory:
            image_paths = download_image_urls(image_urls, Path(directory))
            image_paths, dimensions = validate_image_paths(image_paths)
            media = upload_with_music(
                instagram,
                image_paths,
                caption,
                track,
                overlap_duration_ms=overlap_duration_ms,
                browse_session_id=browse_session_id or None,
                alacorn_session_id=alacorn_session_id or None,
            )
    finally:
        # Instagram can rotate or invalidate authorization during a failed
        # configure call. Preserve the resulting state for diagnosis/recovery.
        synchronize_and_persist_session(
            instagram,
            session_file=config.session_file,
        )

    published = media_result(media)
    if not published["instagram_media_id"] or not published["instagram_permalink"]:
        raise RuntimeError("Instagram private API returned no published media identity.")
    result = {
        "status": "published",
        **common,
        "instagram_username": getattr(account, "username", ""),
        "dimensions": [
            {"width": width, "height": height} for width, height in dimensions
        ],
        "track": track_summary(track),
        **published,
        "instagram_receipt_file": str(receipt_path.resolve()),
    }
    save_json_atomic(receipt_path, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        type=Path,
        action="append",
        default=[],
        help="Ordered local post image; repeat up to 10 times.",
    )
    parser.add_argument("--caption-file", type=Path)
    parser.add_argument("--music-selection", type=Path)
    parser.add_argument(
        "--select-instrumental",
        action="store_true",
        help="Select a random non-explicit instrumental from Instagram's browser.",
    )
    parser.add_argument(
        "--music-output",
        type=Path,
        help="Write the persisted track selection JSON.",
    )
    parser.add_argument(
        "--expected-username",
        default="",
        help="Authenticated Instagram username expected from the saved session.",
    )
    parser.add_argument("--receipt-file", type=Path)
    parser.add_argument("--overlap-duration-ms", type=int, default=30000)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--confirm",
        help="Must be the exact word 'yes' when --publish is supplied.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_publish_confirmation(args.publish, args.confirm)
        if args.overlap_duration_ms <= 0:
            raise ValueError("--overlap-duration-ms must be positive.")
        if args.select_instrumental:
            if args.publish:
                raise ValueError("Track selection and publishing are separate steps.")
            if args.music_output is None:
                raise ValueError("--music-output is required with --select-instrumental.")
            config = load_config(
                expected_username=args.expected_username,
            )
            client = make_client(config.session_file)
            account = login_client(client, config)
            synchronize_and_persist_session(
                client,
                session_file=config.session_file,
            )
            selection = select_and_save_instrumental(client, args.music_output)
            synchronize_and_persist_session(
                client,
                session_file=config.session_file,
            )
            print(
                json.dumps(
                    {
                        "status": "instrumental_selected_not_published",
                        "authenticated_username": getattr(account, "username", ""),
                        "music_selection": str(args.music_output.resolve()),
                        "browser_track_count": selection["browser_track_count"],
                        "eligible_instrumental_count": selection[
                            "eligible_instrumental_count"
                        ],
                        "track": track_summary(selection["track"]),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        image_paths, dimensions = validate_image_paths(args.image)
        caption = read_caption(args.caption_file)
        selection = load_music_selection(args.music_selection)
        track = selection["track"]
        common = {
            "image_count": len(image_paths),
            "images": [str(path) for path in image_paths],
            "dimensions": [
                {"width": width, "height": height}
                for width, height in dimensions
            ],
            "caption_characters": len(caption),
            "overlap_duration_ms": args.overlap_duration_ms,
            "track": track_summary(track),
        }
        if not args.publish:
            print(
                json.dumps(
                    {"status": "validated_not_published", **common},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        config = load_config(
            expected_username=args.expected_username,
        )
        fingerprint = publish_fingerprint(
            image_paths,
            caption,
            track,
            overlap_duration_ms=args.overlap_duration_ms,
        )
        receipt_path = args.receipt_file or default_receipt_path(fingerprint)
        existing = load_published_receipt(receipt_path, fingerprint)
        if existing:
            print(json.dumps(existing, ensure_ascii=False, indent=2))
            return 0

        client = make_client(config.session_file)
        account = login_client(client, config)
        synchronize_and_persist_session(
            client,
            session_file=config.session_file,
        )
        music_browser = client.music_in_feed_audio_browser()
        synchronize_and_persist_session(
            client,
            session_file=config.session_file,
        )
        browse_session_id = str(
            music_browser.get("browse_session_id", "") or ""
        ).strip()
        alacorn_session_id = str(
            music_browser.get("alacorn_session_id", "") or ""
        ).strip()
        try:
            media = upload_with_music(
                client,
                image_paths,
                caption,
                track,
                overlap_duration_ms=args.overlap_duration_ms,
                browse_session_id=browse_session_id or None,
                alacorn_session_id=alacorn_session_id or None,
            )
        finally:
            synchronize_and_persist_session(
                client,
                session_file=config.session_file,
            )
        result = {
            "status": "published",
            "fingerprint": fingerprint,
            "instagram_username": getattr(account, "username", ""),
            **common,
            **media_result(media),
            "receipt_file": str(receipt_path.resolve()),
        }
        save_json_atomic(receipt_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        safe_message = (
            str(error)
            if isinstance(error, (FileNotFoundError, RuntimeError, ValueError))
            else "Instagram private API request failed."
        )
        print(
            f"Error: {type(error).__name__}: {safe_message}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
