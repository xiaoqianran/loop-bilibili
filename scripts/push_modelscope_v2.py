#!/usr/bin/env python3
"""
Push loop-bilibili v2 data/v2 snapshots to a ModelScope dataset.

Auth (never commit tokens):
  export MODELSCOPE_API_TOKEN='ms-...'

Usage:
  # after a scrape finishes
  python scripts/push_modelscope_v2.py
  python scripts/push_modelscope_v2.py --name xiaolaoshi --db data/v2/xiaolaoshi.db
  python scripts/push_modelscope_v2.py --create-only

Env overrides:
  MODELSCOPE_NAMESPACE   default: from whoami
  MODELSCOPE_DATASET     default: loop-bilibili-v2
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


def _token() -> str:
    tok = (
        os.environ.get("MODELSCOPE_API_TOKEN")
        or os.environ.get("MODELSCOPE_SDK_TOKEN")
        or os.environ.get("MODELSCOPE_TOKEN")
        or ""
    ).strip()
    if not tok:
        print(
            "error: set MODELSCOPE_API_TOKEN (https://modelscope.cn/my/myaccesstoken)",
            file=sys.stderr,
        )
        sys.exit(2)
    return tok


def _api(token: str):
    try:
        from modelscope.hub.api import HubApi
        from modelscope.hub.constants import DatasetVisibility
    except ImportError as e:
        print(
            "error: modelscope not installed. "
            "pip install 'modelscope>=1.20'  (or use project venv)",
            file=sys.stderr,
        )
        raise SystemExit(2) from e
    api = HubApi()
    api.login(token)
    return api, DatasetVisibility


def ensure_dataset(
    api,
    DatasetVisibility,
    *,
    namespace: str,
    name: str,
    token: str,
) -> str:
    url = f"https://modelscope.cn/datasets/{namespace}/{name}"
    try:
        api.create_dataset(
            dataset_name=name,
            namespace=namespace,
            chinese_name="loop-bilibili v2 字幕摄取快照",
            license="other",
            visibility=DatasetVisibility.PRIVATE,
            description=(
                "loop-bilibili v2 runtime snapshots (SQLite + optional txt). "
                "Local data/v2 is source of truth; hub is cold backup. "
                "Research/learning only; not official Bilibili data."
            ),
            token=token,
        )
        print(f"created dataset: {url}")
    except Exception as exc:
        # already exists or race — continue to upload
        msg = str(exc).lower()
        if "exist" in msg or "已存在" in msg or "409" in msg or "duplicate" in msg:
            print(f"dataset exists: {url}")
        else:
            # create may still succeed with different error if already there
            print(f"create_dataset note: {exc}")
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
        "snapshot": name,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "db": db_path.name,
        "files": files,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (staging / "README.md").write_text(
        f"""# loop-bilibili-v2

Snapshot: **{name}**  
Exported: {manifest['exported_at']}

Local source of truth: `data/v2/*.db`  
This hub copy is a cold backup after scrape.

## Layout

```text
snapshots/{name}/
  {db_path.name}
  summary.md   # optional
  txt/*.txt    # optional
manifest.json
```
""",
        encoding="utf-8",
    )
    return staging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Push v2 data to ModelScope dataset")
    p.add_argument(
        "--name",
        default="xiaolaoshi",
        help="snapshot name under snapshots/ (default: xiaolaoshi)",
    )
    p.add_argument(
        "--db",
        default=None,
        help="SQLite path (default: data/v2/<name>.db or data/v2/loop.db)",
    )
    p.add_argument(
        "--txt-dir",
        default=None,
        help="optional txt export dir (default: data/v2/<name>_txt if exists)",
    )
    p.add_argument(
        "--summary",
        default=None,
        help="optional summary.md path",
    )
    p.add_argument(
        "--namespace",
        default=os.environ.get("MODELSCOPE_NAMESPACE") or "",
        help="ModelScope namespace (default: whoami username)",
    )
    p.add_argument(
        "--dataset",
        default=os.environ.get("MODELSCOPE_DATASET") or "loop-bilibili-v2",
        help="dataset repo name (default: loop-bilibili-v2)",
    )
    p.add_argument(
        "--create-only",
        action="store_true",
        help="only ensure dataset exists, do not upload",
    )
    p.add_argument(
        "--staging",
        default=str(ROOT / "data/v2/_ms_staging"),
        help="local staging directory",
    )
    args = p.parse_args(argv)

    token = _token()
    api, DatasetVisibility = _api(token)
    who = api.whoami()
    namespace = args.namespace.strip() or (who.username if who and who.username else "")
    if not namespace:
        print("error: cannot resolve namespace; pass --namespace", file=sys.stderr)
        return 2

    url = ensure_dataset(
        api,
        DatasetVisibility,
        namespace=namespace,
        name=args.dataset,
        token=token,
    )
    print(f"dataset: {url}")
    if args.create_only:
        return 0

    name = args.name
    db_path = Path(args.db) if args.db else ROOT / "data/v2" / f"{name}.db"
    if not db_path.is_file():
        alt = ROOT / "data/v2" / "loop.db"
        if alt.is_file():
            db_path = alt
    txt_dir = Path(args.txt_dir) if args.txt_dir else ROOT / "data/v2" / f"{name}_txt"
    if not txt_dir.is_dir():
        txt_dir = None
    summary = Path(args.summary) if args.summary else ROOT / "data/v2" / f"{name}_full_summary.md"
    if not summary.is_file():
        summary = None

    staging = build_staging(
        name=name,
        db_path=db_path,
        txt_dir=txt_dir,
        summary=summary,
        staging=Path(args.staging),
    )
    print(f"staging: {staging}")

    repo_id = f"{namespace}/{args.dataset}"
    msg = f"v2 snapshot {name} @ {time.strftime('%Y-%m-%d %H:%M')}"
    print(f"uploading to {repo_id} ...")
    # Prefer modelscope_hub path via HubApi wrapper
    try:
        from modelscope_hub.constants import RepoType

        repo_type = RepoType.DATASET
    except Exception:
        repo_type = "dataset"

    result = api.upload_folder(
        repo_id=repo_id,
        repo_type=repo_type,
        folder_path=str(staging),
        path_in_repo="",
        commit_message=msg,
        ignore_patterns=["**/.ms_upload_cache/**", "**/__pycache__/**"],
    )
    print("upload done:", result)
    print(f"open: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
