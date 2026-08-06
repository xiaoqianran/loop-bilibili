"""
Long-running interest-driven scrape cadence.

Cycle (persisted in SQLite — survives process restarts):

  1. When homepage_interval_s has elapsed (or first tick): refresh video sources
     → upsert videos + discoveries → preference score → enqueue_once(fetch_subtitle)
  2. Drain up to jobs_per_cycle pending jobs with paced subtitle worker
  3. Sleep / poll until next discovery refresh or more jobs are ready

This is the durable multi-hour entry. ``once`` is a single cycle; ``worker`` is
jobs-only. ``run_cadence`` is discovery + work in one loop.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol, Sequence

from .database import Database
from .ingest import refresh_source
from .preference.scorer import PreferenceScorer
from .worker import (
    DEFAULT_JOB_DELAY,
    DEFAULT_JOB_JITTER,
    DEFAULT_RETRY_DELAY,
    DEFAULT_RISK_BASE_DELAY,
    DEFAULT_RISK_MAX_DELAY,
    SubtitleSource,
    run_once,
)

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


class VideoSource(Protocol):
    name: str

    def fetch(self) -> Sequence:
        ...


def run_cadence(
    db: Database,
    sources: Sequence[VideoSource],
    subtitle_source: SubtitleSource,
    *,
    scorer: PreferenceScorer | None = None,
    prefer_enabled: bool = True,
    homepage_interval_s: float = 600.0,
    poll_interval: float = 2.0,
    jobs_per_cycle: int = 50,
    max_cycles: int = 0,
    max_idle_cycles: int = 0,
    language: str = "zh",
    job_delay: float = DEFAULT_JOB_DELAY,
    job_jitter: float = DEFAULT_JOB_JITTER,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    risk_base_delay: float = DEFAULT_RISK_BASE_DELAY,
    risk_max_delay: float = DEFAULT_RISK_MAX_DELAY,
    sleep: SleepFn | None = None,
    clock: ClockFn | None = None,
    pace: bool = True,
) -> dict:
    """
    Multi-cycle discovery + paced subtitle processing.

    Parameters
    ----------
    max_cycles:
        Stop after this many discovery refreshes (0 = unlimited).
    max_idle_cycles:
        Stop after this many consecutive cycles with zero new candidates and
        zero jobs processed (0 = unlimited). Useful for tests / clean shutdown
        when the feed is static and the queue is empty.
    homepage_interval_s:
        Minimum seconds between discovery refreshes. 0 = refresh every cycle.
    jobs_per_cycle:
        Max jobs to process after each discovery tick (and between ticks when
        jobs remain). 0 means process until queue empty once per cycle step.

    Returns aggregate stats including per-cycle summaries.
    """
    sleep_fn = sleep or time.sleep
    clock_fn = clock or time.time

    interval = max(0.0, float(homepage_interval_s))
    poll = max(0.0, float(poll_interval))
    jpc = max(0, int(jobs_per_cycle))

    totals: dict = {
        "cycles": 0,
        "candidates": 0,
        "enqueued": 0,
        "skipped": 0,
        "blocked": 0,
        "processed": 0,
        "ok": 0,
        "empty": 0,
        "retry": 0,
        "failed": 0,
        "analyze": 0,
        "slept_s": 0.0,
        "idle_cycles": 0,
        "cycle_summaries": [],
    }

    last_refresh = 0.0  # force refresh on first loop
    idle_streak = 0

    while True:
        if max_cycles and totals["cycles"] >= max_cycles:
            break

        now = clock_fn()
        did_refresh = False
        cycle_candidates = 0
        cycle_enqueued = 0
        cycle_skipped = 0
        cycle_blocked = 0
        refresh_details: list[dict] = []

        due = (now - last_refresh) >= interval or totals["cycles"] == 0
        # always refresh on cycle boundary when max_cycles is used with interval 0
        if due and sources:
            did_refresh = True
            last_refresh = now
            for src in sources:
                try:
                    summary = refresh_source(
                        src,
                        db,
                        scorer=scorer,
                        prefer_enabled=prefer_enabled,
                    )
                except Exception as exc:
                    logger.exception("cadence refresh %s failed", getattr(src, "name", "?"))
                    refresh_details.append(
                        {
                            "source": getattr(src, "name", "?"),
                            "status": "failed",
                            "error": str(exc),
                            "candidates": 0,
                            "enqueued": 0,
                            "skipped": 0,
                            "blocked": 0,
                        }
                    )
                    continue
                refresh_details.append(summary)
                cycle_candidates += int(summary.get("candidates") or 0)
                cycle_enqueued += int(summary.get("enqueued") or 0)
                cycle_skipped += int(summary.get("skipped") or 0)
                cycle_blocked += int(summary.get("blocked") or 0)
            totals["cycles"] += 1
            totals["candidates"] += cycle_candidates
            totals["enqueued"] += cycle_enqueued
            totals["skipped"] += cycle_skipped
            totals["blocked"] += cycle_blocked
        elif due and not sources:
            # count empty discovery ticks when no sources configured
            totals["cycles"] += 1
            did_refresh = True
            last_refresh = now

        # Drain jobs after discovery (or between discovery waits)
        job_budget = jpc if jpc > 0 else 10_000
        job_stats = run_once(
            db,
            subtitle_source,
            language=language,
            max_jobs=job_budget,
            job_delay=job_delay,
            job_jitter=job_jitter,
            retry_delay=retry_delay,
            risk_base_delay=risk_base_delay,
            risk_max_delay=risk_max_delay,
            pace=pace,
        )
        for key in ("processed", "ok", "empty", "retry", "failed", "analyze"):
            totals[key] = totals.get(key, 0) + int(job_stats.get(key) or 0)
        totals["slept_s"] += float(job_stats.get("slept_s") or 0.0)

        cycle_rec = {
            "cycle": totals["cycles"],
            "refreshed": did_refresh,
            "candidates": cycle_candidates,
            "enqueued": cycle_enqueued,
            "skipped": cycle_skipped,
            "blocked": cycle_blocked,
            "processed": int(job_stats.get("processed") or 0),
            "ok": int(job_stats.get("ok") or 0),
            "empty": int(job_stats.get("empty") or 0),
            "retry": int(job_stats.get("retry") or 0),
            "failed": int(job_stats.get("failed") or 0),
            "sources": refresh_details,
        }
        if did_refresh:
            totals["cycle_summaries"].append(cycle_rec)
            logger.info(
                "cadence cycle=%s candidates=%s enqueued=%s processed=%s",
                cycle_rec["cycle"],
                cycle_rec["candidates"],
                cycle_rec["enqueued"],
                cycle_rec["processed"],
            )

        # Idle detection: no new enqueue and no jobs processed this tick
        if (
            cycle_enqueued == 0
            and int(job_stats.get("processed") or 0) == 0
            and did_refresh
        ):
            idle_streak += 1
            totals["idle_cycles"] = idle_streak
            if max_idle_cycles and idle_streak >= max_idle_cycles:
                break
        elif int(job_stats.get("processed") or 0) > 0 or cycle_enqueued > 0:
            idle_streak = 0
            totals["idle_cycles"] = 0

        if max_cycles and totals["cycles"] >= max_cycles:
            # after final refresh, we already drained jobs above; stop
            break

        # Wait until next discovery is due, while still draining if work appears
        if not max_cycles or totals["cycles"] < max_cycles:
            now2 = clock_fn()
            wait = max(0.0, (last_refresh + interval) - now2)
            if wait <= 0:
                # immediate next cycle (interval 0 or overdue)
                if poll > 0 and not did_refresh:
                    sleep_fn(poll)
                    totals["slept_s"] += poll
                continue
            # sleep in small polls so pending retries can be claimed when due
            step = poll if poll > 0 else min(wait, 1.0)
            slept = 0.0
            while slept < wait:
                chunk = min(step, wait - slept) if step > 0 else wait - slept
                if chunk <= 0:
                    break
                sleep_fn(chunk)
                totals["slept_s"] += chunk
                slept += chunk
                # opportunistic job drain during wait
                if jpc != 0:
                    mid = run_once(
                        db,
                        subtitle_source,
                        language=language,
                        max_jobs=max(1, min(5, jpc)),
                        job_delay=job_delay,
                        job_jitter=job_jitter,
                        retry_delay=retry_delay,
                        risk_base_delay=risk_base_delay,
                        risk_max_delay=risk_max_delay,
                        pace=pace,
                    )
                    for key in ("processed", "ok", "empty", "retry", "failed", "analyze"):
                        totals[key] = totals.get(key, 0) + int(mid.get(key) or 0)
                    totals["slept_s"] += float(mid.get("slept_s") or 0.0)
                    if int(mid.get("processed") or 0) > 0:
                        idle_streak = 0

    return totals
