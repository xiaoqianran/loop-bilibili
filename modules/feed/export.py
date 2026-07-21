"""关注流 / 用户动态导出。"""

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


def export_feed(
    out_root: Path,
    profile: Profile,
    *,
    uid: str = "",
    limit: int = 10,
    pages: int = 1,
    feed_type: str = "all",
    runner: OpencliRunner | None = None,
) -> Path:
    runner = runner or OpencliRunner()
    args = ["bilibili", "feed"]
    if uid:
        args.append(str(uid))
    args += [
        "--limit",
        str(limit),
        "--pages",
        str(pages),
        "--type",
        feed_type,
        "-f",
        "json",
    ]
    log(f"feed uid={uid or '(following)'} limit={limit} pages={pages}")
    data = run_with_retry(runner, args, profile, timeout=180, log=log)
    rows = _normalize_rows(data)

    label = uid or "following"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    folder = out_root / "feed" / safe
    folder.mkdir(parents=True, exist_ok=True)

    meta = {
        "kind": "feed",
        "uid": uid or None,
        "limit": limit,
        "pages": pages,
        "type": feed_type,
        "count": len(rows),
        "profile": profile.name,
        "exported_at": utc_now_iso(),
        "opencli": "bilibili feed",
    }
    (folder / "feed.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        f"# feed {label}",
        "",
        f"- count: **{len(rows)}**",
        f"- profile: `{profile.name}`",
        "",
        "| # | time | author | title | type | url |",
        "|---|------|--------|-------|------|-----|",
    ]
    for i, r in enumerate(rows, 1):
        title = str(r.get("title") or r.get("text") or "").replace("|", "\\|")[:60]
        author = str(r.get("author") or "-")[:30]
        t = str(r.get("time") or r.get("date") or "-")
        typ = str(r.get("type") or "-")
        url = str(r.get("url") or "")
        lines.append(f"| {i} | {t} | {author} | {title} | {typ} | {url} |")
    lines.append("")
    (folder / "feed.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"Wrote {folder} ({len(rows)} rows)")
    return folder
