"""Subtitle job worker (serial) with SubBatch-like pacing and risk backoff."""

from __future__ import annotations

import logging
import random
import time
from typing import Protocol

from .database import Database
from .models import AiConfig, Job, SubtitlePayload
from .sources._http import RetryableError, is_risk_text

logger = logging.getLogger(__name__)

DEFAULT_JOB_DELAY = 0.5
DEFAULT_JOB_JITTER = 0.15
DEFAULT_RETRY_DELAY = 60.0
DEFAULT_RISK_BASE_DELAY = 15.0
DEFAULT_RISK_MAX_DELAY = 300.0


class SubtitleSource(Protocol):
    def fetch(self, bvid: str) -> object:
        ...


def _result_to_payload(result: object, *, language: str) -> SubtitlePayload:
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


def risk_retry_delay(
    attempts: int,
    *,
    base: float = DEFAULT_RISK_BASE_DELAY,
    maximum: float = DEFAULT_RISK_MAX_DELAY,
) -> float:
    exp = max(0, int(attempts) - 1)
    delay = min(maximum, base * (2**exp))
    jitter = random.uniform(0.0, min(3.0, delay * 0.1))
    return delay + jitter


def job_pace_sleep(delay: float, jitter: float) -> float:
    if delay <= 0 and jitter <= 0:
        return 0.0
    sl = max(0.0, delay + random.uniform(-abs(jitter), abs(jitter)))
    if sl > 0:
        time.sleep(sl)
    return sl


def _enqueue_analyze_jobs(db: Database, bvid: str, ai: AiConfig | None) -> None:
    """After subtitle ok: one analyze job per configured model (or one stub job)."""
    if ai is not None and ai.enabled and ai.api_key and ai.base_url and ai.model_list():
        from .ai_worker import enqueue_analyze_for_models

        enqueue_analyze_for_models(db, bvid, ai, force=False)
        return
    # offline / disabled: single model-less analyze stub
    db.enqueue_once("analyze", bvid, model="")


def process_subtitle_job(
    job: Job,
    db: Database,
    source: SubtitleSource,
    *,
    language: str = "zh",
    retry_delay: float = DEFAULT_RETRY_DELAY,
    risk_base_delay: float = DEFAULT_RISK_BASE_DELAY,
    risk_max_delay: float = DEFAULT_RISK_MAX_DELAY,
    ai: AiConfig | None = None,
) -> str:
    try:
        result = source.fetch(job.bvid)
        payload = _result_to_payload(result, language=language)
        if not payload.bvid:
            payload.bvid = job.bvid
        db.save_subtitle(payload)

        if payload.status == "ok":
            db.complete_job(job.id)
            _enqueue_analyze_jobs(db, job.bvid, ai)
            return "ok"
        if payload.status == "empty":
            db.complete_job(job.id)
            return "empty"
        if payload.status == "retry":
            delay = retry_delay
            if is_risk_text(payload.error):
                delay = risk_retry_delay(
                    job.attempts,
                    base=risk_base_delay,
                    maximum=risk_max_delay,
                )
            db.retry_job(job.id, error=payload.error or "retry", delay_seconds=delay)
            return "retry"
        db.fail_job(job.id, error=payload.error or "failed")
        return "failed"
    except RetryableError as exc:
        err = str(exc)
        delay = retry_delay
        if is_risk_text(err):
            delay = risk_retry_delay(
                job.attempts,
                base=risk_base_delay,
                maximum=risk_max_delay,
            )
        db.retry_job(job.id, error=err, delay_seconds=delay)
        return "retry"
    except Exception as exc:
        logger.exception("subtitle job %s failed", job.bvid)
        db.fail_job(job.id, error=str(exc))
        return "failed"


