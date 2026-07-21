"""批量 bilibili summary（官方 AI 总结）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loop_core.errors import FetchError
from loop_core.rate_limit import Profile
from loop_core.retry import run_with_retry
from loop_core.runner import OpencliRunner

from item_batch.runner import run_item_batch


def _is_empty_summary(data: Any) -> bool:
    if data is None:
        return True
    if isinstance(data, list) and len(data) == 0:
        return True
    if isinstance(data, dict):
        if data.get("ok") is False:
            return True
        err = str(data.get("error") or data.get("message") or "")
        if "no data" in err.lower() or "no summary" in err.lower() or "EMPTY" in err:
            return True
    return False


def fetch_summary(runner: OpencliRunner, bvid: str, profile: Profile) -> dict[str, Any]:
    try:
        data = run_with_retry(
            runner,
            ["bilibili", "summary", bvid, "-f", "json"],
            profile,
            timeout=180,
        )
    except FetchError as e:
        # EMPTY_RESULT often surfaces as hard with message
        msg = str(e).lower()
        if "empty" in msg or "no data" in msg or "no summary" in msg or "exitcode: 66" in msg:
            return {"bvid": bvid, "status": "empty", "reason": "no_ai_summary", "raw_error": str(e)[:400]}
        raise

    if _is_empty_summary(data):
        return {"bvid": bvid, "status": "empty", "reason": "no_ai_summary", "data": data}

    segments = data if isinstance(data, list) else [data]
    return {
        "bvid": bvid,
        "status": "ok",
        "segment_count": len(segments) if isinstance(segments, list) else 1,
        "data": data,
    }


def export_summaries(
    bvids: list[str],
    out_dir: Path,
    profile: Profile,
    *,
    resume: bool = True,
    runner: OpencliRunner | None = None,
) -> Path:
    return run_item_batch(
        kind="summary",
        bvids=bvids,
        out_dir=out_dir,
        profile=profile,
        fetch_one=fetch_summary,
        resume=resume,
        runner=runner,
    )
