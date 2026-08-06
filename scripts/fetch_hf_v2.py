#!/usr/bin/env python3
"""
Download v2 snapshots from Hugging Face dataset into data/v2/.

Usage:
  python scripts/fetch_hf_v2.py --all
  python scripts/fetch_hf_v2.py --name haianyu
  python scripts/fetch_hf_v2.py --name haianyu --name xiaolaoshi

Auth: HF_TOKEN / HUGGINGFACE_HUB_TOKEN or gitignored .env

Env:
  HF_DATASET_REPO   default: seachen/loop-bilibili-v2
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "seachen/loop-bilibili-v2"


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
    p = argparse.ArgumentParser(description="Fetch v2 snapshots from Hugging Face")
    p.add_argument(
        "--repo",
        default=os.environ.get("HF_DATASET_REPO") or DEFAULT_REPO,
        help="dataset repo id owner/name",
    )
    p.add_argument("--name", action="append", default=[], help="snapshot name(s)")
    p.add_argument("--all", action="store_true", help="download full dataset snapshots")
    p.add_argument(
        "--out",
        default=str(ROOT / "data" / "v2"),
        help="output data/v2 directory",
    )
    p.add_argument(
        "--cache",
        default=str(ROOT / "data" / "v2" / "_hf_download"),
        help="download cache dir",
    )
    p.add_argument(
        "--dbs-only",
        action="store_true",
        default=True,
        help="only pull *.db (+ manifest) — default for fast CI (default: true)",
    )
    p.add_argument(
        "--with-txt",
        action="store_true",
        help="also pull txt/ and summary (slower)",
    )
    args = p.parse_args(argv)

    _load_dotenv()
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    ).strip()
    if not token:
        print("error: HF_TOKEN required", file=sys.stderr)
        return 2

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("error: pip install 'huggingface_hub>=0.23'", file=sys.stderr)
        return 2

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    allow: list[str] | None
    if args.name and not args.all:
        allow = ["manifest.json", "README.md"]
        for n in args.name:
            if args.with_txt:
                allow.extend([f"snapshots/{n}/**"])
            else:
                allow.extend(
                    [
                        f"snapshots/{n}/*.db",
                        f"snapshots/{n}/**/*.db",
                    ]
                )
    else:
        if args.with_txt:
            allow = None  # full tree
        else:
            allow = [
                "manifest.json",
                "README.md",
                "snapshots/**/*.db",
                "**/*.db",
            ]

    print(f"downloading hf://datasets/{args.repo} ...", flush=True)
    if allow:
        print(f"  allow_patterns: {allow}", flush=True)
    local = snapshot_download(
        repo_id=args.repo,
        repo_type="dataset",
        local_dir=str(cache),
        token=token,
        allow_patterns=allow,
        max_workers=8,
    )
    local_p = Path(local)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    snap_root = local_p / "snapshots"
    if not snap_root.is_dir():
        print(f"warn: no snapshots/ under {local_p}", file=sys.stderr)
        print(list(local_p.iterdir())[:20], file=sys.stderr)
        return 1

    names = args.name or [d.name for d in snap_root.iterdir() if d.is_dir()]
    copied = 0
    for name in names:
        src = snap_root / name
        if not src.is_dir():
            print(f"skip missing snapshot: {name}", file=sys.stderr)
            continue
        for db in src.glob("*.db"):
            dest = out / db.name
            shutil.copy2(db, dest)
            print(f"  db -> {dest} ({db.stat().st_size} bytes)", flush=True)
            copied += 1
        if args.with_txt:
            txt_src = src / "txt"
            if txt_src.is_dir():
                txt_dest = out / f"{name}_txt"
                if txt_dest.exists():
                    shutil.rmtree(txt_dest)
                shutil.copytree(txt_src, txt_dest)
                n = len(list(txt_dest.glob("*.txt")))
                print(f"  txt -> {txt_dest} ({n} files)", flush=True)
            summary = src / "summary.md"
            if summary.is_file():
                shutil.copy2(summary, out / f"{name}_full_summary.md")
    if copied == 0:
        print("error: no .db files copied", file=sys.stderr)
        return 1
    print(f"done ({copied} db files)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
