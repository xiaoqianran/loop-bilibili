"""hot / ranking / search 一次性导出（列表级限速）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from loop_core.rate_limit import Profile
from loop_core.retry import run_with_retry
from loop_core.runner import OpencliRunner
from loop_core.timeutil import utc_now_iso

logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


def _normalize_rows(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("items", "data", "results", "list", "rows"):
            if isinstance(data.get(k), list):
                return [x for x in data[k] if isinstance(x, dict)]
    return []


def _write_dump(folder: Path, name: str, rows: list[dict], meta: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / f"{name}.json"
    md_path = folder / f"{name}.md"
    meta_path = folder / f"{name}.meta.json"

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        f"# {name}",
        "",
        f"- count: **{len(rows)}**",
        f"- exported_at: {meta.get('exported_at')}",
        f"- profile: `{meta.get('profile')}`",
        "",
        "| # | title / text | author | url |",
        "|---|--------------|--------|-----|",
    ]
    for i, r in enumerate(rows, 1):
        title = str(
            r.get("title") or r.get("text") or r.get("name") or r.get("content") or ""
        ).replace("|", "\\|")[:80]
        author = str(r.get("author") or r.get("name") or r.get("up") or "-")[:40]
        url = str(r.get("url") or r.get("link") or "")
        lines.append(f"| {i} | {title} | {author} | {url} |")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"Wrote {json_path} ({len(rows)} rows)")
    return folder


def export_hot(
    out_root: Path,
    profile: Profile,
    *,
    limit: int = 10,
    runner: OpencliRunner | None = None,
) -> Path:
    runner = runner or OpencliRunner()
    log(f"hot limit={limit} profile={profile.name}")
    data = run_with_retry(
        runner,
        ["bilibili", "hot", "--limit", str(limit), "-f", "json"],
        profile,
        timeout=180,
        log=log,
    )
    rows = _normalize_rows(data)
    folder = out_root / "hot"
    meta = {
        "kind": "hot",
        "limit": limit,
        "count": len(rows),
        "profile": profile.name,
        "exported_at": utc_now_iso(),
        "opencli": "bilibili hot",
    }
    return _write_dump(folder, "hot", rows, meta)


def export_ranking(
    out_root: Path,
    profile: Profile,
    *,
    limit: int = 10,
    runner: OpencliRunner | None = None,
) -> Path:
    runner = runner or OpencliRunner()
    log(f"ranking limit={limit} profile={profile.name}")
    # ranking may not support --limit on all versions; try with limit first
    args = ["bilibili", "ranking", "-f", "json"]
    # some adapters accept --limit
    try:
        data = run_with_retry(
            runner,
            ["bilibili", "ranking", "--limit", str(limit), "-f", "json"],
            profile,
            timeout=180,
            log=log,
        )
    except Exception:
        data = run_with_retry(runner, args, profile, timeout=180, log=log)
    rows = _normalize_rows(data)[:limit]
    folder = out_root / "ranking"
    meta = {
        "kind": "ranking",
        "limit": limit,
        "count": len(rows),
        "profile": profile.name,
        "exported_at": utc_now_iso(),
        "opencli": "bilibili ranking",
    }
    return _write_dump(folder, "ranking", rows, meta)


def export_search(
    query: str,
    out_root: Path,
    profile: Profile,
    *,
    limit: int = 10,
    runner: OpencliRunner | None = None,
) -> Path:
    runner = runner or OpencliRunner()
    log(f"search {query!r} limit={limit} profile={profile.name}")
    data = run_with_retry(
        runner,
        ["bilibili", "search", query, "--limit", str(limit), "-f", "json"],
        profile,
        timeout=180,
        log=log,
    )
    rows = _normalize_rows(data)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in query)[:40] or "q"
    folder = out_root / "search" / safe
    meta = {
        "kind": "search",
        "query": query,
        "limit": limit,
        "count": len(rows),
        "profile": profile.name,
        "exported_at": utc_now_iso(),
        "opencli": "bilibili search",
    }
    return _write_dump(folder, "search", rows, meta)
