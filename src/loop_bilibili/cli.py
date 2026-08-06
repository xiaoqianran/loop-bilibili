"""CLI entry: loop-bilibili init | once | run | worker | status | analyze."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .cadence import run_cadence
from .config import load_config
from .database import Database
from .ingest import refresh_source
from .models import AppConfig
from .preference.loader import load_preference_profile
from .preference.scorer import PreferenceScorer
from .sources._http import cookie_ready_for_batch, cookie_summary
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

    run = sub.add_parser(
        "run",
        help=(
            "Long-running cadence: homepage refresh on interval → "
            "preference score → enqueue → paced subtitle jobs"
        ),
    )
    run.add_argument(
        "--no-homepage",
        action="store_true",
        help="Skip homepage rcmd (creators only if configured)",
    )
    run.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Stop after N discovery cycles (0 = unlimited)",
    )
    run.add_argument(
        "--max-idle-cycles",
        type=int,
        default=0,
        help="Stop after N consecutive idle discovery cycles (0 = unlimited)",
    )
    run.add_argument(
        "--homepage-interval",
        type=float,
        default=None,
        help="Override sources.homepage_interval_s (seconds between refreshes)",
    )
    run.add_argument(
        "--jobs-per-cycle",
        type=int,
        default=None,
        help="Override worker.jobs_per_cycle (jobs drained per cycle)",
    )
    run.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Alias for --jobs-per-cycle",
    )

    w = sub.add_parser("worker", help="Long-running job worker loop (no discovery)")
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

    az = sub.add_parser(
        "analyze",
        help="Enqueue + run Mermaid AI post-process for ok subtitles",
    )
    az.add_argument(
        "--bvid",
        action="append",
        default=[],
        help="Specific BV id (repeatable). Default: all ok subtitles missing analysis",
    )
    az.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max videos to enqueue (0 = all)",
    )
    az.add_argument(
        "--force",
        action="store_true",
        help="Re-queue even if analyze job already done / analysis exists",
    )
    az.add_argument(
        "--max-jobs",
        type=int,
        default=0,
        help="Process at most N analyze jobs this run (0 = all enqueued)",
    )
    az.add_argument(
        "--enqueue-only",
        action="store_true",
        help="Only enqueue analyze jobs; do not process",
    )
    az.add_argument(
        "--model",
        default=None,
        help="Only this model id (default: all models in [ai].models)",
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
    analyses = snap.get("analyses") or {}
    if analyses:
        print(
            "analyses: "
            f"total={analyses.get('total', 0)} ok={analyses.get('ok', 0)} "
            f"failed={analyses.get('failed', 0)}"
        )
    print(f"runs: {snap['runs']}")


def _print_ai(cfg: AppConfig) -> None:
    ai = cfg.ai
    key_ok = bool(ai.api_key)
    models = ai.model_list()
    print(
        f"ai: enabled={ai.enabled} models={models} "
        f"default={ai.default_model or '-'} "
        f"base={ai.base_url or '-'} key={'yes' if key_ok else 'no'} "
        f"mode={ai.mode}",
        flush=True,
    )


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
        if not getattr(args, "json", False):
            _print_ai(cfg)
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


def _warn_cookie(cfg: AppConfig) -> int | None:
    """Print cookie diagnostics; return exit code if require_cookie blocks."""
    summary = cookie_summary(cfg.cookie)
    print(
        "cookie: "
        f"len={summary['length']} SESSDATA={summary['has_SESSDATA']} "
        f"bili_jct={summary['has_bili_jct']} buvid3={summary['has_buvid3']}",
        flush=True,
    )
    if cookie_ready_for_batch(cfg.cookie):
        print(
            "homepage: personalized mode (SESSDATA present — rcmd uses your account feed)",
            flush=True,
        )
    else:
        print(
            "homepage: guest mode (no SESSDATA) — feed is not account-personalized.\n"
            "  For personalized homepage: put login cookie in gitignored .env:\n"
            "    BILI_COOKIE='SESSDATA=...; bili_jct=...; DedeUserID=...'\n"
            "  Or: export BILI_COOKIE='...'  /  python scripts/set_bili_cookie.py\n"
            "  Then re-run. Cookie is sent on every homepage + subtitle request.",
            file=sys.stderr,
            flush=True,
        )
        if cfg.require_cookie:
            print(
                "error: runtime.require_cookie=true and SESSDATA missing",
                file=sys.stderr,
            )
            return 2
    print(
        f"pace: job_delay={cfg.job_delay}s ±{cfg.job_jitter}s "
        f"risk_backoff={cfg.risk_base_delay}..{cfg.risk_max_delay}s",
        flush=True,
    )
    _print_ai(cfg)
    return None


def _worker_kwargs(cfg: AppConfig) -> dict:
    return dict(
        language=cfg.subtitle_language,
        job_delay=cfg.job_delay,
        job_jitter=cfg.job_jitter,
        retry_delay=cfg.retry_delay,
        risk_base_delay=cfg.risk_base_delay,
        risk_max_delay=cfg.risk_max_delay,
        ai=cfg.ai,
    )


def _load_scorer(cfg: AppConfig) -> PreferenceScorer | None:
    if not cfg.preference_enabled:
        print("preference: disabled", flush=True)
        return None
    path = Path(cfg.preference_path)
    if not path.is_file():
        # try relative to cwd already; also next to config if needed later
        print(f"preference: file missing ({path}) — enqueue all", flush=True)
        return None
    profile = load_preference_profile(path)
    print(
        f"preference: {path} interests={len(profile.interests)} "
        f"threshold={profile.threshold}",
        flush=True,
    )
    return PreferenceScorer(profile)


def cmd_once(cfg: AppConfig, args: argparse.Namespace) -> int:
    blocked = _warn_cookie(cfg)
    if blocked is not None:
        return blocked
    scorer = _load_scorer(cfg)
    db = _open_db(cfg, args.db, init=True)
    try:
        if not args.skip_refresh:
            sources = _build_sources(cfg, no_homepage=args.no_homepage)
            if not sources:
                print("refresh: no sources configured (skip)")
            for src in sources:
                try:
                    summary = refresh_source(
                        src,
                        db,
                        scorer=scorer,
                        prefer_enabled=cfg.preference_enabled,
                    )
                    print(
                        f"refresh {summary['source']}: "
                        f"candidates={summary['candidates']} "
                        f"enqueued={summary['enqueued']} "
                        f"skipped={summary.get('skipped', 0)} "
                        f"blocked={summary.get('blocked', 0)} "
                        f"run_id={summary['run_id']}"
                    )
                    for sample in summary.get("samples") or []:
                        print(
                            f"  · {sample['decision']:7} "
                            f"{sample['score']:.3f} {sample['bvid']} "
                            f"{sample['title']}",
                            flush=True,
                        )
                except Exception as exc:
                    print(f"refresh {src.name} failed: {exc}", file=sys.stderr)
        sub_src = BilibiliSubtitleSource(
            cookie=cfg.cookie or None,
            default_language=cfg.subtitle_language,
        )
        stats = run_once(
            db,
            sub_src,
            max_jobs=max(0, int(args.max_jobs)),
            **_worker_kwargs(cfg),
        )
        print(
            "jobs: "
            f"processed={stats['processed']} ok={stats['ok']} "
            f"empty={stats['empty']} retry={stats['retry']} "
            f"failed={stats['failed']} analyze={stats['analyze']} "
            f"slept_s={stats.get('slept_s', 0):.1f}"
        )
        snap = db.status_snapshot()
        print(f"schema: {snap['schema']}")
        print(
            f"totals: videos={snap['videos']} "
            f"jobs_pending={snap['jobs']['pending']} "
            f"jobs_done={snap['jobs']['done']} "
            f"analyses_ok={(snap.get('analyses') or {}).get('ok', 0)}"
        )
        return 0
    finally:
        db.close()


def cmd_worker(cfg: AppConfig, args: argparse.Namespace) -> int:
    blocked = _warn_cookie(cfg)
    if blocked is not None:
        return blocked
    db = _open_db(cfg, args.db, init=True)
    try:
        sub_src = BilibiliSubtitleSource(
            cookie=cfg.cookie or None,
            default_language=cfg.subtitle_language,
        )
        stats = worker_loop(
            db,
            sub_src,
            poll_interval=cfg.poll_interval,
            max_jobs=max(0, int(args.max_jobs)),
            max_idle=max(0, int(args.max_idle)),
            **_worker_kwargs(cfg),
        )
        print(
            "worker done: "
            f"processed={stats['processed']} ok={stats['ok']} "
            f"empty={stats['empty']} retry={stats['retry']} "
            f"failed={stats['failed']} analyze={stats['analyze']} "
            f"idle_polls={stats['idle_polls']} "
            f"slept_s={stats.get('slept_s', 0):.1f}"
        )
        _print_snapshot(db.status_snapshot())
        return 0
    finally:
        db.close()


def _print_cycle(summary: dict) -> None:
    print(
        f"cycle {summary.get('cycle')}: "
        f"candidates={summary.get('candidates', 0)} "
        f"enqueued={summary.get('enqueued', 0)} "
        f"skipped={summary.get('skipped', 0)} "
        f"blocked={summary.get('blocked', 0)} "
        f"processed={summary.get('processed', 0)} "
        f"ok={summary.get('ok', 0)} "
        f"empty={summary.get('empty', 0)} "
        f"retry={summary.get('retry', 0)} "
        f"failed={summary.get('failed', 0)}",
        flush=True,
    )
    for src in summary.get("sources") or []:
        if not isinstance(src, dict):
            continue
        for sample in (src.get("samples") or [])[:8]:
            print(
                f"  · {sample['decision']:7} "
                f"{sample['score']:.3f} {sample['bvid']} "
                f"{sample['title']}",
                flush=True,
            )


def cmd_run(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Multi-cycle homepage → preference → paced subtitles (long-run entry)."""
    blocked = _warn_cookie(cfg)
    if blocked is not None:
        return blocked
    scorer = _load_scorer(cfg)
    interval = (
        float(args.homepage_interval)
        if args.homepage_interval is not None
        else float(cfg.homepage_interval_s)
    )
    jpc = cfg.jobs_per_cycle
    if args.jobs_per_cycle is not None:
        jpc = int(args.jobs_per_cycle)
    elif args.max_jobs is not None:
        jpc = int(args.max_jobs)

    print(
        f"cadence: interval={interval}s jobs_per_cycle={jpc} "
        f"max_cycles={args.max_cycles} max_idle_cycles={args.max_idle_cycles}",
        flush=True,
    )
    db = _open_db(cfg, args.db, init=True)
    try:
        sources = _build_sources(cfg, no_homepage=args.no_homepage)
        if not sources:
            print(
                "error: no sources (enable homepage or add creators)",
                file=sys.stderr,
            )
            return 2
        sub_src = BilibiliSubtitleSource(
            cookie=cfg.cookie or None,
            default_language=cfg.subtitle_language,
        )
        stats = run_cadence(
            db,
            sources,
            sub_src,
            scorer=scorer,
            prefer_enabled=cfg.preference_enabled,
            homepage_interval_s=interval,
            poll_interval=cfg.poll_interval,
            jobs_per_cycle=max(0, jpc),
            max_cycles=max(0, int(args.max_cycles)),
            max_idle_cycles=max(0, int(args.max_idle_cycles)),
            **_worker_kwargs(cfg),
        )
        for cyc in stats.get("cycle_summaries") or []:
            _print_cycle(cyc)
        print(
            "cadence done: "
            f"cycles={stats['cycles']} candidates={stats['candidates']} "
            f"enqueued={stats['enqueued']} skipped={stats['skipped']} "
            f"blocked={stats['blocked']} processed={stats['processed']} "
            f"ok={stats['ok']} empty={stats['empty']} "
            f"retry={stats['retry']} failed={stats['failed']} "
            f"analyze={stats['analyze']} slept_s={stats.get('slept_s', 0):.1f}",
            flush=True,
        )
        _print_snapshot(db.status_snapshot())
        return 0
    finally:
        db.close()


