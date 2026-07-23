"""catalog 导出编排：采集投稿 + 官方合集/系列 → 写盘；或离线 rebuild。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from loop_core.progress import clear_progress
from loop_core.rate_limit import Profile

from .collections import (
    CollectionItem,
    enrich_videos_with_collections,
    fetch_all_collections,
)
from .collector import CatalogCollector
from .models import normalize_video, slugify
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
    fetch_collections: bool = True,
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

    collections: list[CollectionItem] = []
    if fetch_collections:
        log(f"Fetching official 合集和系列 for mid={uid} ...")
        try:
            collections = fetch_all_collections(
                str(uid),
                delay=max(0.25, float(profile.page_delay) * 0.25),
                log=log,
            )
            log(
                f"  official collections: "
                f"{sum(1 for c in collections if c.kind=='season')} 合集 + "
                f"{sum(1 for c in collections if c.kind=='series')} 系列"
            )
            videos = enrich_videos_with_collections(videos, collections)
        except Exception as e:
            log(f"  ! official collections failed ({e}); series fall back to title heuristic")
            videos = [normalize_video(v) for v in videos]
    else:
        videos = [normalize_video(v) for v in videos]

    # persist official collections payload
    coll_path = folder / "collections.json"
    coll_path.write_text(
        json.dumps(
            {
                "mid": str(uid),
                "source": "bilibili space seasons_series_list + archives",
                "count": len(collections),
                "items": [c.to_dict() for c in collections],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    write_catalog_files(
        folder,
        uid,
        up_name,
        videos,
        profile_name=profile.name,
        collections=collections,
    )
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

    collections: list[CollectionItem] = []
    coll_path = folder / "collections.json"
    if coll_path.exists():
        try:
            raw = json.loads(coll_path.read_text(encoding="utf-8"))
            for it in raw.get("items") or []:
                collections.append(
                    CollectionItem(
                        kind=str(it.get("kind") or "season"),
                        id=int(it.get("id") or 0),
                        name=str(it.get("name") or ""),
                        total=int(it.get("total") or 0),
                        description=str(it.get("description") or ""),
                        cover=str(it.get("cover") or ""),
                        archives=list(it.get("archives") or []),
                    )
                )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            collections = []

    write_catalog_files(
        folder,
        uid,
        up_name,
        videos,
        profile_name="rebuild",
        collections=collections or None,
    )
    return folder
