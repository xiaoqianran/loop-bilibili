"""批量 bilibili comments。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loop_core.errors import FetchError
from loop_core.rate_limit import Profile
from loop_core.retry import run_with_retry
from loop_core.runner import OpencliRunner

from item_batch.runner import run_item_batch


def make_fetch_comments(comment_limit: int = 20):
    def fetch_comments(runner: OpencliRunner, bvid: str, profile: Profile) -> dict[str, Any]:
        try:
            data = run_with_retry(
                runner,
                [
                    "bilibili",
                    "comments",
                    bvid,
                    "--limit",
                    str(comment_limit),
                    "-f",
                    "json",
                ],
                profile,
                timeout=180,
            )
        except FetchError as e:
            msg = str(e).lower()
            if "empty" in msg or "no data" in msg:
                return {
                    "bvid": bvid,
                    "status": "empty",
                    "reason": "no_comments",
                    "raw_error": str(e)[:400],
                }
            raise

        rows = data if isinstance(data, list) else []
        if isinstance(data, dict):
            for k in ("items", "data", "comments", "results"):
                if isinstance(data.get(k), list):
                    rows = data[k]
                    break
        if not rows:
            return {"bvid": bvid, "status": "empty", "reason": "no_comments", "data": data}
        return {
            "bvid": bvid,
            "status": "ok",
            "comment_count": len(rows),
            "limit": comment_limit,
            "data": data,
        }

    return fetch_comments


def export_comments(
    bvids: list[str],
    out_dir: Path,
    profile: Profile,
    *,
    comment_limit: int = 20,
    resume: bool = True,
    runner: OpencliRunner | None = None,
) -> Path:
    return run_item_batch(
        kind="comments",
        bvids=bvids,
        out_dir=out_dir,
        profile=profile,
        fetch_one=make_fetch_comments(comment_limit),
        resume=resume,
        runner=runner,
    )
