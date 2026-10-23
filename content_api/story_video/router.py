from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse

from .config import Settings
from .delivery_worker import delivery_preflight
from .errors import IdempotencyConflict, PipelineError, QueueFull
from .pipeline import StoryVideoService
from .repository import PREFIX, JobRepository, job_view
from .review import ReviewRepository
from .schemas import CreateVideo, JobView, TelegramDecision
from .telegram import TelegramSettings


def get_settings() -> Settings:
    return Settings.from_env()


def get_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobRepository:
    return JobRepository(settings.output_dir, settings.queue_limit)


def create_router(authorize: Callable) -> APIRouter:
    router = APIRouter(
        prefix=PREFIX, tags=["Story videos"], dependencies=[Depends(authorize)]
    )

    @router.post("", response_model=JobView, status_code=202)
    def create_video(
        request: CreateVideo,
        response: Response,
        settings: Annotated[Settings, Depends(get_settings)],
        repository: Annotated[JobRepository, Depends(get_repository)],
        idempotency_key: Annotated[
            str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
        ],
    ):
        try:
            StoryVideoService(settings).preflight()
            if request.send_telegram_preview:
                delivery_preflight()
            job = repository.create(request, idempotency_key)
        except PipelineError as exc:
            raise HTTPException(503, {"code": exc.code, "message": str(exc)}) from None
        except IdempotencyConflict:
            raise HTTPException(
                409, "This Idempotency-Key was already used with different settings."
            ) from None
        except QueueFull:
            raise HTTPException(
                429, "Story video queue is full.", headers={"Retry-After": "60"}
            ) from None
        view = job_view(job)
        response.headers["Location"] = view.status_url
        response.headers["Cache-Control"] = "no-store"
        return view

    @router.get("/{job_id}", response_model=JobView)
    def get_video_job(
        job_id: UUID,
        response: Response,
        repository: Annotated[JobRepository, Depends(get_repository)],
    ):
        job = repository.get(str(job_id))
        if not job:
            raise HTTPException(404, "Story video job not found.")
        response.headers["Cache-Control"] = "no-store"
        return job_view(job)

    def completed_file(
        job_id: UUID, repository: JobRepository, filename: str, mime: str
    ) -> FileResponse:
        job = repository.get(str(job_id))
        if not job:
            raise HTTPException(404, "Story video job not found.")
        if job["status"] != "completed":
            raise HTTPException(409, "The story video is not completed.")
        path = repository.directory / str(job_id) / filename
        if not path.is_file():
            raise HTTPException(404, "The saved artifact is missing.")
        return FileResponse(
            path,
            media_type=mime,
            filename=f"{job_id}-{Path(filename).name}",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/{job_id}/video", response_class=FileResponse)
    def download_video(
        job_id: UUID, repository: Annotated[JobRepository, Depends(get_repository)]
    ):
        return completed_file(job_id, repository, "video.mp4", "video/mp4")

    @router.get("/{job_id}/manifest", response_class=FileResponse)
    def download_manifest(
        job_id: UUID, repository: Annotated[JobRepository, Depends(get_repository)]
    ):
        return completed_file(job_id, repository, "manifest.json", "application/json")

    @router.get("/{job_id}/thumbnail", response_class=FileResponse)
    def download_thumbnail(
        job_id: UUID, repository: Annotated[JobRepository, Depends(get_repository)]
    ):
        return completed_file(
            job_id, repository, "packaging/thumbnail.png", "image/png"
        )

    @router.get("/{job_id}/preview", response_class=FileResponse)
    def download_preview(
        job_id: UUID, repository: Annotated[JobRepository, Depends(get_repository)]
    ):
        return completed_file(job_id, repository, "preview.mp4", "video/mp4")

    @router.post("/{job_id}/decision")
    def decide(
        job_id: UUID,
        decision: TelegramDecision,
        repository: Annotated[JobRepository, Depends(get_repository)],
    ):
        try:
            settings = TelegramSettings.from_env()
            return ReviewRepository(repository).decide(
                str(job_id), decision, settings.allowed_users, settings.chat_id
            )
        except PipelineError as exc:
            status = 403 if exc.code == "approval_forbidden" else 409
            raise HTTPException(
                status, {"code": exc.code, "message": str(exc)}
            ) from None

    @router.post("/{job_id}/retry-delivery", status_code=202)
    def retry_delivery(
        job_id: UUID, repository: Annotated[JobRepository, Depends(get_repository)]
    ):
        try:
            ReviewRepository(repository).retry(str(job_id))
            return {
                "message": "Delivery recovery queued; existing approval rules still apply."
            }
        except PipelineError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from None

    return router
