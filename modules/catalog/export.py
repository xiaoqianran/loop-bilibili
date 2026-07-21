"""catalog 导出编排：采集 → 写盘；或离线 rebuild。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from loop_core.progress import clear_progress
from loop_core.rate_limit import Profile

from .collector import CatalogCollector
from .models import slugify
from .writer import write_catalog_files

logger = logging.getLogger(__name__)


def log(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


def export_catalog(
    uid: str,
    up_name: str,
    out_root: Path,
    profile: Profile,
    *,
    order: str = "pubdate",
    resume: bool = False,
    max_pages: int | None = None,
) -> Path:
    folder = out_root / f"{uid}-{slugify(up_name)}"
    folder.mkdir(parents=True, exist_ok=True)

    collector = CatalogCollector(profile, order=order)
    log(f"Fetching all videos for {up_name} ({uid}) ...")
    videos = collector.fetch_all(
        uid, folder, resume=resume, max_pages=max_pages
    )
    if not videos:
        raise SystemExit(f"未获取到任何视频：uid={uid}")

    write_catalog_files(folder, uid, up_name, videos, profile_name=profile.name)
    clear_progress(folder)
    log("Progress files cleared (export complete).")
    return folder


def rebuild_from_folder(folder: Path, name_override: str = "") -> Path:
    folder = folder.resolve()
    all_json = folder / "all.json"
    partial = folder / "all.partial.json"
    meta_path = folder / "meta.json"

    if all_json.exists():
        videos = json.loads(all_json.read_text(encoding="utf-8"))
    elif partial.exists():
        videos = json.loads(partial.read_text(encoding="utf-8"))
        log("Rebuilding from all.partial.json (incomplete fetch?)")
    else:
        raise SystemExit(f"No all.json or all.partial.json in {folder}")

    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    uid = str(meta.get("uid") or "")
    up_name = name_override or str(meta.get("name") or "")
    if not uid:
        m = re.match(r"^(\d+)-(.+)$", folder.name)
        if m:
            uid, up_name = m.group(1), up_name or m.group(2)
    if not uid:
        raise SystemExit("Cannot determine uid from folder/meta")
    if not up_name:
        up_name = uid

    write_catalog_files(folder, uid, up_name, videos, profile_name="rebuild")
    return folder
