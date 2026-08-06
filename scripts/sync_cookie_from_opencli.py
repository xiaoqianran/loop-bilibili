#!/usr/bin/env python3
"""
Pull bilibili login Cookie via opencli Browser Bridge and save to .env.

Requires:
  - opencli installed and doctor OK (extension connected)
  - bilibili logged in (opencli bilibili whoami)

Usage:
  python scripts/sync_cookie_from_opencli.py
  python scripts/sync_cookie_from_opencli.py --test   # also smoke-test homepage rcmd
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export_cookie_via_opencli() -> dict:
    proc = subprocess.run(
        ["opencli", "bilibili", "export-cookie", "-f", "json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0 and "{" not in raw:
        print(raw, file=sys.stderr)
        raise SystemExit(f"opencli export-cookie failed rc={proc.returncode}")
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        print(raw[:800], file=sys.stderr)
        raise SystemExit("opencli returned no JSON")
    data = json.loads(m.group(0))
    if not data.get("has_SESSDATA"):
        raise SystemExit(
            "no SESSDATA from opencli — run: opencli bilibili login && opencli bilibili whoami"
        )
    return data


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync BILI_COOKIE from opencli")
    p.add_argument("--test", action="store_true", help="smoke-test homepage after save")
    args = p.parse_args(argv)

    print("opencli bilibili export-cookie ...", flush=True)
    data = export_cookie_via_opencli()
    cookie = str(data.get("cookie") or "")
    print(
        f"got cookie len={len(cookie)} "
        f"SESSDATA={data.get('has_SESSDATA')} "
        f"bili_jct={data.get('has_bili_jct')} "
        f"uid_field={data.get('has_DedeUserID')}",
        flush=True,
    )

    cmd = [sys.executable, str(ROOT / "scripts" / "set_bili_cookie.py"), cookie]
    if args.test:
        cmd.append("--test")
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
