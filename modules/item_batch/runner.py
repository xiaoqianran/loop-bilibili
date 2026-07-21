"""串行逐 bvid 批处理 + resume + 更严 item_delay。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from loop_core.batch import (
    load_done_set,
    load_results,
    pending_keys,
    save_done_set,
    save_job_progress,
    save_results,
    upsert_result,
)
from loop_core.errors import FetchError
from loop_core.rate_limit import Profile, sleep_item
from loop_core.retry import run_with_retry
from loop_core.runner import OpencliRunner
from loop_core.timeutil import utc_now_iso

logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


FetchFn = Callable[[OpencliRunner, str, Profile], dict[str, Any]]


def run_item_batch(
    *,
    kind: str,
    bvids: list[str],
    out_dir: Path,
    profile: Profile,
    fetch_one: FetchFn,
    resume: bool = True,
    runner: OpencliRunner | None = None,
) -> Path:
    """
    对每个 bvid 调用 fetch_one；成功/软跳过写入 results；done 集合支持 resume。
    fetch_one 返回 dict，建议含: bvid, status (ok|empty|error), ...
    """
    runner = runner or OpencliRunner()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    done = load_done_set(out_dir) if resume else set()
    todo = pending_keys(bvids, done, resume=resume)
    results = load_results(out_dir) if resume else []

    log(
        f"{kind}: total={len(bvids)} done={len(done)} todo={len(todo)} "
        f"item_delay={profile.item_delay}±{profile.item_jitter}s profile={profile.name}"
    )

    for i, bvid in enumerate(todo, 1):
        log(f"[{i}/{len(todo)}] {kind} {bvid}")
        save_job_progress(
            out_dir,
            {
                "kind": kind,
                "current": bvid,
                "index": i,
                "todo": len(todo),
                "status": "running",
                "profile": profile.name,
            },
        )
        try:
            row = fetch_one(runner, bvid, profile)
            if "bvid" not in row:
                row["bvid"] = bvid
            if "status" not in row:
                row["status"] = "ok"
            row["fetched_at"] = utc_now_iso()
        except FetchError as e:
            if e.category == "sign352":
                save_job_progress(
                    out_dir,
                    {
                        "kind": kind,
                        "current": bvid,
                        "status": "failed",
                        "error": str(e)[:500],
                        "category": e.category,
                    },
                )
                raise SystemExit(f"{kind} hard fail (-352) on {bvid}: {e}") from e
            row = {
                "bvid": bvid,
                "status": "error",
                "error": str(e)[:800],
                "category": e.category,
                "fetched_at": utc_now_iso(),
            }
            log(f"  error [{e.category}]: {str(e)[:120]}")
        except SystemExit:
            raise
        except Exception as e:
            row = {
                "bvid": bvid,
                "status": "error",
                "error": str(e)[:800],
                "category": "hard",
                "fetched_at": utc_now_iso(),
            }
            log(f"  error: {e}")

        results = upsert_result(results, "bvid", row)
        # only mark done for ok/empty (soft skip); leave error for retry unless resume re-does all
        if row.get("status") in ("ok", "empty"):
            done.add(bvid)
        save_results(out_dir, results)
        save_done_set(out_dir, done)

        # per-item payload file
        item_path = out_dir / "items" / f"{bvid}.json"
        item_path.parent.mkdir(parents=True, exist_ok=True)
        item_path.write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        if i < len(todo):
            sleep_item(profile, label=f"{kind}-item", log=log)

    meta = {
        "kind": kind,
        "total_requested": len(bvids),
        "done_count": len(done),
        "results_count": len(results),
        "profile": profile.name,
        "item_delay": profile.item_delay,
        "item_jitter": profile.item_jitter,
        "exported_at": utc_now_iso(),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    save_job_progress(
        out_dir,
        {"kind": kind, "status": "complete", "done_count": len(done), "profile": profile.name},
    )
    log(f"{kind} complete -> {out_dir}")
    return out_dir
