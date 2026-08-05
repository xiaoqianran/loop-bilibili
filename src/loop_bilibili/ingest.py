"""Source refresh: discover videos, record history, enqueue subtitle jobs."""

from __future__ import annotations

import logging
from typing import Protocol, Sequence

from .database import Database
from .models import Candidate

logger = logging.getLogger(__name__)


class VideoSource(Protocol):
    name: str

    def fetch(self) -> Sequence[Candidate]:
        ...


def refresh_source(source: VideoSource, db: Database) -> dict:
    """
    Fetch candidates from source, upsert videos, save ordered discoveries,
    enqueue fetch_subtitle at most once per bvid.
    """
    run = db.start_run(source.name)
    enqueued = 0
    seen = 0
    try:
        candidates = list(source.fetch())
        with db.transaction():
            for position, candidate in enumerate(candidates):
                video = candidate.video
                if not video.bvid:
                    continue
                seen += 1
                db.upsert_video(video)
                db.save_discovery(
                    run_id=run.id,
                    bvid=video.bvid,
                    source=candidate.source or source.name,
                    position=position,
                    reason=candidate.reason or "",
                )
                if db.enqueue_once("fetch_subtitle", video.bvid):
                    enqueued += 1
        db.finish_run(run.id, "ok")
        return {
            "run_id": run.id,
            "source": source.name,
            "status": "ok",
            "candidates": seen,
            "enqueued": enqueued,
        }
    except Exception as exc:
        db.finish_run(run.id, "failed", str(exc))
        logger.exception("refresh_source %s failed", source.name)
        raise
