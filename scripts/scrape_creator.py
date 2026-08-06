#!/usr/bin/env python3
"""
Scrape one Bilibili creator: list uploads → subtitle jobs → SQLite + txt export.

Uses HTTP/WBI space listing (no opencli browser) so it can run in GitHub Actions.

Usage:
  # from config.toml [[sources.creators]]
  python scripts/scrape_creator.py --from-config

  # one-off
  python scripts/scrape_creator.py --mid 2071007724 --name 海安雨 --slug haianyu

  # scrape + push Hugging Face
  python scripts/scrape_creator.py --from-config --push-hf

Env:
  BILI_COOKIE              recommended for bulk
  HF_TOKEN                 required if --push-hf (or .env)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _load_dotenv() -> None:
    for path in (ROOT / ".env", Path.home() / ".config" / "loop-bilibili" / "env"):
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            continue


def _read_creators_from_config(path: Path) -> list[dict]:
    import sys as _sys

    if _sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore

    if not path.is_file():
        return []
    with path.open("rb") as fh:
        data = tomllib.load(fh) or {}
    sources = data.get("sources") or {}
    # preferred: [[sources.creators]] array of tables
    items = sources.get("creators")
    out: list[dict] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("mid"):
                out.append(
                    {
                        "mid": str(it["mid"]).strip(),
                        "name": str(it.get("name") or it["mid"]).strip(),
                        "slug": str(it.get("slug") or it["mid"]).strip(),
                    }
                )
            elif isinstance(it, (str, int)):
                # legacy: creators = ["2071007724"]
                mid = str(it).strip()
                out.append({"mid": mid, "name": mid, "slug": mid})
    return out


def scrape_one(
    *,
    mid: str,
    name: str,
    slug: str,
    cookie: str,
    job_delay: float = 0.55,
    max_pages: int = 50,
) -> dict:
    from loop_bilibili.database import Database
    from loop_bilibili.ingest import refresh_source
    from loop_bilibili.sources.creator_http import CreatorHttpSource
    from loop_bilibili.sources.subtitle_bilibili import BilibiliSubtitleSource
    from loop_bilibili.worker import job_pace_sleep, process_subtitle_job

    db_path = ROOT / "data" / "v2" / f"{slug}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "data" / "v2" / f"{slug}_scrape.log"

    def log(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    t0 = time.time()
    log(f"=== scrape mid={mid} name={name} slug={slug} ===")
    db = Database(db_path)
    db.init_schema()

    # Prefer pure HTTP (CI-friendly); fall back to opencli if banned/412.
    src = CreatorHttpSource(
        mid,
        owner_name=name,
        page_size=30,
        max_pages=max_pages,
        cookie=cookie or None,
        page_delay=0.5,
    )
    try:
        summary = refresh_source(src, db)
    except Exception as exc:
        log(f"HTTP list failed ({exc}); trying opencli…")
        from loop_bilibili.sources.creator_opencli import CreatorOpencliSource

        # opencli paginates one page at a time
        total_cand = 0
        total_enq = 0
        for page in range(1, max_pages + 1):
            ocli = CreatorOpencliSource(
                mid, limit=30, page=page, owner_name=name
            )
            s = refresh_source(ocli, db)
            total_cand += s["candidates"]
            total_enq += s["enqueued"]
            log(f"  opencli page={page} candidates={s['candidates']}")
            if s["candidates"] < 30:
                break
            time.sleep(0.5)
        summary = {
            "candidates": total_cand,
            "enqueued": total_enq,
            "status": "ok",
            "source": "creator",
        }
    log(
        f"list done candidates={summary['candidates']} enqueued={summary['enqueued']} "
        f"videos={db.count_videos()}"
    )

    # ensure jobs for anything not ok/empty
    for row in db._conn.execute(
        """
        SELECT v.bvid FROM videos v
        WHERE v.bvid NOT IN (
          SELECT bvid FROM subtitles WHERE status IN ('ok','empty')
        )
        """
    ):
        db.enqueue_once("fetch_subtitle", row["bvid"])
        db._conn.execute(
            "UPDATE jobs SET status='pending', run_after=0 "
            "WHERE kind='fetch_subtitle' AND bvid=?",
            (row["bvid"],),
        )

    http_src = BilibiliSubtitleSource(cookie=cookie or None, default_language="zh")
    stats: Counter = Counter()
    risk_streak = 0
    processed = 0
    max_jobs = max(50, db.count_videos() * 3)

    while processed < max_jobs:
        job = db.claim_next_job(kinds=["fetch_subtitle"])
        if job is None:
            n = db._conn.execute(
                "UPDATE jobs SET run_after=0 WHERE kind='fetch_subtitle' "
                "AND status='pending' AND run_after>0"
            ).rowcount
            if n and risk_streak < 6:
                log(f"unlock {n} delayed; sleep")
                time.sleep(min(40, 6 + risk_streak * 4))
                continue
            break
        sub = db.get_subtitle(job.bvid, "zh")
        if sub and sub["status"] in ("ok", "empty"):
            db.complete_job(job.id)
            continue
        processed += 1
        outcome = process_subtitle_job(
            job,
            db,
            http_src,
            language="zh",
            retry_delay=45.0,
            risk_base_delay=20.0,
            risk_max_delay=180.0,
        )
        stats[outcome] += 1
        if processed % 25 == 0 or outcome in ("ok", "empty"):
            snap = db.status_snapshot()["subtitles"]
            log(
                f"#{processed} {job.bvid} -> {outcome} | "
                f"ok={snap['ok']} empty={snap['empty']} retry={snap['retry']}"
            )
        if outcome == "retry":
            j = db.get_job("fetch_subtitle", job.bvid)
            err = (j.last_error if j else "") or ""
            if "352" in err or "412" in err or "风控" in err:
                risk_streak += 1
                sl = min(60, 8 * risk_streak)
                log(f"  risk streak={risk_streak} sleep {sl}s")
                time.sleep(sl)
            else:
                risk_streak = max(0, risk_streak - 1)
                job_pace_sleep(job_delay + 0.2, 0.15)
        else:
            risk_streak = 0
            job_pace_sleep(job_delay, 0.15)

    # finalize counts
    rows = db._conn.execute(
        """
        SELECT v.bvid, v.title,
          COALESCE((
            SELECT s.status FROM subtitles s WHERE s.bvid=v.bvid
            ORDER BY CASE s.status WHEN 'ok' THEN 0 WHEN 'empty' THEN 1 ELSE 2 END
            LIMIT 1
          ), 'missing') AS st
        FROM videos v
        """
    ).fetchall()
    by = Counter(r["st"] for r in rows)

    # export txt + summary
    txt_dir = ROOT / "data" / "v2" / f"{slug}_txt"
    txt_dir.mkdir(parents=True, exist_ok=True)
    n_txt = 0
    for r in db._conn.execute("SELECT bvid, text FROM subtitles WHERE status='ok'"):
        (txt_dir / f"{r['bvid']}.txt").write_text(r["text"] or "", encoding="utf-8")
        n_txt += 1

    summary_path = ROOT / "data" / "v2" / f"{slug}_full_summary.md"
    lines = [
        f"# {name} 抓取汇总",
        "",
        f"- mid: `{mid}`",
        f"- slug: `{slug}`",
        f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- videos: **{len(rows)}**",
        f"- breakdown: `{dict(by)}`",
        f"- txt exported: {n_txt}",
        "",
    ]
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    elapsed = time.time() - t0
    result = {
        "mid": mid,
        "name": name,
        "slug": slug,
        "db": str(db_path),
        "videos": len(rows),
        "breakdown": dict(by),
        "stats": dict(stats),
        "elapsed_s": round(elapsed, 1),
        "txt_dir": str(txt_dir),
        "summary": str(summary_path),
    }
    log(f"DONE {result}")
    db.close()
    return result


def push_hf(slug: str) -> int:
    script = ROOT / "scripts" / "push_hf_v2.py"
    cmd = [
        sys.executable,
        str(script),
        "--name",
        slug,
        "--db",
        str(ROOT / "data" / "v2" / f"{slug}.db"),
        "--txt-dir",
        str(ROOT / "data" / "v2" / f"{slug}_txt"),
        "--summary",
        str(ROOT / "data" / "v2" / f"{slug}_full_summary.md"),
    ]
    print("push hf:", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def push_ms(slug: str) -> int:
    # legacy name: ModelScope path deprecated — forward to Hugging Face
    return push_hf(slug)


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    p = argparse.ArgumentParser(description="Scrape creator subtitles into v2 SQLite")
    p.add_argument("--from-config", action="store_true", help="read [[sources.creators]]")
    p.add_argument("--config", default=str(ROOT / "config.toml"))
    p.add_argument("--mid", default="")
    p.add_argument("--name", default="")
    p.add_argument("--slug", default="")
    p.add_argument(
        "--push-hf",
        action="store_true",
        help="push each finished slug to Hugging Face dataset",
    )
    p.add_argument(
        "--push-ms",
        action="store_true",
        help="(legacy alias) same as --push-hf",
    )
    p.add_argument("--job-delay", type=float, default=0.55)
    p.add_argument("--max-pages", type=int, default=50)
    args = p.parse_args(argv)

    cookie = (os.environ.get("BILI_COOKIE") or "").strip()
    # also allow cookie from config runtime
    if not cookie:
        try:
            from loop_bilibili.config import load_config

            cookie = load_config(args.config).cookie
        except Exception:
            pass

    creators: list[dict] = []
    if args.from_config:
        creators = _read_creators_from_config(Path(args.config))
        if not creators:
            print("error: no [[sources.creators]] in config", file=sys.stderr)
            return 2
    elif args.mid:
        creators = [
            {
                "mid": args.mid,
                "name": args.name or args.mid,
                "slug": args.slug or args.mid,
            }
        ]
    else:
        p.print_help()
        return 2

    if not cookie:
        print(
            "warn: BILI_COOKIE empty — bulk may hit -352; set cookie for stability",
            file=sys.stderr,
        )

    results = []
    for c in creators:
        r = scrape_one(
            mid=c["mid"],
            name=c["name"],
            slug=c["slug"],
            cookie=cookie,
            job_delay=args.job_delay,
            max_pages=args.max_pages,
        )
        results.append(r)
        if args.push_hf or args.push_ms:
            rc = push_hf(c["slug"])
            if rc != 0:
                print(f"push-hf failed for {c['slug']} rc={rc}", file=sys.stderr)
                return rc

    print("ALL", results, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
