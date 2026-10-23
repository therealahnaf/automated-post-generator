"""YouTube resumable uploads with durable checkpoints and channel verification."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from .errors import PipelineError
from .review import VideoPackage, validate_title

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
CHUNK_SIZE = 8 * 1024 * 1024


@dataclass(frozen=True)
class YouTubeSettings:
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    channel_id: str

    @classmethod
    def from_env(cls):
        values = [
            os.getenv(key, "").strip()
            for key in (
                "YOUTUBE_CLIENT_ID",
                "YOUTUBE_CLIENT_SECRET",
                "YOUTUBE_REFRESH_TOKEN",
                "YOUTUBE_CHANNEL_ID",
            )
        ]
        if not all(values):
            raise PipelineError(
                "youtube_not_configured",
                "Configure the saved YouTube OAuth credentials and channel ID.",
            )
        return cls(*values)


class YouTubePublisher:
    def __init__(self, settings: YouTubeSettings):
        self.settings = settings
        credentials = Credentials(
            None,
            refresh_token=settings.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            scopes=SCOPES,
        )
        self.session = AuthorizedSession(credentials)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.session.close()

    def channel(self) -> dict:
        try:
            self.session.credentials.refresh(Request())
            response = self.session.get(
                API + "/channels",
                params={"part": "snippet", "mine": "true"},
                timeout=30,
            )
            items = (
                response.json().get("items", []) if response.status_code == 200 else []
            )
            if len(items) != 1 or items[0]["id"] != self.settings.channel_id:
                raise ValueError
            return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}
        except Exception:  # noqa: BLE001 -- sanitize OAuth/HTTP exceptions at the provider boundary
            raise PipelineError(
                "youtube_channel_check_failed",
                "YouTube authorization or channel verification failed. No new upload was attempted.",
            ) from None

    @staticmethod
    def _location(uri: str) -> str:
        parsed = urlparse(uri)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.googleapis.com", "youtube.googleapis.com"}
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or not parsed.path.startswith("/upload/youtube/")
        ):
            raise PipelineError(
                "youtube_upload_location_invalid",
                "YouTube returned an invalid resumable upload location.",
            )
        return uri

    @staticmethod
    def _video_id(response) -> str:
        try:
            video_id = response.json()["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                raise ValueError
            return video_id
        except (ValueError, KeyError, TypeError):
            raise PipelineError(
                "youtube_response_invalid",
                "YouTube upload response did not contain a valid video ID.",
            ) from None

    def publish(
        self, package: VideoPackage, state: dict, checkpoint: Callable[[dict], None]
    ) -> str:
        """Never starts another upload when a prior upload may already exist."""
        validate_title(package.title)
        channel = self.channel()
        if channel["id"] != package.channel_id:
            raise PipelineError(
                "youtube_channel_changed", "The approved channel no longer matches."
            )
        state = dict(state)
        try:
            if not state.get("video_id"):
                self._upload(package, state, checkpoint)
            video_id = state["video_id"]
            if not state.get("thumbnail_done"):
                with (package.directory / "packaging/thumbnail.png").open(
                    "rb"
                ) as image:
                    response = self.session.post(
                        UPLOAD + "/thumbnails/set",
                        params={"videoId": video_id, "uploadType": "media"},
                        data=image,
                        headers={"Content-Type": "image/png"},
                        timeout=90,
                    )
                if response.status_code != 200:
                    raise PipelineError(
                        "youtube_thumbnail_failed",
                        "The video was uploaded, but YouTube rejected the custom thumbnail. Retry only the thumbnail.",
                    )
                state["thumbnail_done"] = True
                checkpoint(state)
            return video_id
        except PipelineError:
            raise
        except Exception:  # noqa: BLE001 -- preserve checkpoints without leaking signed session URLs
            raise PipelineError(
                "youtube_upload_interrupted",
                "YouTube delivery was interrupted. Recover from the saved checkpoint; do not create another upload.",
            ) from None

    def _upload(
        self, package: VideoPackage, state: dict, checkpoint: Callable[[dict], None]
    ):
        path = package.directory / "video.mp4"
        size = path.stat().st_size
        uri = state.get("upload_uri")
        if not uri:
            if state.get("started"):
                raise PipelineError(
                    "youtube_upload_uncertain",
                    "The upload session could not be saved. Check the channel before any manual retry.",
                )
            state["started"] = True
            checkpoint(state)
            response = self.session.post(
                UPLOAD + "/videos",
                params={"part": "snippet,status", "uploadType": "resumable"},
                json={
                    "snippet": {
                        "title": package.title,
                        "description": package.description,
                        "categoryId": "28",
                        "defaultLanguage": "en",
                    },
                    "status": {
                        "privacyStatus": package.privacy,
                        "selfDeclaredMadeForKids": False,
                        "containsSyntheticMedia": True,
                    },
                },
                headers={
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "video/mp4",
                },
                timeout=60,
            )
            if response.status_code not in {200, 201} or not response.headers.get(
                "Location"
            ):
                raise PipelineError(
                    "youtube_session_failed",
                    "YouTube did not establish an upload session. No automatic new session will be created.",
                )
            uri = state["upload_uri"] = self._location(response.headers["Location"])
            checkpoint(state)
        uri = self._location(uri)
        # Query the session even after a lost response. A completed upload returns its ID.
        response = self.session.put(
            uri,
            data=b"",
            headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
            timeout=60,
        )
        offset = self._offset_or_finish(response, size, state, checkpoint)
        with path.open("rb") as video:
            while offset is not None and offset < size:
                video.seek(offset)
                chunk = video.read(min(CHUNK_SIZE, size - offset))
                response = self.session.put(
                    uri,
                    data=chunk,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}",
                    },
                    timeout=90,
                )
                next_offset = self._offset_or_finish(response, size, state, checkpoint)
                if next_offset is not None and next_offset <= offset:
                    raise PipelineError(
                        "youtube_upload_stalled",
                        "The resumable upload made no progress.",
                    )
                offset = next_offset
        if not state.get("video_id"):
            raise PipelineError(
                "youtube_upload_unconfirmed",
                "YouTube has not confirmed the video ID. Resume the same upload session.",
            )

    def _offset_or_finish(
        self, response, size: int, state: dict, checkpoint: Callable[[dict], None]
    ) -> int | None:
        if response.status_code in {200, 201}:
            state["video_id"] = self._video_id(response)
            visibility = response.json().get("status", {}).get("privacyStatus")
            if visibility in {"public", "unlisted", "private"}:
                state["visibility"] = visibility
            checkpoint(state)  # Persist publication BEFORE attempting thumbnail upload.
            return None
        if response.status_code != 308:
            raise PipelineError(
                "youtube_resume_failed",
                "YouTube could not resume the saved upload. No new upload was created.",
            )
        value = response.headers.get("Range", "")
        if not value:
            return 0
        match = re.fullmatch(r"bytes=0-(\d+)", value)
        if not match or int(match[1]) >= size:
            raise PipelineError(
                "youtube_range_invalid", "YouTube returned an invalid upload offset."
            )
        return int(match[1]) + 1
