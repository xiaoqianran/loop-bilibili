#!/usr/bin/env python3
"""
Long-running homepage cadence + incremental HF push.

  刷首页 → preferences 筛选 → 抓字幕 → (可选) Mermaid analyze → 有进展就 push HF

Does NOT wait for all subtitles to be analyzed. Each cycle:
  1. refresh homepage feed
  2. process up to --jobs-per-cycle queue items (subtitle + analyze)
  3. if ok/analyze counts rose (or --push-every), push data/v2/loop.db → HF
  4. optionally trigger Pages workflow (best-effort, rate-limited)

Usage:
  python scripts/homepage_daemon.py
  python scripts/homepage_daemon.py --interval 300 --jobs-per-cycle 20
  python scripts/homepage_daemon.py --no-ai          # subtitles only
  python scripts/homepage_daemon.py --no-push
  python scripts/homepage_daemon.py --trigger-pages  # also gh workflow run

Stop:
  kill $(cat logs/homepage_daemon.pid)
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

LOG_DIR = ROOT / "logs"
PID_FILE = LOG_DIR / "homepage_daemon.pid"
STATUS_FILE = LOG_DIR / "homepage_daemon.status"
DEFAULT_LOG = LOG_DIR / "homepage_daemon.log"


def _load_dotenv() -> None:
    from loop_bilibili.config import load_dotenv_files

    load_dotenv_files(ROOT / ".env")


def _log(msg: str, log_path: Path) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _write_status(data: dict) -> None:
    import json

    try:
        STATUS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def _snapshot(db_path: Path) -> dict:
    from loop_bilibili.database import Database

    db = Database(db_path)
    db.init_schema()
    try:
        snap = db.status_snapshot()
        ok = int((snap.get("subtitles") or {}).get("ok") or 0)
        empty = int((snap.get("subtitles") or {}).get("empty") or 0)
        analyses_ok = int((snap.get("analyses") or {}).get("ok") or 0)
        pending = int((snap.get("jobs") or {}).get("pending") or 0)
        videos = int(snap.get("videos") or 0)
        return {
            "videos": videos,
            "ok": ok,
            "empty": empty,
            "analyses_ok": analyses_ok,
            "pending": pending,
            "snap": snap,
        }
    finally:
        db.close()


def _run_once(
    *,
    max_jobs: int,
    log_path: Path,
    config: Path,
    db: Path,
) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    cmd = [
        sys.executable,
        "-m",
        "loop_bilibili",
        "--config",
        str(config),
        "--db",
        str(db),
        "once",
        "--max-jobs",
        str(max(0, max_jobs)),
    ]
    _log(f"$ {' '.join(cmd)}", log_path)
    with log_path.open("a", encoding="utf-8") as fh:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return int(proc.returncode)


def _push_hf(*, name: str, db: Path, log_path: Path) -> int:
    script = ROOT / "scripts" / "push_hf_v2.py"
    cmd = [
        sys.executable,
        str(script),
        "--name",
        name,
        "--db",
        str(db),
    ]
    _log(f"push hf: {' '.join(cmd)}", log_path)
    with log_path.open("a", encoding="utf-8") as fh:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=os.environ.copy(),
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return int(proc.returncode)


def _trigger_pages(log_path: Path) -> int:
    # Best-effort; ignore if gh missing or queue busy
    cmd = ["gh", "workflow", "run", "pages.yml", "-f", "snapshots=loop"]
    _log(f"trigger pages: {' '.join(cmd)}", log_path)
    with log_path.open("a", encoding="utf-8") as fh:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return int(proc.returncode)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Homepage discover → subtitle → HF push daemon")
    p.add_argument("--config", default=str(ROOT / "config.toml"))
    p.add_argument("--db", default=str(ROOT / "data" / "v2" / "loop.db"))
    p.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Seconds between homepage cycles (0 = config homepage_interval_s)",
    )
    p.add_argument("--jobs-per-cycle", type=int, default=20)
    p.add_argument(
        "--hf-name",
        default="loop",
        help="HF snapshot name for loop.db (default: loop)",
    )
    p.add_argument("--no-ai", action="store_true", help="Disable Mermaid analyze this run")
    p.add_argument(
        "--single-model",
        default="openai/gpt-oss-120b",
        help="When AI on, only this model (keeps pace reasonable). Empty = use config list",
    )
    p.add_argument("--no-push", action="store_true")
    p.add_argument(
        "--push-every",
        type=int,
        default=3,
        help="Force HF push every N cycles even if counts unchanged (0=only on progress)",
    )
    p.add_argument("--trigger-pages", action="store_true")
    p.add_argument(
        "--pages-min-interval",
        type=float,
        default=1800.0,
        help="Min seconds between Pages workflow triggers",
    )
    p.add_argument("--log", default=str(DEFAULT_LOG))
    p.add_argument("--max-cycles", type=int, default=0, help="0 = forever")
    args = p.parse_args(argv)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log)
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = ROOT / db_path

    _load_dotenv()

    # Prefer single model for long-run throughput unless user clears it
    if args.no_ai:
        os.environ["AI_ENABLED"] = "false"
    elif args.single_model.strip():
        os.environ["AI_MODELS"] = args.single_model.strip()
        os.environ["AI_DEFAULT_MODEL"] = args.single_model.strip()
        os.environ.setdefault("AI_ENABLED", "true")

    from loop_bilibili.config import load_config

    cfg = load_config(args.config)
    interval = float(args.interval) if args.interval > 0 else float(cfg.homepage_interval_s)
    interval = max(30.0, interval)  # never hammer homepage

    # PID
    PID_FILE.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    stop = {"flag": False}

    def _handle(sig, _frame):  # type: ignore[no-untyped-def]
        stop["flag"] = True
        _log(f"signal {sig} — graceful stop after cycle", log_path)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    _log(
        f"daemon start pid={os.getpid()} interval={interval}s "
        f"jobs={args.jobs_per_cycle} ai={'off' if args.no_ai else os.environ.get('AI_MODELS') or 'config'} "
        f"push={'off' if args.no_push else args.hf_name} cookie_len={len(cfg.cookie or '')}",
        log_path,
    )

    prev = _snapshot(db_path)
    _log(
        f"baseline videos={prev['videos']} ok={prev['ok']} "
        f"analyses_ok={prev['analyses_ok']} pending={prev['pending']}",
        log_path,
    )

    cycle = 0
    last_pages_at = 0.0
    pushes = 0
    while not stop["flag"]:
        cycle += 1
        t0 = time.time()
        rc = _run_once(
            max_jobs=int(args.jobs_per_cycle),
            log_path=log_path,
            config=Path(args.config),
            db=db_path,
        )
        cur = _snapshot(db_path)
        progress = (
            cur["ok"] > prev["ok"]
            or cur["analyses_ok"] > prev["analyses_ok"]
            or cur["videos"] > prev["videos"]
        )
        force_push = bool(args.push_every) and (cycle % max(1, args.push_every) == 0)
        _log(
            f"cycle {cycle} rc={rc} "
            f"Δok={cur['ok'] - prev['ok']} Δana={cur['analyses_ok'] - prev['analyses_ok']} "
            f"Δvid={cur['videos'] - prev['videos']} "
            f"pending={cur['pending']} elapsed={time.time() - t0:.1f}s "
            f"progress={progress}",
            log_path,
        )

        pushed = False
        if not args.no_push and (progress or force_push):
            prc = _push_hf(name=args.hf_name, db=db_path, log_path=log_path)
            pushed = prc == 0
            pushes += 1 if pushed else 0
            _log(f"push rc={prc} total_pushes={pushes}", log_path)
            if (
                pushed
                and args.trigger_pages
                and (time.time() - last_pages_at) >= float(args.pages_min_interval)
            ):
                trc = _trigger_pages(log_path)
                last_pages_at = time.time()
                _log(f"pages trigger rc={trc}", log_path)

        _write_status(
            {
                "pid": os.getpid(),
                "cycle": cycle,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "interval_s": interval,
                "jobs_per_cycle": args.jobs_per_cycle,
                "last_rc": rc,
                "videos": cur["videos"],
                "ok": cur["ok"],
                "analyses_ok": cur["analyses_ok"],
                "pending": cur["pending"],
                "progress": progress,
                "pushes": pushes,
                "last_push": pushed,
                "stop_requested": stop["flag"],
            }
        )
        prev = cur

        if args.max_cycles and cycle >= int(args.max_cycles):
            _log(f"max-cycles={args.max_cycles} reached — exit", log_path)
            break

        # sleep remaining interval (cycle already spent some time)
        spent = time.time() - t0
        wait = max(0.0, interval - spent)
        # wake early on stop
        end = time.time() + wait
        while time.time() < end and not stop["flag"]:
            time.sleep(min(5.0, end - time.time()))

    _log(f"daemon stopped after {cycle} cycles", log_path)
    try:
        if PID_FILE.is_file() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
