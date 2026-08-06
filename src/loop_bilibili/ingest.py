"""Source refresh: discover videos, record history, optionally preference-filter enqueue."""

from __future__ import annotations

import logging
from typing import Callable, Protocol, Sequence

from .database import Database
from .models import Candidate
from .preference.models import ScoreBreakdown
from .preference.scorer import PreferenceScorer

logger = logging.getLogger(__name__)

ScoreFn = Callable[[Candidate], ScoreBreakdown]


class VideoSource(Protocol):
    name: str

    def fetch(self) -> Sequence[Candidate]:
        ...


def refresh_source(
    source: VideoSource,
    db: Database,
    *,
    scorer: PreferenceScorer | None = None,
    prefer_enabled: bool = True,
) -> dict:
    """
    Fetch candidates, upsert videos, save ordered discoveries.

    Subtitle jobs:
      * scorer is None or prefer_enabled is False → enqueue all (legacy behaviour)
      * otherwise enqueue only when scorer marks selected
    """
    run = db.start_run(source.name)
    enqueued = 0
    skipped = 0
    blocked = 0
    seen = 0
    score_samples: list[dict] = []
    try:
        candidates = list(source.fetch())
        with db.transaction():
            for position, candidate in enumerate(candidates):
                video = candidate.video
                if not video.bvid:
                    continue
                seen += 1
                db.upsert_video(video)

                decision = "enqueue"
                breakdown: ScoreBreakdown | None = None
                if scorer is not None and prefer_enabled:
                    breakdown = scorer.score_candidate(candidate)
                    if breakdown.blocked:
                        decision = "blocked"
                        blocked += 1
                    elif not breakdown.selected:
                        decision = "skip"
                        skipped += 1
                    else:
                        decision = "enqueue"

                # keep discovery reason + optional score trail for debugging
                reason = candidate.reason or ""
                if breakdown is not None:
                    reason = (
                        f"{reason} | prefer:{breakdown.summary()}".strip(" |")
                        if reason
                        else f"prefer:{breakdown.summary()}"
                    )
                    if len(score_samples) < 12:
                        score_samples.append(
                            {
                                "bvid": video.bvid,
                                "title": (video.title or "")[:80],
                                "score": breakdown.score,
                                "decision": decision,
                                "summary": breakdown.summary(),
                            }
                        )

                db.save_discovery(
                    run_id=run.id,
                    bvid=video.bvid,
                    source=candidate.source or source.name,
                    position=position,
                    reason=reason,
                )

                if decision == "enqueue":
                    if db.enqueue_once("fetch_subtitle", video.bvid):
                        enqueued += 1

        db.finish_run(run.id, "ok")
        return {
            "run_id": run.id,
            "source": source.name,
            "status": "ok",
            "candidates": seen,
            "enqueued": enqueued,
            "skipped": skipped,
            "blocked": blocked,
            "prefer": bool(scorer is not None and prefer_enabled),
            "samples": score_samples,
        }
    except Exception as exc:
        db.finish_run(run.id, "failed", str(exc))
        logger.exception("refresh_source %s failed", source.name)
        raise
