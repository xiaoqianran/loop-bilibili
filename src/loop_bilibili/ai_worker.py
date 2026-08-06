"""AI analyze worker — multi-model Mermaid learning graphs from subtitles."""

from __future__ import annotations

import json
import logging
from typing import Any

from .ai_client import AiClientError, chat_completion
from .ai_mermaid import (
    build_messages,
    extract_mermaid_diagrams,
    format_cues_for_ai,
    sanitize_mermaid_in_markdown,
    truncate_subtitle,
)
from .database import Database
from .models import AiConfig, AnalysisPayload, Job

logger = logging.getLogger(__name__)


def _load_cues(sub: dict[str, Any]) -> list[dict[str, Any]]:
    raw = sub.get("cues_json") or "[]"
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _subtitle_for_job(db: Database, bvid: str, language: str) -> dict[str, Any] | None:
    sub = db.get_subtitle(bvid, language)
    if sub and sub.get("status") == "ok":
        return sub
    row = db._conn.execute(
        "SELECT * FROM subtitles WHERE bvid = ? AND status = 'ok' LIMIT 1",
        (bvid,),
    ).fetchone()
    return dict(row) if row else None


def build_analysis_from_markdown(
    *,
    bvid: str,
    markdown: str,
    model: str,
    mode: str = "mermaid",
) -> AnalysisPayload:
    cleaned = sanitize_mermaid_in_markdown(markdown)
    diagrams = extract_mermaid_diagrams(cleaned)
    if not diagrams:
        return AnalysisPayload(
            bvid=bvid,
            status="failed",
            mode=mode,
            model=model,
            markdown=cleaned,
            diagrams_json="[]",
            error="no mermaid diagrams in model output",
        )
    return AnalysisPayload(
        bvid=bvid,
        status="ok",
        mode=mode,
        model=model,
        markdown=cleaned,
        diagrams_json=json.dumps(diagrams, ensure_ascii=False),
        error="",
    )


def _should_retry_job(job: Job, ai: AiConfig) -> bool:
    max_attempts = max(1, int(ai.max_attempts or 3))
    return int(job.attempts or 0) < max_attempts


def resolve_job_model(job: Job, ai: AiConfig) -> str:
    """Which model id this analyze job should call."""
    if (job.model or "").strip():
        return job.model.strip()
    return ai.default_model


def enqueue_analyze_for_models(
    db: Database,
    bvid: str,
    ai: AiConfig,
    *,
    force: bool = False,
) -> int:
    """
    Enqueue one analyze job per configured model.

    Returns number of newly enqueued / requeued jobs.
    """
    n = 0
    for model in ai.model_list():
        if force:
            db.requeue_job("analyze", bvid, model=model)
            n += 1
            continue
        existing = db.get_analysis(bvid, model=model)
        if existing and existing.get("status") == "ok":
            continue
        job = db.get_job("analyze", bvid, model=model)
        if job and job.status in ("pending", "running"):
            continue
        if job and job.status == "done" and existing and existing.get("status") == "ok":
            continue
        if job and job.status == "failed":
            db.requeue_job("analyze", bvid, model=model)
            n += 1
        elif db.enqueue_once("analyze", bvid, model=model):
            n += 1
        else:
            db.requeue_job("analyze", bvid, model=model)
            n += 1
    return n


def analyze_with_llm(
    job: Job,
    db: Database,
    ai: AiConfig,
    *,
    language: str = "zh",
) -> str:
    """
    Call one LLM model to produce Mermaid diagrams for one video subtitle.

    Job.model selects the model; empty → ai.default_model.
    """
    if not ai.enabled:
        return "skipped"
    if not ai.api_key or not ai.base_url:
        db.fail_job(job.id, error="AI config incomplete (api_key/base_url)")
        return "failed"

    model = resolve_job_model(job, ai)
    if not model:
        db.fail_job(job.id, error="AI model empty")
        return "failed"

    sub = _subtitle_for_job(db, job.bvid, language)
    if not sub or sub.get("status") != "ok":
        db.fail_job(job.id, error="subtitle not ok for analyze")
        return "failed"

    video = db.get_video(job.bvid) or {}
    title = str(video.get("title") or job.bvid)
    author = str(video.get("owner_name") or "")
    cues = _load_cues(sub)
    transcript = format_cues_for_ai(
        cues,
        bvid=job.bvid,
        plain_text=str(sub.get("text") or ""),
    )
    cut = truncate_subtitle(transcript, ai.max_subtitle_chars)
    messages = build_messages(
        title=title,
        bvid=job.bvid,
        author=author,
        subtitle=cut["text"],
        custom_instruction=ai.custom_instruction,
    )

    try:
        result = chat_completion(
            base_url=ai.base_url,
            api_key=ai.api_key,
            model=model,
            messages=messages,
            temperature=ai.temperature,
            max_tokens=ai.max_tokens,
            timeout=ai.timeout_s,
            stream=False,
            retries=max(0, int(ai.request_retries)),
            retry_backoff_s=float(ai.request_retry_backoff_s),
        )
    except AiClientError as exc:
        logger.warning("analyze %s [%s] LLM error: %s", job.bvid, model, exc)
        payload = AnalysisPayload(
            bvid=job.bvid,
            status="failed",
            mode=ai.mode,
            model=model,
            markdown="",
            diagrams_json="[]",
            error=str(exc)[:800],
        )
        db.save_analysis(payload)
        retryable = bool(getattr(exc, "retryable", False)) or any(
            x in str(exc).lower()
            for x in ("http 429", "http 5", "timeout", "network", "overloaded")
        )
        if retryable and _should_retry_job(job, ai):
            delay = float(ai.job_retry_delay_s or 60.0)
            db.retry_job(job.id, error=str(exc)[:400], delay_seconds=delay)
            logger.info(
                "analyze %s [%s] requeued (attempt %s/%s) in %.0fs",
                job.bvid,
                model,
                job.attempts,
                ai.max_attempts,
                delay,
            )
            return "failed"
        db.fail_job(job.id, error=str(exc)[:400])
        return "failed"

    # Prefer explicit job model id for storage (stable key); content model as note
    store_model = model
    payload = build_analysis_from_markdown(
        bvid=job.bvid,
        markdown=result.content,
        model=store_model,
        mode=ai.mode,
    )
    if cut.get("truncated") and payload.status != "ok":
        payload.error = (
            (payload.error + "; " if payload.error else "")
            + f"subtitle truncated from {cut['original_len']} chars"
        )
    db.save_analysis(payload)
    if payload.status == "ok":
        db.complete_job(job.id)
        logger.info(
            "analyze %s [%s] ok · diagrams=%s",
            job.bvid,
            store_model,
            len(json.loads(payload.diagrams_json or "[]")),
        )
        return "ok"

    if _should_retry_job(job, ai):
        delay = float(ai.job_retry_delay_s or 60.0)
        db.retry_job(
            job.id,
            error=(payload.error or "no mermaid diagrams")[:400],
            delay_seconds=delay,
        )
        logger.info(
            "analyze %s [%s] no diagrams · requeued attempt %s/%s",
            job.bvid,
            store_model,
            job.attempts,
            ai.max_attempts,
        )
        return "failed"

    db.fail_job(job.id, error=payload.error or "analyze failed")
    return "failed"


def analyze_stub(job: Job, db: Database) -> str:
    """No-op when AI is disabled: mark analyze done if subtitle is ok."""
    sub = _subtitle_for_job(db, job.bvid, "zh")
    if not sub or sub.get("status") != "ok":
        db.fail_job(job.id, error="subtitle not ok for analyze")
        return "failed"
    db.complete_job(job.id)
    return "ok"
