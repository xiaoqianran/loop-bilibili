"""python -m bili_subbatch"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .batch import load_bvids_from_catalog, run_batch, write_json
from .client import fetch_subtitle
from .pack import pack_dataset
from .srt import write_srt
from .util import extract_bvid


def cmd_one(args: argparse.Namespace) -> int:
    bvid = extract_bvid(args.bvid) or args.bvid.strip()
    r = fetch_subtitle(bvid, cookie=args.cookie)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "items").mkdir(exist_ok=True)
    row = r.to_row()
    write_json(out / "items" / f"{r.bvid or 'unknown'}.json", row)
    if r.status == "ok" and r.data:
        (out / "srt").mkdir(exist_ok=True)
        write_srt(out / "srt" / f"{r.bvid}.srt", r.data)
    title = (r.title or "")[:40]
    print(
        f"{r.bvid} status={r.status} cues={r.cue_count} "
        f"lan={r.lan} source={r.source} title={title}"
    )
    if r.error:
        print("error:", r.error)
    print(f"out -> {out}")
    return 0 if r.status in ("ok", "empty") else 1


def cmd_batch(args: argparse.Namespace) -> int:
    if args.catalog:
        bvids = load_bvids_from_catalog(Path(args.catalog))
    elif args.bvids:
        text = Path(args.bvids).read_text(encoding="utf-8")
        bvids = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            b = extract_bvid(line) or line
            bvids.append(b)
    else:
        print("need --catalog or --bvids file", file=sys.stderr)
        return 2
    if args.limit is not None:
        if args.limit < 0:
            print("--limit must be >= 0", file=sys.stderr)
            return 2
        bvids = bvids[: args.limit]
    if args.delay < 0 or args.jitter < 0:
        print("--delay/--jitter must be >= 0", file=sys.stderr)
        return 2
    run_batch(
        bvids,
        Path(args.out),
        delay=args.delay,
        jitter=args.jitter,
        resume=not args.no_resume,
        cookie=args.cookie,
    )
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    """Turn local batch outputs into a GitHub-friendly data tree."""
    srcs = [Path(p) for p in (args.src or [])]
    if args.src_root:
        root = Path(args.src_root)
        if not root.is_dir():
            print(f"--src-root not a directory: {root}", file=sys.stderr)
            return 2
        # if both given, src list wins for explicit dirs; root still allowed alone
        if not srcs:
            from .pack import discover_batch_dirs

            srcs = discover_batch_dirs(root)
    if not srcs:
        print("need --src-root or one or more --src batch dirs", file=sys.stderr)
        return 2
    out = Path(args.out)
    catalogs = Path(args.catalogs) if args.catalogs else None
    pack_dataset(
        srcs,
        out,
        write_txt=not args.no_txt,
        catalogs=catalogs,
        skip_empty_up=args.skip_empty_up,
        clean=not args.no_clean,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="loop-bilibili-subbatch",
        description="loop-bilibili-subbatch: Chrome SubBatch protocol clone (no opencli)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("one", help="fetch one bvid")
    one.add_argument("bvid")
    one.add_argument("-o", "--out", default="./out")
    one.add_argument("--cookie", default=None, help="or env BILI_COOKIE")
    one.set_defaults(func=cmd_one)

    bat = sub.add_parser("batch", help="batch fetch with resume")
    bat.add_argument("--catalog", help="catalog all.json list")
    bat.add_argument("--bvids", help="text file one bvid/url per line")
    bat.add_argument("-o", "--out", required=True)
    bat.add_argument("--limit", type=int, default=None)
    bat.add_argument("--delay", type=float, default=0.4)
    bat.add_argument("--jitter", type=float, default=0.15)
    bat.add_argument("--cookie", default=None)
    bat.add_argument("--no-resume", action="store_true")
    bat.set_defaults(func=cmd_batch)

    pk = sub.add_parser(
        "pack",
        help="pack batch outputs into GitHub-friendly dataset (srt+index, no cue bloat)",
    )
    pk.add_argument(
        "--src-root",
        help="directory containing many UP batch folders (e.g. loop-bilibili/data/subtitle)",
    )
    pk.add_argument(
        "--src",
        action="append",
        default=[],
        help="one batch dir (repeatable); overrides discovery if set with --src-root only as filter base",
    )
    pk.add_argument(
        "-o",
        "--out",
        required=True,
        help="data repo root to write (ups/, dataset.json, README, NOTICE)",
    )
    pk.add_argument(
        "--catalogs",
        default=None,
        help="optional loop-bilibili catalogs/ for space_url / series meta",
    )
    pk.add_argument(
        "--no-txt",
        action="store_true",
        help="do not write txt/ plain text (srt only)",
    )
    pk.add_argument(
        "--skip-empty-up",
        action="store_true",
        help="skip UP folders with zero ok subtitles",
    )
    pk.add_argument(
        "--no-clean",
        action="store_true",
        help="do not wipe existing ups/ before packing",
    )
    pk.set_defaults(func=cmd_pack)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