def process_analyze_job(
    job: Job,
    db: Database,
    *,
    ai: AiConfig | None = None,
    language: str = "zh",
) -> str:
    from .ai_worker import analyze_stub, analyze_with_llm

    if ai is not None and ai.enabled and ai.api_key and ai.base_url and ai.model_list():
        return analyze_with_llm(job, db, ai, language=language)
    return analyze_stub(job, db)


def run_once(
    db: Database,
    source: SubtitleSource,
    *,
    language: str = "zh",
    max_jobs: int = 1,
    job_delay: float = DEFAULT_JOB_DELAY,
    job_jitter: float = DEFAULT_JOB_JITTER,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    risk_base_delay: float = DEFAULT_RISK_BASE_DELAY,
    risk_max_delay: float = DEFAULT_RISK_MAX_DELAY,
    pace: bool = True,
    ai: AiConfig | None = None,
) -> dict:
    stats = {
        "processed": 0,
        "ok": 0,
        "empty": 0,
        "retry": 0,
        "failed": 0,
        "analyze": 0,
        "slept_s": 0.0,
    }
    # AI multi-model path: drop legacy model-less analyze stubs so they
    # cannot starve real Mermaid jobs.
    if ai is not None and ai.enabled and ai.api_key and ai.model_list():
        n = db.supersede_empty_analyze_stubs()
        if n:
            logger.info("superseded %s empty-model analyze stubs", n)
    for i in range(max(0, max_jobs)):
        job = db.claim_next_job(kinds=["fetch_subtitle", "analyze"])
        if job is None:
            break
        stats["processed"] += 1
        if job.kind == "analyze":
            outcome = process_analyze_job(job, db, ai=ai, language=language)
            if outcome == "ok":
                stats["analyze"] += 1
            else:
                stats["failed"] += 1
        else:
            outcome = process_subtitle_job(
                job,
                db,
                source,
                language=language,
                retry_delay=retry_delay,
                risk_base_delay=risk_base_delay,
                risk_max_delay=risk_max_delay,
                ai=ai,
            )
            stats[outcome] = stats.get(outcome, 0) + 1
        if pace and i + 1 < max_jobs:
            stats["slept_s"] += job_pace_sleep(job_delay, job_jitter)
    return stats


def worker_loop(
    db: Database,
    source: SubtitleSource,
    *,
    language: str = "zh",
    poll_interval: float = 2.0,
    max_jobs: int = 0,
    max_idle: int = 0,
    job_delay: float = DEFAULT_JOB_DELAY,
    job_jitter: float = DEFAULT_JOB_JITTER,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    risk_base_delay: float = DEFAULT_RISK_BASE_DELAY,
    risk_max_delay: float = DEFAULT_RISK_MAX_DELAY,
    ai: AiConfig | None = None,
) -> dict:
    total = {
        "processed": 0,
        "ok": 0,
        "empty": 0,
        "retry": 0,
        "failed": 0,
        "analyze": 0,
        "idle_polls": 0,
        "slept_s": 0.0,
    }
    idle = 0
    cleaned_stubs = False
    while True:
        if max_jobs and total["processed"] >= max_jobs:
            break
        if (
            not cleaned_stubs
            and ai is not None
            and ai.enabled
            and ai.api_key
            and ai.model_list()
        ):
            n = db.supersede_empty_analyze_stubs()
            if n:
                logger.info("superseded %s empty-model analyze stubs", n)
            cleaned_stubs = True
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
            outcome = process_analyze_job(job, db, ai=ai, language=language)
            if outcome == "ok":
                total["analyze"] += 1
            else:
                total["failed"] += 1
        else:
            outcome = process_subtitle_job(
                job,
                db,
                source,
                language=language,
                retry_delay=retry_delay,
                risk_base_delay=risk_base_delay,
                risk_max_delay=risk_max_delay,
                ai=ai,
            )
            total[outcome] = total.get(outcome, 0) + 1
        total["slept_s"] += job_pace_sleep(job_delay, job_jitter)
        if max_jobs and total["processed"] >= max_jobs:
            break
    return total