def cmd_analyze(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Enqueue + drain Mermaid analyze jobs (one job per model × bvid)."""
    from .ai_worker import enqueue_analyze_for_models

    _print_ai(cfg)
    if not cfg.ai.enabled or not cfg.ai.api_key or not cfg.ai.base_url:
        print(
            "error: AI not configured. Set [ai] in config.toml and "
            "AI_API_KEY in .env (see README).",
            file=sys.stderr,
        )
        return 2
    if not cfg.ai.model_list():
        print("error: no AI models configured", file=sys.stderr)
        return 2

    db = _open_db(cfg, args.db, init=True)
    try:
        if args.bvid:
            bvids = [str(b).strip() for b in args.bvid if str(b).strip()]
        else:
            bvids = db.list_ok_subtitle_bvids(limit=max(0, int(args.limit)))

        # optional: only one model
        models = cfg.ai.model_list()
        if getattr(args, "model", None):
            only = str(args.model).strip()
            if only not in models:
                # still allow explicit model id even if not in config list
                models = [only]
            else:
                models = [only]
            # temporarily narrow for enqueue helper
            from dataclasses import replace
            ai = replace(cfg.ai, models=models)
        else:
            ai = cfg.ai

        enqueued = 0
        for bvid in bvids:
            enqueued += enqueue_analyze_for_models(
                db, bvid, ai, force=bool(args.force)
            )

        print(
            f"analyze enqueue: jobs={enqueued} pool_bvids={len(bvids)} "
            f"models={ai.model_list()}",
            flush=True,
        )
        if args.enqueue_only:
            _print_snapshot(db.status_snapshot())
            return 0

        class _NoSub:
            def fetch(self, bvid: str) -> object:
                raise RuntimeError("analyze-only run should not fetch subtitles")

        max_jobs = int(args.max_jobs) if args.max_jobs else max(enqueued, 1) * 2
        stats = run_once(
            db,
            _NoSub(),
            max_jobs=max(0, max_jobs),
            **_worker_kwargs(cfg),
        )
        print(
            "analyze jobs: "
            f"processed={stats['processed']} analyze_ok={stats['analyze']} "
            f"failed={stats['failed']}"
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
    if args.command == "run":
        return cmd_run(cfg, args)
    if args.command == "worker":
        return cmd_worker(cfg, args)
    if args.command == "analyze":
        return cmd_analyze(cfg, args)
    parser.error(f"unknown command {args.command!r}")
    return 2


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
