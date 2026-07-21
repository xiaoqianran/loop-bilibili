#!/usr/bin/env python3
"""loop-bilibili 根入口。

用法（仓库根目录）:
  python3 main.py modules
  python3 main.py catalog 2071007724 --name 海安雨
  python3 main.py catalog --rebuild catalogs/2071007724-海安雨
  python3 main.py catalog 2071007724 --name 海安雨 --resume
  python3 main.py status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LOOP_BILIBILI_ROOT", str(REPO_ROOT))

_PACKAGES = REPO_ROOT / "packages"
if _PACKAGES.is_dir() and str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))

from loop_core.rate_limit import PROFILES, Profile, get_profile  # noqa: E402
from loop_core.workspace import ensure_sys_path  # noqa: E402

ensure_sys_path(REPO_ROOT)

from catalog.export import export_catalog, rebuild_from_folder  # noqa: E402
from catalog.collector import CatalogCollector  # noqa: E402
from catalog.models import parse_target  # noqa: E402

logger = logging.getLogger("loop-bilibili")

MODULE_CATALOG = {
    "catalog": {
        "status": "implemented",
        "opencli": "bilibili user-videos",
        "desc": "UP 投稿全量目录 → 按系列导出 Markdown/JSON/CSV",
        "path": "modules/catalog/",
    },
    "summary": {
        "status": "planned",
        "opencli": "bilibili summary",
        "desc": "批量官方 AI 总结（更严限速）",
        "path": "modules/summary/",
    },
    "subtitle": {
        "status": "planned",
        "opencli": "bilibili subtitle",
        "desc": "批量字幕导出（更严限速）",
        "path": "modules/subtitle/",
    },
}


def build_profile(args: argparse.Namespace) -> Profile:
    p = get_profile(args.profile)
    if getattr(args, "page_size", None) is not None:
        p.page_size = args.page_size
    if getattr(args, "delay", None) is not None:
        p.page_delay = args.delay
    if getattr(args, "jitter", None) is not None:
        p.page_jitter = args.jitter
    if getattr(args, "retries", None) is not None:
        p.max_retries = args.retries
    if getattr(args, "max_pages", None) is not None:
        p.max_pages = args.max_pages
    if getattr(args, "cooldown_412", None) is not None:
        p.cooldown_412 = args.cooldown_412
    return p


def add_rate_limit_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--profile",
        choices=list(PROFILES.keys()),
        default="conservative",
        help="限速 profile",
    )
    p.add_argument("--page-size", type=int, default=None)
    p.add_argument("--delay", type=float, default=None, help="页间隔秒")
    p.add_argument("--jitter", type=float, default=None)
    p.add_argument("--retries", type=int, default=None)
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--cooldown-412", type=float, default=None)
    p.add_argument(
        "--order",
        choices=["pubdate", "click", "stow"],
        default="pubdate",
    )


def cmd_modules(_args: argparse.Namespace) -> int:
    print("loop-bilibili modules（对齐 opencli bilibili）\n")
    for name, meta in MODULE_CATALOG.items():
        print(f"  {name}")
        print(f"    status : {meta['status']}")
        print(f"    opencli: {meta['opencli']}")
        print(f"    desc   : {meta['desc']}")
        print(f"    path   : {meta['path']}")
        print()
    print("其它 opencli bilibili 可扩展: hot, search, video, comments, download, ...")
    print("  opencli bilibili --help")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    out = Path(args.out) if getattr(args, "out", None) else REPO_ROOT / "catalogs"
    print("=== loop-bilibili status ===")
    print(f"repo:     {REPO_ROOT}")
    print(f"core:     {REPO_ROOT / 'packages' / 'loop_core'}")
    print(f"catalogs: {out}")
    print("\n[modules]")
    for name, meta in MODULE_CATALOG.items():
        print(f"  {name:10} [{meta['status']}] {meta['opencli']}")

    if out.is_dir():
        print("\n[catalogs]")
        dirs = sorted([p for p in out.iterdir() if p.is_dir()])
        if not dirs:
            print("  (empty)")
        for d in dirs:
            meta_path = d / "meta.json"
            if meta_path.is_file():
                try:
                    m = json.loads(meta_path.read_text(encoding="utf-8"))
                    print(
                        f"  {d.name}: total={m.get('total')} "
                        f"series={m.get('series_count')} "
                        f"profile={m.get('profile')}"
                    )
                    continue
                except Exception:
                    pass
            progress = d / ".progress.json"
            if progress.is_file():
                try:
                    pr = json.loads(progress.read_text(encoding="utf-8"))
                    print(
                        f"  {d.name}: IN PROGRESS page={pr.get('next_page')} "
                        f"fetched={pr.get('fetched_count')}"
                    )
                    continue
                except Exception:
                    pass
            print(f"  {d.name}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    if args.rebuild:
        rebuild_from_folder(Path(args.rebuild), name_override=args.name or "")
        return 0

    if not args.target:
        print("error: catalog 需要 target，或使用 --rebuild DIR", file=sys.stderr)
        return 2

    if args.profile == "aggressive":
        print(
            "WARNING: profile=aggressive 间隔很短，易触发 -799/-412。"
            "大批量请用 conservative。",
            flush=True,
        )

    profile = build_profile(args)
    info = parse_target(args.target)
    collector = CatalogCollector(profile, order=args.order)

    if "uid" in info:
        uid = info["uid"]
        up_name = args.name or uid
    else:
        uid, resolved = collector.resolve_uid(args.target)
        up_name = args.name or resolved
    if args.name:
        up_name = args.name

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    export_catalog(
        uid,
        up_name,
        out_root,
        profile,
        order=args.order,
        resume=args.resume,
        max_pages=args.max_pages,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="loop-bilibili 根入口（packages/loop_core + modules/*）",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("modules", help="列出模块与 opencli 对应关系")

    st = sub.add_parser("status", help="仓库与 catalogs 状态")
    st.add_argument("--out", default="catalogs", help="catalogs 根目录")

    cat = sub.add_parser("catalog", help="导出 UP 投稿目录（按系列）")
    cat.add_argument("target", nargs="?", default="", help="UID / 空间链接 / 名称")
    cat.add_argument("--name", default="", help="显示名")
    cat.add_argument("--out", default="catalogs", help="输出根目录")
    cat.add_argument("--resume", action="store_true", help="断点续跑")
    cat.add_argument(
        "--rebuild",
        metavar="DIR",
        default="",
        help="从已有 all.json 离线重建",
    )
    add_rate_limit_args(cat)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        print("\n提示: python3 main.py catalog --help")
        return 0

    handlers = {
        "modules": cmd_modules,
        "status": cmd_status,
        "catalog": cmd_catalog,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
