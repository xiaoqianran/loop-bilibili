"""[LEGACY] 旧版 opencli bilibili subtitle 批量导出。

已退役。请使用 modules.subtitle.export.export_subtitles（bili_subbatch）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loop_core.errors import FetchError
from loop_core.rate_limit import Profile
from loop_core.retry import run_with_retry
from loop_core.runner import OpencliRunner

from item_batch.runner import run_item_batch


def fetch_subtitle_opencli(
    runner: OpencliRunner, bvid: str, profile: Profile
) -> dict[str, Any]:
    try:
        data = run_with_retry(
            runner,
            ["bilibili", "subtitle", bvid, "-f", "json"],
            profile,
            timeout=180,
        )
    except FetchError as e:
        msg = str(e).lower()
        if "empty" in msg or "no data" in msg or "no subtitle" in msg:
            return {
                "bvid": bvid,
                "status": "empty",
                "reason": "no_subtitle",
                "raw_error": str(e)[:400],
                "plugin": "legacy-opencli",
            }
        raise

    if data is None or (isinstance(data, list) and len(data) == 0):
        return {
            "bvid": bvid,
            "status": "empty",
            "reason": "no_subtitle",
            "plugin": "legacy-opencli",
        }

    cues = (
        data
        if isinstance(data, list)
        else data.get("cues") or data.get("items") or [data]
    )
    n = len(cues) if isinstance(cues, list) else 1
    return {
        "bvid": bvid,
        "status": "ok",
        "cue_count": n,
        "data": data,
        "plugin": "legacy-opencli",
    }


def export_subtitles_opencli(
    bvids: list[str],
    out_dir: Path,
    profile: Profile,
    *,
    resume: bool = True,
    runner: OpencliRunner | None = None,
) -> Path:
    """串行 opencli 字幕批处理（legacy）。"""
    return run_item_batch(
        kind="subtitle-opencli-legacy",
        bvids=bvids,
        out_dir=out_dir,
        profile=profile,
        fetch_one=fetch_subtitle_opencli,
        resume=resume,
        runner=runner,
    )
