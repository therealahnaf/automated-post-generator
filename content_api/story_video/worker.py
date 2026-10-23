"""Run with `python -m content_api.story_video.worker` beside the existing API."""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from .config import Settings
from .errors import PipelineError
from .pipeline import StoryVideoService
from .repository import JobRepository
from .schemas import CreateVideo

logger = logging.getLogger(__name__)


@contextmanager
def lease_heartbeat(repository: JobRepository, job_id: str, owner: str):
    stop, lost = threading.Event(), threading.Event()

    def renew():
        while not stop.wait(15):
            try:
                if not repository.heartbeat(job_id, owner):
                    lost.set()
                    return
            except Exception:  # noqa: BLE001 -- fail closed if lease renewal is unavailable
                lost.set()
                return

    thread = threading.Thread(target=renew, name="story-video-lease", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=15)


def process_next(repository: JobRepository, service: StoryVideoService) -> bool:
    owner = str(uuid4())
    job = repository.claim(owner)
    if not job:
        return False
    job_id = job["id"]
    try:
        with lease_heartbeat(repository, job_id, owner) as lost:

            def progress(stage: str, completed: int, total: int):
                if lost.is_set() or not repository.progress(
                    job_id, owner, stage, completed, total
                ):
                    raise PipelineError(
                        "worker_interrupted", "This worker no longer owns the job."
                    )

            duration = service.generate(
                job_id, CreateVideo.model_validate_json(job["request_json"]), progress
            )
            if lost.is_set():
                raise PipelineError(
                    "worker_interrupted", "This worker no longer owns the job."
                )
            repository.finish(job_id, owner, duration=duration)
        logger.info("Story video %s completed", job_id)
    except Exception as exc:  # noqa: BLE001 -- persist a safe failure at the job boundary
        code = exc.code if isinstance(exc, PipelineError) else "generation_failed"
        repository.finish(job_id, owner, error=code)
        # Never log raw provider exceptions, prompts, HTTP bodies or credentials.
        logger.error("Story video %s failed: %s (%s)", job_id, code, type(exc).__name__)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Process at most one job, then exit."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    settings = Settings.from_env()
    service = StoryVideoService(settings)
    try:
        service.preflight()
    except PipelineError as exc:
        print(json.dumps({"error": exc.code, "detail": str(exc)}))
        return 1
    repository = JobRepository(settings.output_dir, settings.queue_limit)
    try:
        while True:
            processed = process_next(repository, service)
            if args.once:
                return 0
            if not processed:
                time.sleep(2)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
