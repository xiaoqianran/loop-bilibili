"""CLI entry: loop-bilibili init | once | worker | status."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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
    sub.add_parser("once", help="Refresh sources once and process pending jobs")
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
    sub.add_parser("status", help="Print run/job/subtitle counts")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Full implementations are wired in later milestones.
    # Stub keeps entrypoint importable and installable.
    if args.command == "init":
        print("init: not yet implemented (see feat(db) / feat(cli))", file=sys.stderr)
        return 2
    if args.command == "once":
        print("once: not yet implemented", file=sys.stderr)
        return 2
    if args.command == "worker":
        print("worker: not yet implemented", file=sys.stderr)
        return 2
    if args.command == "status":
        print("status: not yet implemented", file=sys.stderr)
        return 2
    parser.error(f"unknown command {args.command!r}")
    return 2


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
