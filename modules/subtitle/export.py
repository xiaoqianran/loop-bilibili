"""批量字幕 — 默认 SubBatch 协议（packages/bili_subbatch），不经 opencli。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loop_core.rate_limit import Profile

from bili_subbatch.batch import run_batch
from bili_subbatch.client import fetch_subtitle as _fetch_one

# opencli 时代 profile 名 → subbatch 友好间隔（秒）
_PROFILE_PACE = {
    "conservative": (0.5, 0.2),
    "balanced": (0.4, 0.15),
    "aggressive": (0.25, 0.1),
}


def fetch_subtitle(
    bvid: str,
    *,
    cookie: str | None = None,
) -> dict[str, Any]:
    """单条字幕，返回与 batch 一致的 row dict。"""
    r = _fetch_one(bvid, cookie=cookie)
    return r.to_row()


def export_subtitles(
    bvids: list[str],
    out_dir: Path,
    profile: Profile | None = None,
    *,
    resume: bool = True,
    cookie: str | None = None,
    write_srt_files: bool = True,
    delay: float | None = None,
    jitter: float | None = None,
) -> Path:
    """
    串行批量字幕（SubBatch HTTP）。

    - 未显式传 delay/jitter 时：按 profile 名映射到 subbatch 温和间隔
      （不再沿用 opencli 的 2s item_delay）。
    - 显式 --item-delay / --item-jitter 优先生效。
    """
    if delay is None or jitter is None:
        name = getattr(profile, "name", None) if profile is not None else None
        d0, j0 = _PROFILE_PACE.get(name or "balanced", (0.4, 0.15))
        if delay is None:
            delay = d0
        if jitter is None:
            jitter = j0

    return run_batch(
        bvids,
        Path(out_dir),
        delay=float(delay),
        jitter=float(jitter),
        resume=resume,
        cookie=cookie,
        write_srt_files=write_srt_files,
    )
