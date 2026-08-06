#!/usr/bin/env python3
"""
Download v2 snapshots from ModelScope private dataset into data/v2/.

Usage:
  python scripts/fetch_modelscope_v2.py
  python scripts/fetch_modelscope_v2.py --name haianyu
  python scripts/fetch_modelscope_v2.py --all

Auth: MODELSCOPE_API_TOKEN or gitignored .env (same as push script).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    for path in (ROOT / ".env", Path.home() / ".config" / "loop-bilibili" / "env"):
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            continue


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch v2 snapshots from ModelScope")
    p.add_argument(
        "--repo",
        default=os.environ.get("MODELSCOPE_DATASET_REPO")
        or "yuminghui/loop-bilibili-v2",
        help="dataset repo id owner/name",
    )
    p.add_argument("--name", action="append", default=[], help="snapshot name(s)")
    p.add_argument("--all", action="store_true", help="download full dataset")
    p.add_argument(
        "--out",
        default=str(ROOT / "data" / "v2"),
        help="output data/v2 directory",
    )
    p.add_argument(
        "--cache",
        default=str(ROOT / "data" / "v2" / "_ms_download"),
        help="download cache dir",
    )
    args = p.parse_args(argv)

    _load_dotenv()
    token = (
        os.environ.get("MODELSCOPE_API_TOKEN")
        or os.environ.get("MODELSCOPE_SDK_TOKEN")
        or ""
    ).strip()
    if not token:
        print("error: MODELSCOPE_API_TOKEN required", file=sys.stderr)
        return 2

    try:
        from modelscope import snapshot_download
        from modelscope.hub.api import HubApi
    except ImportError:
        print("error: pip install modelscope", file=sys.stderr)
        return 2

    api = HubApi()
    api.login(token)

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    allow = None
    if args.name and not args.all:
        # only pull matching snapshot paths
        allow = []
        for n in args.name:
            allow.extend(
                [
                    f"snapshots/{n}/*",
                    f"snapshots/{n}/**",
                    "manifest.json",
                    "README.md",
                ]
            )

    print(f"downloading {args.repo} ...", flush=True)
    local = snapshot_download(
        repo_id=args.repo,
        repo_type="dataset",
        local_dir=str(cache),
        token=token,
        allow_patterns=allow,
    )
    local_p = Path(local)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    snap_root = local_p / "snapshots"
    if not snap_root.is_dir():
        # flat layout fallback
        print(f"warn: no snapshots/ under {local_p}", file=sys.stderr)
        print(list(local_p.iterdir())[:20])
        return 1

    names = args.name or [d.name for d in snap_root.iterdir() if d.is_dir()]
    for name in names:
        src = snap_root / name
        if not src.is_dir():
            print(f"skip missing snapshot: {name}", file=sys.stderr)
            continue
        # copy db
        for db in src.glob("*.db"):
            dest = out / db.name
            shutil.copy2(db, dest)
            print(f"  db -> {dest}", flush=True)
        # txt
        txt_src = src / "txt"
        if txt_src.is_dir():
            txt_dest = out / f"{name}_txt"
            if txt_dest.exists():
                shutil.rmtree(txt_dest)
            shutil.copytree(txt_src, txt_dest)
            print(f"  txt -> {txt_dest} ({len(list(txt_dest.glob('*.txt')))} files)", flush=True)
        summary = src / "summary.md"
        if summary.is_file():
            shutil.copy2(summary, out / f"{name}_full_summary.md")
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
