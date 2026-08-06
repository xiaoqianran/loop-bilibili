#!/usr/bin/env python3
"""
Push loop-bilibili v2 data/v2 snapshots to a Hugging Face dataset.

Auth (never commit tokens):
  export HF_TOKEN='hf_...'
  # or put HF_TOKEN in loop-bilibili/.env (gitignored)

Usage:
  python scripts/push_hf_v2.py --name haianyu
  python scripts/push_hf_v2.py --name xiaolaoshi --db data/v2/xiaolaoshi.db
  python scripts/push_hf_v2.py --create-only
  python scripts/push_hf_v2.py --all   # push every data/v2/*.db (skip _* / test)

Env:
  HF_TOKEN / HUGGINGFACE_HUB_TOKEN
  HF_DATASET_REPO   default: <whoami>/loop-bilibili-v2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
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


def _token() -> str:
    _load_dotenv()
    tok = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    ).strip()
    if not tok:
        print(
            "error: set HF_TOKEN (https://huggingface.co/settings/tokens)\n"
            "  or put it in loop-bilibili/.env (gitignored)",
            file=sys.stderr,
        )
        sys.exit(2)
    return tok


def _api(token: str):
    try:
        from huggingface_hub import HfApi, whoami
    except ImportError as e:
        print(
            "error: huggingface_hub not installed. pip install 'huggingface_hub>=0.23'",
            file=sys.stderr,
        )
        raise SystemExit(2) from e
    return HfApi(token=token), whoami


def ensure_dataset(api, repo_id: str, *, private: bool = True) -> str:
    url = f"https://huggingface.co/datasets/{repo_id}"
    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="dataset",
            private=private,
            exist_ok=True,
        )
        print(f"dataset ready: {url} (private={private})")
    except Exception as exc:
        print(f"create_repo note: {exc}")
    # README (best-effort; ignore if already customized)
    readme = (
        "---\n"
        "license: other\n"
        "pretty_name: loop-bilibili-v2\n"
        "tags:\n"
        "  - bilibili\n"
        "  - subtitles\n"
        "  - research\n"
        "---\n\n"
        "# loop-bilibili-v2\n\n"
        "Cold backup of local `data/v2/*.db` snapshots for GitHub Pages builds.\n\n"
        "Local SQLite is source of truth; this hub copy is for CI sync only.\n\n"
        "## Layout\n\n"
        "```text\n"
        "snapshots/<slug>/\n"
        "  <slug>.db\n"
        "  summary.md   # optional\n"
        "  txt/*.txt    # optional\n"
        "manifest.json\n"
        "```\n"
    )
    try:
        api.upload_file(
            path_or_fileobj=readme.encode("utf-8"),
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="dataset",
            commit_message="docs: dataset README",
        )
    except Exception as exc:
        print(f"readme note: {exc}")
    return url


def build_staging(
    *,
    name: str,
    db_path: Path,
    txt_dir: Path | None,
    summary: Path | None,
    staging: Path,
) -> Path:
    if staging.exists():
        shutil.rmtree(staging)
    snap = staging / "snapshots" / name
    snap.mkdir(parents=True)
    if not db_path.is_file():
        print(f"error: db not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    shutil.copy2(db_path, snap / db_path.name)
    if summary and summary.is_file():
        shutil.copy2(summary, snap / "summary.md")
    if txt_dir and txt_dir.is_dir():
        dest = snap / "txt"
        dest.mkdir()
        for p in sorted(txt_dir.glob("*.txt")):
            shutil.copy2(p, dest / p.name)
    files = []
    for p in sorted(staging.rglob("*")):
        if p.is_file():
            files.append(
                {
                    "path": str(p.relative_to(staging)),
                    "bytes": p.stat().st_size,
                    "sha256_16": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                }
            )
    manifest = {
        "project": "loop-bilibili",
        "version": "v2",
        "hub": "huggingface",
        "snapshot": name,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db": db_path.name,
        "files": files,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return staging


def resolve_db(name: str, db: str | None) -> Path:
    if db:
        return Path(db)
    p = ROOT / "data" / "v2" / f"{name}.db"
    if p.is_file():
        return p
    alt = ROOT / "data" / "v2" / "loop.db"
    return alt if alt.is_file() else p


def push_one(
    api,
    *,
    repo_id: str,
    name: str,
    db_path: Path,
    txt_dir: Path | None,
    summary: Path | None,
    staging_root: Path,
) -> None:
    staging = build_staging(
        name=name,
        db_path=db_path,
        txt_dir=txt_dir,
        summary=summary,
        staging=staging_root / name,
    )
    msg = f"v2 snapshot {name} @ {time.strftime('%Y-%m-%d %H:%M')}"
    print(f"uploading {name} -> {repo_id} ...", flush=True)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(staging),
        path_in_repo="",
        commit_message=msg,
        allow_patterns=["**/*"],
        # merge: only overwrite files present in this staging (HF merges by path)
    )
    print(f"  done: snapshots/{name}/ ({db_path.name})", flush=True)


def discover_names() -> list[str]:
    out: list[str] = []
    d = ROOT / "data" / "v2"
    for db in sorted(d.glob("*.db")):
        if db.name.startswith("_"):
            continue
        if db.stem in ("single_test", "loop") or db.stem.endswith("_test"):
            continue
        if "guest" in db.stem or "prefer" in db.stem:
            continue
        out.append(db.stem)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Push v2 data to Hugging Face dataset")
    p.add_argument("--name", action="append", default=[], help="snapshot name(s)")
    p.add_argument("--all", action="store_true", help="push all non-test data/v2/*.db")
    p.add_argument("--db", default=None, help="SQLite path (single --name only)")
    p.add_argument("--txt-dir", default=None)
    p.add_argument("--summary", default=None)
    p.add_argument(
        "--repo",
        default=os.environ.get("HF_DATASET_REPO") or "",
        help="dataset repo id owner/name (default: <whoami>/loop-bilibili-v2)",
    )
    p.add_argument("--public", action="store_true", help="create as public (default private)")
    p.add_argument("--create-only", action="store_true")
    p.add_argument(
        "--staging",
        default=str(ROOT / "data" / "v2" / "_hf_staging"),
        help="local staging directory",
    )
    args = p.parse_args(argv)

    token = _token()
    api, whoami = _api(token)
    me = whoami(token=token)
    user = me.get("name") or ""
    repo_id = (args.repo or "").strip() or f"{user}/loop-bilibili-v2"
    if "/" not in repo_id:
        repo_id = f"{user}/{repo_id}"

    url = ensure_dataset(api, repo_id, private=not args.public)
    print(f"dataset: {url}")
    if args.create_only:
        return 0

    names = list(args.name)
    if args.all or not names:
        if not names:
            names = discover_names()
    if not names:
        print("error: no snapshots to push (pass --name or --all)", file=sys.stderr)
        return 2

    staging_root = Path(args.staging)
    for name in names:
        db_path = resolve_db(name, args.db if len(names) == 1 else None)
        if not db_path.is_file():
            print(f"skip missing db: {db_path}", file=sys.stderr)
            continue
        txt_dir = Path(args.txt_dir) if (args.txt_dir and len(names) == 1) else ROOT / "data/v2" / f"{name}_txt"
        if not txt_dir.is_dir():
            txt_dir = None
        summary = (
            Path(args.summary)
            if (args.summary and len(names) == 1)
            else ROOT / "data/v2" / f"{name}_full_summary.md"
        )
        if not summary.is_file():
            summary = None
        push_one(
            api,
            repo_id=repo_id,
            name=name,
            db_path=db_path,
            txt_dir=txt_dir,
            summary=summary,
            staging_root=staging_root,
        )
    print(f"open: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
