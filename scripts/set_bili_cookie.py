#!/usr/bin/env python3
"""
Save BILI_COOKIE into gitignored .env for personalized homepage rcmd.

Usage:
  # paste cookie string (from browser DevTools → Application → Cookies → bilibili.com)
  python scripts/set_bili_cookie.py 'SESSDATA=..; bili_jct=..; DedeUserID=..'

  # read from stdin
  python scripts/set_bili_cookie.py --stdin

  # verify only (no write)
  python scripts/set_bili_cookie.py --check

  # optional: smoke-test homepage with cookie
  python scripts/set_bili_cookie.py --test

How to copy cookie from browser:
  1. Open https://www.bilibili.com while logged in
  2. F12 → Application/应用 → Cookies → https://www.bilibili.com
  3. Copy at least: SESSDATA, bili_jct, DedeUserID (buvid3 optional)
  4. Join as: SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _normalize(cookie: str) -> str:
    c = (cookie or "").strip().strip("'").strip('"')
    # allow multi-line paste
    c = re.sub(r"\s*\n\s*", " ", c)
    c = re.sub(r"\s*;\s*", "; ", c).strip().rstrip(";")
    return c


def _summary(cookie: str) -> dict:
    return {
        "length": len(cookie),
        "SESSDATA": "SESSDATA=" in cookie,
        "bili_jct": "bili_jct=" in cookie,
        "DedeUserID": "DedeUserID=" in cookie,
        "buvid3": "buvid3=" in cookie.lower(),
    }


def _upsert_env(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for ln in lines:
        if ln.strip().startswith(f"{key}=") or ln.strip().startswith(f"export {key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"# Bilibili login cookie for personalized homepage (do not commit)")
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _test_homepage(cookie: str) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from loop_bilibili.sources.homepage_rcmd import HomepageRcmdSource

    print("testing homepage rcmd with cookie...", flush=True)
    src = HomepageRcmdSource(pages=1, ps=6, cookie=cookie)
    try:
        cands = list(src.fetch())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: got {len(cands)} candidates")
    for c in cands[:5]:
        v = c.video
        print(f"  · {v.bvid} {v.owner_name}: {(v.title or '')[:50]}")
    return 0 if cands else 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Set BILI_COOKIE in .env for personalized feed")
    p.add_argument("cookie", nargs="?", default="", help="Cookie header string")
    p.add_argument("--stdin", action="store_true", help="Read cookie from stdin")
    p.add_argument("--check", action="store_true", help="Only report current cookie status")
    p.add_argument("--test", action="store_true", help="Smoke-test homepage after set/check")
    p.add_argument("--env", default=str(ENV_PATH), help="Path to .env")
    args = p.parse_args(argv)

    env_path = Path(args.env)

    if args.check and not args.cookie and not args.stdin:
        # load existing
        if env_path.is_file():
            for ln in env_path.read_text(encoding="utf-8").splitlines():
                if ln.strip().startswith("BILI_COOKIE="):
                    os.environ["BILI_COOKIE"] = ln.split("=", 1)[1].strip().strip("'\"")
        cookie = (os.environ.get("BILI_COOKIE") or "").strip()
        s = _summary(cookie)
        print(f".env: {env_path} exists={env_path.is_file()}")
        print(f"cookie: {s}")
        if not s["SESSDATA"]:
            print("missing SESSDATA — homepage will stay guest/non-personalized", file=sys.stderr)
            return 1
        if args.test:
            return _test_homepage(cookie)
        return 0

    raw = args.cookie
    if args.stdin or not raw:
        if not sys.stdin.isatty() or args.stdin:
            raw = sys.stdin.read()
        elif not raw:
            p.print_help()
            print("\nerror: pass cookie string or --stdin", file=sys.stderr)
            return 2

    cookie = _normalize(raw)
    s = _summary(cookie)
    print(f"parsed: {s}")
    if not s["SESSDATA"]:
        print("error: cookie must include SESSDATA=...", file=sys.stderr)
        return 2
    if not s["bili_jct"]:
        print("warn: bili_jct missing — some APIs may fail", file=sys.stderr)

    _upsert_env(env_path, "BILI_COOKIE", cookie)
    print(f"wrote {env_path} (mode 600, gitignored)")
    print("homepage + subtitle requests will send this Cookie on next run.")

    if args.test:
        return _test_homepage(cookie)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
