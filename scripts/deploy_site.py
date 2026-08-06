#!/usr/bin/env python3
"""
Reliable site publish helper (local or ops machine).

Does NOT depend on GitHub-hosted runner queue for the *build* step:
  1. Optionally push local DBs to HF (source of truth for CI)
  2. Build static site from local data/v2 (or after HF fetch)
  3. Trigger the Pages workflow (HF → build → deploy-pages)
  4. Optionally also mirror to gh-pages branch (backup; not primary CDN)

Primary CDN path = Settings → Pages → GitHub Actions (build_type=workflow).

Usage:
  # Build from local dbs + trigger Actions deploy
  python scripts/deploy_site.py

  # Only build + trigger (no HF push)
  python scripts/deploy_site.py --no-push-hf

  # Wait until live mermaid is visible
  python scripts/deploy_site.py --wait

  # Snapshots to fetch on CI (comma-separated)
  python scripts/deploy_site.py --snapshots loop,haianyu
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            continue


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], *, check: bool = True) -> int:
    log("$ " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(ROOT))
    if check and p.returncode != 0:
        raise SystemExit(p.returncode)
    return int(p.returncode)


def build_local(out: Path) -> None:
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_subtitle_site.py"),
            "--from-dir",
            str(ROOT / "data" / "v2"),
            "--out",
            str(out),
            "--base-url",
            "/loop-bilibili",
        ]
    )
    assert (out / "index.html").is_file()
    n = 0
    for p in (out / "data").rglob("v/*.json") if (out / "data").is_dir() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("diagram_count") or d.get("diagrams"):
            n += 1
    log(f"local build ok → {out} (videos_with_mermaid={n})")


def push_hf_names(names: list[str]) -> None:
    for name in names:
        db = ROOT / "data" / "v2" / f"{name}.db"
        if not db.is_file():
            log(f"skip hf push missing {db}")
            continue
        run(
            [
                sys.executable,
                str(ROOT / "scripts" / "push_hf_v2.py"),
                "--name",
                name,
                "--db",
                str(db),
            ],
            check=False,
        )


def clear_pages_locks() -> int:
    """Best-effort: mark github-pages deployments inactive."""
    try:
        token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        log("gh auth token unavailable — skip lock clear")
        return 0
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "loop-bilibili-deploy-site",
        "Content-Type": "application/json",
    }

    def api(method: str, url: str, data: dict | None = None):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None

    n = 0
    try:
        # Prefer branch publish from gh-pages (works without Actions runners).
        # workflow mode remains available when Actions runners are healthy.
        try:
            api(
                "PUT",
                "https://api.github.com/repos/xiaoqianran/loop-bilibili/pages",
                {
                    "build_type": "legacy",
                    "source": {"branch": "gh-pages", "path": "/"},
                },
            )
            log("pages build_type=legacy source=gh-pages")
        except Exception as exc:
            log(f"pages PUT note: {exc}")
        try:
            api(
                "POST",
                "https://api.github.com/repos/xiaoqianran/loop-bilibili/pages/builds",
                None,
            )
            log("requested pages build")
        except Exception as exc:
            log(f"pages build request note: {exc}")

        status, deployments = api(
            "GET",
            "https://api.github.com/repos/xiaoqianran/loop-bilibili/deployments"
            "?environment=github-pages&per_page=30",
        )
        for d in deployments or []:
            did = d.get("id")
            if not did:
                continue
            try:
                api(
                    "POST",
                    f"https://api.github.com/repos/xiaoqianran/loop-bilibili/deployments/{did}/statuses",
                    {
                        "state": "inactive",
                        "description": "clear stuck pages lock",
                        "environment": "github-pages",
                        "auto_inactive": False,
                    },
                )
                n += 1
            except Exception:
                pass
    except Exception as exc:
        log(f"clear locks failed: {exc}")
        return 0
    log(f"cleared/attempted inactive on {n} deployments")
    return n


def push_gh_pages(site_dir: Path) -> int:
    """Force-push built site to origin/gh-pages (branch publish backup)."""
    if not site_dir.is_dir() or not (site_dir / "index.html").is_file():
        log(f"error: site not ready at {site_dir}")
        return 2
    try:
        token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception as exc:
        log(f"error: gh auth token: {exc}")
        return 2
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="gp-deploy-"))
    try:
        # copy site
        for item in site_dir.iterdir():
            dest = tmp / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        (tmp / ".nojekyll").write_text("", encoding="utf-8")
        (tmp / ".deploy-stamp").write_text(
            time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "\n", encoding="utf-8"
        )
        cmds = [
            ["git", "init", "-b", "gh-pages"],
            ["git", "add", "-A"],
            [
                "git",
                "-c",
                "user.email=bot@loop-bilibili.local",
                "-c",
                "user.name=loop-bilibili-bot",
                "commit",
                "-m",
                f"publish: site {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())}",
            ],
            [
                "git",
                "remote",
                "add",
                "origin",
                f"https://x-access-token:{token}@github.com/xiaoqianran/loop-bilibili.git",
            ],
            ["git", "push", "-f", "origin", "gh-pages"],
        ]
        for cmd in cmds:
            # hide token in log
            shown = " ".join(cmd).replace(token, "***")
            log(f"$ {shown}")
            p = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True)
            if p.returncode != 0:
                log((p.stderr or p.stdout or "")[-500:])
                return p.returncode
        log("gh-pages force-pushed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def trigger_workflow(snapshots: str) -> str | None:
    args = ["gh", "workflow", "run", "pages.yml", "-R", "xiaoqianran/loop-bilibili"]
    if snapshots:
        args += ["-f", f"snapshots={snapshots}"]
    run(args)
    time.sleep(3)
    # resolve newest run id
    out = subprocess.check_output(
        [
            "gh",
            "run",
            "list",
            "-R",
            "xiaoqianran/loop-bilibili",
            "-w",
            "pages.yml",
            "-L",
            "1",
            "--json",
            "databaseId,status,url",
        ],
        text=True,
    )
    rows = json.loads(out)
    if not rows:
        return None
    rid = str(rows[0]["databaseId"])
    log(f"workflow run {rid} status={rows[0].get('status')} {rows[0].get('url')}")
    return rid


def wait_run(run_id: str, timeout_s: float = 900) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        out = subprocess.check_output(
            [
                "gh",
                "run",
                "view",
                run_id,
                "-R",
                "xiaoqianran/loop-bilibili",
                "--json",
                "status,conclusion",
            ],
            text=True,
        )
        info = json.loads(out)
        st = f"{info.get('status')} {info.get('conclusion') or ''}".strip()
        log(f"run {run_id}: {st}")
        if info.get("status") == "completed":
            return str(info.get("conclusion") or "unknown")
        time.sleep(12)
    return "timeout"


def wait_live(timeout_s: float = 300) -> bool:
    urls = [
        "https://xiaoqianran.github.io/loop-bilibili/data/loop/meta.json",
        "https://xiaoqianran.github.io/loop-bilibili/data/haianyu/meta.json",
    ]
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        for url in urls:
            try:
                req = urllib.request.Request(
                    url + f"?t={int(time.time())}",
                    headers={"Cache-Control": "no-cache", "User-Agent": "loop-deploy"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status != 200:
                        continue
                    data = json.loads(resp.read().decode())
                mer = data.get("mermaid")
                log(f"live {url.split('/')[-2]} mermaid={mer}")
                if mer is not None and int(mer) > 0:
                    return True
            except Exception as exc:
                log(f"live probe {url}: {exc}")
        time.sleep(15)
    return False


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    p = argparse.ArgumentParser(description="Build + publish loop-bilibili Pages")
    p.add_argument("--no-push-hf", action="store_true")
    p.add_argument("--no-build", action="store_true", help="skip local build smoke")
    p.add_argument("--no-trigger", action="store_true", help="skip Actions workflow trigger")
    p.add_argument(
        "--no-gh-pages",
        action="store_true",
        help="skip force-push to gh-pages branch",
    )
    p.add_argument("--no-clear-locks", action="store_true")
    p.add_argument(
        "--snapshots",
        default="loop,haianyu,xiaolaoshi",
        help="CI fetch list; also HF push names when pushing",
    )
    p.add_argument("--wait", action="store_true", help="wait for workflow + live mermaid")
    p.add_argument("--out", default=str(ROOT / "site"))
    args = p.parse_args(argv)

    names = [x.strip() for x in args.snapshots.split(",") if x.strip()]
    out = Path(args.out)

    if not args.no_push_hf:
        push_hf_names(names)

    if not args.no_build:
        build_local(out)

    if not args.no_gh_pages:
        rc = push_gh_pages(out if out.is_dir() else ROOT / "site")
        if rc != 0:
            log(f"warn: gh-pages push rc={rc}")

    if not args.no_clear_locks:
        clear_pages_locks()

    if args.no_trigger:
        log("done (no workflow trigger)")
        log("CDN mirrors (usually instant):")
        log("  https://cdn.jsdelivr.net/gh/xiaoqianran/loop-bilibili@gh-pages/index.html")
        log("  https://raw.githack.com/xiaoqianran/loop-bilibili/gh-pages/index.html")
        return 0

    rid = trigger_workflow(",".join(names))
    if not rid:
        log("error: could not resolve workflow run id")
        return 2

    if not args.wait:
        log(f"triggered run {rid} — check Actions UI or re-run with --wait")
        log("instant preview via jsDelivr @gh-pages if Actions queue is slow")
        return 0

    conclusion = wait_run(rid)
    if conclusion != "success":
        log(f"workflow conclusion={conclusion}")
        subprocess.run(
            ["gh", "run", "view", rid, "-R", "xiaoqianran/loop-bilibili", "--log-failed"],
            check=False,
        )
        log("fallback: open jsDelivr mirror (gh-pages branch content)")
        log("  https://cdn.jsdelivr.net/gh/xiaoqianran/loop-bilibili@gh-pages/ups/loop/")
        return 1

    ok = wait_live()
    if not ok:
        log("workflow green but live mermaid not visible yet (CDN lag?)")
        return 3
    log("LIVE OK — mermaid visible on github.io")
    log("https://xiaoqianran.github.io/loop-bilibili/ups/loop/")
    log("https://xiaoqianran.github.io/loop-bilibili/ups/haianyu/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
