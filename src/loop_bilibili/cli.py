"""CLI entry: loop-bilibili init | once | worker | status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import load_config
from .database import Database
from .ingest import refresh_source
from .models import AppConfig
from .sources.creator_opencli import CreatorOpencliSource
from .sources.homepage_rcmd import HomepageRcmdSource
from .sources.subtitle_bilibili import BilibiliSubtitleSource
from .worker import run_once, worker_loop


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="loop-bilibili",
        description="Bilibili subtitle ingest service (v2)",
    )
    p.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: ./config.toml)",
    )
    p.add_argument(
        "--db",
        default=None,
        help="Override database path (default: config database.path)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create database schema (SQLite WAL)")

    once = sub.add_parser(
        "once", help="Refresh sources once and process pending jobs"
    )
    once.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Only process jobs; do not refresh sources",
    )
    once.add_argument(
        "--no-homepage",
        action="store_true",
        help="Skip homepage rcmd refresh",
    )
    once.add_argument(
        "--max-jobs",
        type=int,
        default=50,
        help="Max jobs to process after refresh (default 50)",
    )

    w = sub.add_parser("worker", help="Long-running job worker loop")
    w.add_argument(
        "--max-jobs",
        type=int,
        default=0,
        help="Stop after N jobs (0 = unlimited)",
    )
    w.add_argument(
        "--max-idle",
        type=int,
        default=0,
        help="Stop after N idle polls (0 = unlimited)",
    )

    st = sub.add_parser("status", help="Print run/job/subtitle counts")
    st.add_argument(
        "--json",
        action="store_true",
        help="Emit status snapshot as JSON",
    )
    return p


def _resolve_db_path(cfg: AppConfig, db_override: str | None) -> Path:
    return Path(db_override or cfg.database_path)


def _open_db(cfg: AppConfig, db_override: str | None, *, init: bool = False) -> Database:
    path = _resolve_db_path(cfg, db_override)
    db = Database(path)
    if init or not path.exists() or path.stat().st_size == 0:
        db.init_schema()
    else:
        # ensure schema exists on existing files
        db.init_schema()
    return db


def _print_snapshot(snap: dict, *, json_mode: bool = False) -> None:
    if json_mode:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return
    print(f"schema: {snap['schema']}")
    print(f"database: {snap['database']}")
    print(f"videos: {snap['videos']}")
    jobs = snap["jobs"]
    print(
        "jobs: "
        f"total={jobs['total']} pending={jobs['pending']} "
        f"running={jobs['running']} done={jobs['done']} "
        f"failed={jobs['failed']}"
    )
    subs = snap["subtitles"]
    print(
        "subtitles: "
        f"total={subs['total']} ok={subs['ok']} empty={subs['empty']} "
        f"retry={subs['retry']} failed={subs['failed']}"
    )
    print(f"runs: {snap['runs']}")


def cmd_init(cfg: AppConfig, args: argparse.Namespace) -> int:
    db = _open_db(cfg, args.db, init=True)
    try:
        _print_snapshot(db.status_snapshot())
        return 0
    finally:
        db.close()


def cmd_status(cfg: AppConfig, args: argparse.Namespace) -> int:
    path = _resolve_db_path(cfg, args.db)
    if not path.exists():
        print(f"database missing: {path}", file=sys.stderr)
        print("hint: run `loop-bilibili init` first", file=sys.stderr)
        return 1
    db = _open_db(cfg, args.db)
    try:
        _print_snapshot(db.status_snapshot(), json_mode=getattr(args, "json", False))
        return 0
    finally:
        db.close()


def _build_sources(
    cfg: AppConfig, *, no_homepage: bool = False
) -> list:
    sources: list = []
    for mid in cfg.creators:
        sources.append(CreatorOpencliSource(mid))
    if cfg.homepage_enabled and not no_homepage:
        sources.append(
            HomepageRcmdSource(
                pages=cfg.homepage_pages,
                ps=cfg.homepage_ps,
                cookie=cfg.cookie or None,
            )
        )
    return sources


def cmd_once(cfg: AppConfig, args: argparse.Namespace) -> int:
    db = _open_db(cfg, args.db, init=True)
    try:
        refresh_results = []
        if not args.skip_refresh:
            sources = _build_sources(cfg, no_homepage=args.no_homepage)
            if not sources:
                print("refresh: no sources configured (skip)")
            for src in sources:
                try:
                    summary = refresh_source(src, db)
                    refresh_results.append(summary)
                    print(
                        f"refresh {summary['source']}: "
                        f"candidates={summary['candidates']} "
                        f"enqueued={summary['enqueued']} "
                        f"run_id={summary['run_id']}"
                    )
                except Exception as exc:
                    print(f"refresh {src.name} failed: {exc}", file=sys.stderr)
                    # continue other sources / still process jobs
        sub_src = BilibiliSubtitleSource(
            cookie=cfg.cookie or None,
            default_language=cfg.subtitle_language,
        )
        stats = run_once(
            db,
            sub_src,
            language=cfg.subtitle_language,
            max_jobs=max(0, int(args.max_jobs)),
        )
        print(
            "jobs: "
            f"processed={stats['processed']} ok={stats['ok']} "
            f"empty={stats['empty']} retry={stats['retry']} "
            f"failed={stats['failed']} analyze={stats['analyze']}"
        )
        snap = db.status_snapshot()
        print(f"schema: {snap['schema']}")
        print(
            f"totals: videos={snap['videos']} "
            f"jobs_pending={snap['jobs']['pending']} "
            f"jobs_done={snap['jobs']['done']}"
        )
        return 0
    finally:
        db.close()


def cmd_worker(cfg: AppConfig, args: argparse.Namespace) -> int:
    db = _open_db(cfg, args.db, init=True)
    try:
        sub_src = BilibiliSubtitleSource(
            cookie=cfg.cookie or None,
            default_language=cfg.subtitle_language,
        )
        stats = worker_loop(
            db,
            sub_src,
            language=cfg.subtitle_language,
            poll_interval=cfg.poll_interval,
            max_jobs=max(0, int(args.max_jobs)),
            max_idle=max(0, int(args.max_idle)),
        )
        print(
            "worker done: "
            f"processed={stats['processed']} ok={stats['ok']} "
            f"empty={stats['empty']} retry={stats['retry']} "
            f"failed={stats['failed']} analyze={stats['analyze']} "
            f"idle_polls={stats['idle_polls']}"
        )
        _print_snapshot(db.status_snapshot())
        return 0
    finally:
        db.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg = load_config(args.config)
    if args.command == "init":
        return cmd_init(cfg, args)
    if args.command == "status":
        return cmd_status(cfg, args)
    if args.command == "once":
        return cmd_once(cfg, args)
    if args.command == "worker":
        return cmd_worker(cfg, args)
    parser.error(f"unknown command {args.command!r}")
    return 2


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
