#!/usr/bin/env python3
"""
Zero-cookie homepage smoke test.

  PYTHONPATH=src python scripts/try_homepage_guest.py
  PYTHONPATH=src python scripts/try_homepage_guest.py --pages 2 --max-jobs 10

Does NOT require BILI_COOKIE. Writes data/v2/homepage_guest.db
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Force guest mode for this trial
os.environ.pop("BILI_COOKIE", None)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Guest homepage rcmd + subtitle sample")
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--ps", type=int, default=12)
    p.add_argument("--max-jobs", type=int, default=8)
    p.add_argument(
        "--db",
        default=str(ROOT / "data" / "v2" / "homepage_guest.db"),
    )
    args = p.parse_args(argv)

    from loop_bilibili.database import Database
    from loop_bilibili.ingest import refresh_source
    from loop_bilibili.sources.homepage_rcmd import HomepageRcmdSource
    from loop_bilibili.sources.subtitle_bilibili import BilibiliSubtitleSource
    from loop_bilibili.worker import job_pace_sleep, process_subtitle_job

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    db.init_schema()

    print("mode: guest (no cookie)", flush=True)
    src = HomepageRcmdSource(
        pages=max(1, args.pages),
        ps=max(1, args.ps),
        cookie=None,
    )
    t0 = time.time()
    summary = refresh_source(src, db)
    print(
        f"homepage: candidates={summary['candidates']} enqueued={summary['enqueued']} "
        f"elapsed={time.time()-t0:.2f}s videos={db.count_videos()}",
        flush=True,
    )

    http = BilibiliSubtitleSource(cookie=None)
    stats = {"ok": 0, "empty": 0, "retry": 0, "failed": 0}
    for i in range(max(0, args.max_jobs)):
        job = db.claim_next_job(kinds=["fetch_subtitle"])
        if job is None:
            break
        out = process_subtitle_job(job, db, http, language="zh")
        stats[out] = stats.get(out, 0) + 1
        print(f"  [{i+1}] {job.bvid} -> {out}", flush=True)
        job_pace_sleep(0.6, 0.1)

    snap = db.status_snapshot()
    print("subtitles", snap["subtitles"], flush=True)
    print("stats", stats, flush=True)
    row = db._conn.execute(
        "SELECT bvid, title FROM videos v "
        "JOIN subtitles s ON s.bvid=v.bvid AND s.status='ok' LIMIT 1"
    ).fetchone()
    if row:
        print("sample ok:", row["bvid"], (row["title"] or "")[:40], flush=True)
    db.close()
    print("db:", db_path.resolve(), flush=True)
    return 0 if summary["candidates"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
