#!/usr/bin/env python3
"""loop-bilibili 根入口（packages/loop_core + modules/*）。

用法:
  python3 main.py modules
  python3 main.py status
  python3 main.py catalog 2071007724 --name 海安雨
  python3 main.py hot --limit 5
  python3 main.py ranking --limit 5
  python3 main.py feed --limit 5
  python3 main.py search "AI" --limit 5
  python3 main.py summary --bvid BV1xxx
  python3 main.py subtitle --catalog catalogs/UID-name --limit 3 --resume
  python3 main.py comments --bvid BV1xxx --comment-limit 10
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

from loop_core.batch import load_bvids_from_args  # noqa: E402
from loop_core.rate_limit import (  # noqa: E402
    ITEM_DEFAULT_PROFILE,
    LIST_DEFAULT_PROFILE,
    PROFILES,
    Profile,
    get_profile,
    item_is_stricter_than_list,
)
from loop_core.workspace import ensure_sys_path  # noqa: E402

ensure_sys_path(REPO_ROOT)

from catalog.collector import CatalogCollector  # noqa: E402
from catalog.export import export_catalog, rebuild_from_folder  # noqa: E402
from catalog.models import parse_target  # noqa: E402
from comments.export import export_comments  # noqa: E402
from discover.export import export_hot, export_ranking, export_search  # noqa: E402
from feed.export import export_feed  # noqa: E402
from subtitle.export import export_subtitles  # noqa: E402
from summary.export import export_summaries  # noqa: E402

logger = logging.getLogger("loop-bilibili")

MODULE_CATALOG = {
    "catalog": {
        "status": "implemented",
        "opencli": "bilibili user-videos",
        "desc": "UP 投稿全量目录 → 按系列导出 Markdown/JSON/CSV",
        "path": "modules/catalog/",
        "rate": "list",
    },
    "hot": {
        "status": "implemented",
        "opencli": "bilibili hot",
        "desc": "B 站热门视频快照",
        "path": "modules/discover/",
        "rate": "list",
    },
    "ranking": {
        "status": "implemented",
        "opencli": "bilibili ranking",
        "desc": "排行榜快照",
        "path": "modules/discover/",
        "rate": "list",
    },
    "search": {
        "status": "implemented",
        "opencli": "bilibili search",
        "desc": "关键词搜索导出",
        "path": "modules/discover/",
        "rate": "list",
    },
    "feed": {
        "status": "implemented",
        "opencli": "bilibili feed",
        "desc": "关注时间线 / 用户动态",
        "path": "modules/feed/",
        "rate": "list",
    },
    "summary": {
        "status": "implemented",
        "opencli": "bilibili summary",
        "desc": "批量官方 AI 总结（更严 item 限速 + resume）",
        "path": "modules/summary/",
        "rate": "item",
    },
    "subtitle": {
        "status": "implemented",
        "opencli": "bilibili subtitle",
        "desc": "批量字幕（更严 item 限速 + resume）",
        "path": "modules/subtitle/",
        "rate": "item",
    },
    "comments": {
        "status": "implemented",
        "opencli": "bilibili comments",
        "desc": "批量视频评论（更严 item 限速 + resume）",
        "path": "modules/comments/",
        "rate": "item",
    },
}


def build_profile(args: argparse.Namespace, *, default: str | None = None) -> Profile:
    name = getattr(args, "profile", None) or default or LIST_DEFAULT_PROFILE
    p = get_profile(name)
    if getattr(args, "page_size", None) is not None:
        p.page_size = args.page_size
    if getattr(args, "delay", None) is not None:
        p.page_delay = args.delay
    if getattr(args, "jitter", None) is not None:
        p.page_jitter = args.jitter
    if getattr(args, "item_delay", None) is not None:
        p.item_delay = args.item_delay
    if getattr(args, "item_jitter", None) is not None:
        p.item_jitter = args.item_jitter
    if getattr(args, "retries", None) is not None:
        p.max_retries = args.retries
    if getattr(args, "max_pages", None) is not None:
        p.max_pages = args.max_pages
    if getattr(args, "cooldown_412", None) is not None:
        p.cooldown_412 = args.cooldown_412
    return p


def add_list_rate_args(p: argparse.ArgumentParser, *, default_profile: str = LIST_DEFAULT_PROFILE) -> None:
    p.add_argument(
        "--profile",
        choices=list(PROFILES.keys()),
        default=default_profile,
        help="限速 profile（默认 conservative 防风控）",
    )
    p.add_argument("--page-size", type=int, default=None)
    p.add_argument("--delay", type=float, default=None, help="列表/页间隔秒")
    p.add_argument("--jitter", type=float, default=None)
    p.add_argument("--retries", type=int, default=None)
    p.add_argument("--max-pages", type=int, default=None)
    p.add_argument("--cooldown-412", type=float, default=None)


def add_item_rate_args(p: argparse.ArgumentParser) -> None:
    add_list_rate_args(p, default_profile=ITEM_DEFAULT_PROFILE)
    p.add_argument("--item-delay", type=float, default=None, help="每视频间隔秒（覆盖 profile）")
    p.add_argument("--item-jitter", type=float, default=None)


def add_bvid_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bvid", default="", help="单个 BV 号或链接")
    p.add_argument("--catalog", default="", help="catalog 目录或 all.json 路径")
    p.add_argument("--limit", type=int, default=None, help="最多处理多少个 bvid")
    p.add_argument("--resume", action="store_true", default=True, help="跳过已完成（默认开）")
    p.add_argument("--no-resume", action="store_true", help="忽略 done，全量重跑")
    p.add_argument("--out", default="data", help="输出根目录")


def resolve_bvids(args: argparse.Namespace) -> list[str]:
    bvids = load_bvids_from_args(
        bvid=args.bvid or None,
        catalog=args.catalog or None,
        limit=args.limit,
    )
    if not bvids:
        raise SystemExit("需要 --bvid 或 --catalog（指向含 all.json 的目录）")
    return bvids


def cmd_modules(_args: argparse.Namespace) -> int:
    print("loop-bilibili modules（对齐 opencli bilibili）\n")
    for name, meta in MODULE_CATALOG.items():
        print(f"  {name}")
        print(f"    status : {meta['status']}")
        print(f"    opencli: {meta['opencli']}")
        print(f"    rate   : {meta['rate']} (default profile {LIST_DEFAULT_PROFILE if meta['rate']=='list' else ITEM_DEFAULT_PROFILE})")
        print(f"    desc   : {meta['desc']}")
        print(f"    path   : {meta['path']}")
        print()
    p = get_profile(ITEM_DEFAULT_PROFILE)
    print(
        f"anti-risk: item_delay({p.item_delay}) > page_delay({p.page_delay}) "
        f"→ {item_is_stricter_than_list(p)}"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print("=== loop-bilibili status ===")
    print(f"repo: {REPO_ROOT}")
    print(f"core: {REPO_ROOT / 'packages' / 'loop_core'}")
    print("\n[modules]")
    for name, meta in MODULE_CATALOG.items():
        print(f"  {name:10} [{meta['status']}] {meta['opencli']}")
    catalogs = Path(getattr(args, "catalogs", None) or "catalogs")
    if catalogs.is_dir():
        print(f"\n[catalogs] {catalogs}")
        for d in sorted(p for p in catalogs.iterdir() if p.is_dir()):
            meta_path = d / "meta.json"
            if meta_path.is_file():
                try:
                    m = json.loads(meta_path.read_text(encoding="utf-8"))
                    print(f"  {d.name}: total={m.get('total')} series={m.get('series_count')}")
                    continue
                except Exception:
                    pass
            print(f"  {d.name}")
    data = Path(getattr(args, "out", None) or "data")
    if data.is_dir():
        print(f"\n[data] {data}")
        for p in sorted(data.rglob("meta.json"))[:20]:
            print(f"  {p.relative_to(data)}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    if args.rebuild:
        rebuild_from_folder(Path(args.rebuild), name_override=args.name or "")
        return 0
    if not args.target:
        print("error: catalog 需要 target 或 --rebuild", file=sys.stderr)
        return 2
    if args.profile == "aggressive":
        print("WARNING: aggressive 易触发风控，建议 conservative", flush=True)
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


def cmd_hot(args: argparse.Namespace) -> int:
    profile = build_profile(args)
    export_hot(Path(args.out), profile, limit=args.limit)
    return 0


def cmd_ranking(args: argparse.Namespace) -> int:
    profile = build_profile(args)
    export_ranking(Path(args.out), profile, limit=args.limit)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    profile = build_profile(args)
    export_search(args.query, Path(args.out), profile, limit=args.limit)
    return 0


def cmd_feed(args: argparse.Namespace) -> int:
    profile = build_profile(args)
    export_feed(
        Path(args.out),
        profile,
        uid=args.uid or "",
        limit=args.limit,
        pages=args.pages,
        feed_type=args.type,
    )
    return 0


def _item_out(args: argparse.Namespace, kind: str) -> Path:
    base = Path(args.out)
    if args.catalog:
        # nest under catalog slug when batching from catalog
        slug = Path(args.catalog).name
        return base / kind / slug
    if args.bvid:
        from loop_core.batch import extract_bvid

        return base / kind / extract_bvid(args.bvid)
    return base / kind


def cmd_summary(args: argparse.Namespace) -> int:
    profile = build_profile(args, default=ITEM_DEFAULT_PROFILE)
    bvids = resolve_bvids(args)
    resume = not args.no_resume
    export_summaries(bvids, _item_out(args, "summary"), profile, resume=resume)
    return 0


def cmd_subtitle(args: argparse.Namespace) -> int:
    profile = build_profile(args, default=ITEM_DEFAULT_PROFILE)
    bvids = resolve_bvids(args)
    resume = not args.no_resume
    export_subtitles(bvids, _item_out(args, "subtitle"), profile, resume=resume)
    return 0


def cmd_comments(args: argparse.Namespace) -> int:
    profile = build_profile(args, default=ITEM_DEFAULT_PROFILE)
    bvids = resolve_bvids(args)
    resume = not args.no_resume
    export_comments(
        bvids,
        _item_out(args, "comments"),
        profile,
        comment_limit=args.comment_limit,
        resume=resume,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="loop-bilibili 根入口（防风控默认 conservative）",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("modules", help="列出模块")
    st = sub.add_parser("status", help="状态")
    st.add_argument("--out", default="data")
    st.add_argument("--catalogs", default="catalogs")

    cat = sub.add_parser("catalog", help="UP 投稿目录")
    cat.add_argument("target", nargs="?", default="")
    cat.add_argument("--name", default="")
    cat.add_argument("--out", default="catalogs")
    cat.add_argument("--resume", action="store_true")
    cat.add_argument("--rebuild", metavar="DIR", default="")
    cat.add_argument("--order", choices=["pubdate", "click", "stow"], default="pubdate")
    add_list_rate_args(cat)

    hot = sub.add_parser("hot", help="热门视频")
    hot.add_argument("--limit", type=int, default=5)
    hot.add_argument("--out", default="data")
    add_list_rate_args(hot)

    ranking = sub.add_parser("ranking", help="排行榜")
    ranking.add_argument("--limit", type=int, default=5)
    ranking.add_argument("--out", default="data")
    add_list_rate_args(ranking)

    search = sub.add_parser("search", help="搜索")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--out", default="data")
    add_list_rate_args(search)

    feed = sub.add_parser("feed", help="动态时间线")
    feed.add_argument("uid", nargs="?", default="", help="用户 UID，省略=关注流")
    feed.add_argument("--limit", type=int, default=5)
    feed.add_argument("--pages", type=int, default=1)
    feed.add_argument("--type", default="all")
    feed.add_argument("--out", default="data")
    add_list_rate_args(feed)

    for name, help_ in (
        ("summary", "批量 AI 总结"),
        ("subtitle", "批量字幕"),
        ("comments", "批量评论"),
    ):
        sp = sub.add_parser(name, help=help_)
        add_bvid_source_args(sp)
        add_item_rate_args(sp)
        if name == "comments":
            sp.add_argument("--comment-limit", type=int, default=10)

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
        return 0
    handlers = {
        "modules": cmd_modules,
        "status": cmd_status,
        "catalog": cmd_catalog,
        "hot": cmd_hot,
        "ranking": cmd_ranking,
        "search": cmd_search,
        "feed": cmd_feed,
        "summary": cmd_summary,
        "subtitle": cmd_subtitle,
        "comments": cmd_comments,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
