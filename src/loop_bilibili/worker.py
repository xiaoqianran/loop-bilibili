"""Subtitle job worker (serial)."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from .database import Database
from .models import Job, SubtitlePayload
from .sources._http import RetryableError

logger = logging.getLogger(__name__)


class SubtitleSource(Protocol):
    def fetch(self, bvid: str) -> object:
        ...


def _result_to_payload(result: object, *, language: str) -> SubtitlePayload:
    """Accept SubtitleFetchResult or any object with the same attributes."""
    status = str(getattr(result, "status", "failed"))
    if status not in ("ok", "empty", "retry", "failed", "pending"):
        status = "failed"
    text = str(getattr(result, "text", "") or "")
    error = str(getattr(result, "error", "") or "")
    lang = str(getattr(result, "language", "") or language)
    if hasattr(result, "to_payload"):
        payload = result.to_payload(language=lang)  # type: ignore[operator]
        if isinstance(payload, SubtitlePayload):
            return payload
    cues = getattr(result, "cues", None) or []
    from .database import dumps_cues

    return SubtitlePayload(
        bvid=str(getattr(result, "bvid", "")),
        language=lang,
        status=status,  # type: ignore[arg-type]
        text=text,
        cues_json=dumps_cues(list(cues) if cues else []),
        error=error,
    )


def process_subtitle_job(
    job: Job,
    db: Database,
    source: SubtitleSource,
    *,
    language: str = "zh",
    retry_delay: float = 60.0,
) -> str:
    """
    Process one fetch_subtitle job.

    Returns terminal-ish outcome label: ok|empty|retry|failed.
    """
    try:
        result = source.fetch(job.bvid)
        payload = _result_to_payload(result, language=language)
        if not payload.bvid:
            payload.bvid = job.bvid
        db.save_subtitle(payload)

        if payload.status == "ok":
            db.complete_job(job.id)
            db.enqueue_once("analyze", job.bvid)
            return "ok"
        if payload.status == "empty":
            db.complete_job(job.id)
            return "empty"
        if payload.status == "retry":
            db.retry_job(job.id, error=payload.error or "retry", delay_seconds=retry_delay)
            return "retry"
        # failed
        db.fail_job(job.id, error=payload.error or "failed")
        return "failed"
    except RetryableError as exc:
        db.retry_job(job.id, error=str(exc), delay_seconds=retry_delay)
        return "retry"
    except Exception as exc:
        logger.exception("subtitle job %s failed", job.bvid)
        db.fail_job(job.id, error=str(exc))
        return "failed"


def process_analyze_job(job: Job, db: Database) -> str:
    """No-op AI stub: mark analyze job done when subtitle is ok."""
    # Future: load subtitle text, call LLM, store analysis.
    sub = db.get_subtitle(job.bvid, "zh")
    if sub is None:
        # try any language
        row = db._conn.execute(
            "SELECT * FROM subtitles WHERE bvid = ? AND status = 'ok' LIMIT 1",
            (job.bvid,),
        ).fetchone()
        sub = dict(row) if row else None
    if not sub or sub.get("status") != "ok":
        db.fail_job(job.id, error="subtitle not ok for analyze")
        return "failed"
    db.complete_job(job.id)
    return "ok"


def run_once(
    db: Database,
    source: SubtitleSource,
    *,
    language: str = "zh",
    max_jobs: int = 1,
) -> dict:
    """Claim and process up to max_jobs pending jobs (subtitle then analyze)."""
    stats = {"processed": 0, "ok": 0, "empty": 0, "retry": 0, "failed": 0, "analyze": 0}
    for _ in range(max(0, max_jobs)):
        job = db.claim_next_job(kinds=["fetch_subtitle", "analyze"])
        if job is None:
            break
        stats["processed"] += 1
        if job.kind == "analyze":
            outcome = process_analyze_job(job, db)
            if outcome == "ok":
                stats["analyze"] += 1
            else:
                stats["failed"] += 1
            continue
        outcome = process_subtitle_job(job, db, source, language=language)
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats


def worker_loop(
    db: Database,
    source: SubtitleSource,
    *,
    language: str = "zh",
    poll_interval: float = 2.0,
    max_jobs: int = 0,
    max_idle: int = 0,
) -> dict:
    """
    Long-running serial worker.

    max_jobs: stop after N processed jobs (0 = unlimited).
    max_idle: stop after N idle polls (0 = unlimited).
    """
    total = {
        "processed": 0,
        "ok": 0,
        "empty": 0,
        "retry": 0,
        "failed": 0,
        "analyze": 0,
        "idle_polls": 0,
    }
    idle = 0
    while True:
        if max_jobs and total["processed"] >= max_jobs:
            break
        job = db.claim_next_job(kinds=["fetch_subtitle", "analyze"])
        if job is None:
            idle += 1
            total["idle_polls"] = idle
            if max_idle and idle >= max_idle:
                break
            time.sleep(poll_interval)
            continue
        idle = 0
        total["processed"] += 1
        if job.kind == "analyze":
            outcome = process_analyze_job(job, db)
            if outcome == "ok":
                total["analyze"] += 1
            else:
                total["failed"] += 1
        else:
            outcome = process_subtitle_job(job, db, source, language=language)
            total[outcome] = total.get(outcome, 0) + 1
        if max_jobs and total["processed"] >= max_jobs:
            break
    return total
