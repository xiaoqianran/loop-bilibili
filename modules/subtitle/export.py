"""批量字幕 — SubtitlePipeline（packages/bili_subbatch），不经 opencli。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from loop_core.rate_limit import Profile

from bili_subbatch.client import fetch_subtitle as _fetch_one
from bili_subbatch.models import BatchConfig, BatchStats
from bili_subbatch.pipeline import SubtitlePipeline
from bili_subbatch.processors.base import SubtitleProcessor

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


def resolve_pace(
    profile: Profile | None,
    *,
    delay: float | None = None,
    jitter: float | None = None,
) -> tuple[float, float]:
    if delay is None or jitter is None:
        name = getattr(profile, "name", None) if profile is not None else None
        d0, j0 = _PROFILE_PACE.get(name or "balanced", (0.4, 0.15))
        if delay is None:
            delay = d0
        if jitter is None:
            jitter = j0
    return float(delay), float(jitter)


def export_subtitles(
    bvids: list[str],
    out_dir: Path,
    profile: Profile | None = None,
    *,
    resume: bool = True,
    cookie: str | None = None,
    write_srt_files: bool = True,
    write_txt: bool = True,
    delay: float | None = None,
    jitter: float | None = None,
    processors: Sequence[SubtitleProcessor] | None = None,
    config: BatchConfig | None = None,
) -> Path:
    """
    串行批量字幕（pipeline）。

    - 未显式传 delay/jitter 时：按 profile 名映射到 subbatch 温和间隔
    - 可注入 processors（LLM / 清洗 / 通知…）
    """
    d, j = resolve_pace(profile, delay=delay, jitter=jitter)
    cfg = config or BatchConfig(
        delay=d,
        jitter=j,
        resume=resume,
        cookie=cookie,
        write_srt=write_srt_files,
        write_txt=write_txt,
    )
    if config is not None:
        # explicit kwargs still win when provided
        if delay is not None:
            cfg.delay = d
        if jitter is not None:
            cfg.jitter = j
        if cookie is not None:
            cfg.cookie = cookie
        cfg.resume = resume
        cfg.write_srt = write_srt_files
        cfg.write_txt = write_txt

    pipe = SubtitlePipeline(cfg, processors=processors)
    stats: BatchStats = pipe.run(bvids, Path(out_dir))
    return Path(stats.out_dir)
