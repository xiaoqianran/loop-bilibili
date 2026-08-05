"""推荐首页导出：packages/bili_subbatch.homepage。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from bili_subbatch.homepage import (
    fetch_homepage,
    write_homepage_export,
)

logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


def export_homepage(
    out_root: Path,
    *,
    limit: int = 20,
    pages: int = 1,
    page_size: int = 12,
    fresh_idx: int = 1,
    page_delay: float = 0.8,
    page_jitter: float = 0.2,
    videos_only: bool = True,
    cookie: str | None = None,
    stamp_subdir: bool = False,
) -> Path:
    """
    拉取推荐首页并写入 out_root/homepage/（或带时间戳子目录）。

    返回导出目录路径（内含 homepage.json / bvids.txt 等）。
    """
    out_root = Path(out_root)
    if stamp_subdir:
        import time

        folder = out_root / "homepage" / time.strftime("%Y%m%d_%H%M%S")
    else:
        folder = out_root / "homepage"

    log(
        f"homepage rcmd limit={limit} pages={pages} page_size={page_size} "
        f"fresh_idx={fresh_idx} videos_only={videos_only}"
    )
    cards = fetch_homepage(
        limit=limit,
        pages=pages,
        page_size=page_size,
        fresh_idx_start=fresh_idx,
        page_delay=page_delay,
        page_jitter=page_jitter,
        videos_only=videos_only,
        cookie=cookie,
    )
    meta: dict[str, Any] = {
        "limit": limit,
        "pages": pages,
        "page_size": page_size,
        "fresh_idx_start": fresh_idx,
        "videos_only": videos_only,
    }
    write_homepage_export(cards, folder, meta=meta)
    bvids = [c.bvid for c in cards if c.bvid]
    log(f"homepage done → {folder} cards={len(cards)} bvids={len(bvids)}")
    if bvids:
        log(f"bvids file: {folder / 'bvids.txt'}")
    return folder
