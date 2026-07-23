"""Serial batch + resume."""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .client import SubtitleResult, fetch_subtitle
from .srt import write_srt
from .util import load_bvids_from_items, pending_keys

FetchFn = Callable[..., SubtitleResult]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_bvids_from_catalog(path: Path) -> list[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("catalog JSON must be a list")
    return load_bvids_from_items(data)


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return set(raw.get("done") or [])
    if isinstance(raw, list):
        return set(raw)
    return set()


def load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else []


def upsert_result(results: list[dict], row: dict) -> list[dict]:
    bvid = row.get("bvid")
    return [r for r in results if r.get("bvid") != bvid] + [row]


def write_json(path: Path, data: object) -> None:
    """Atomic-ish write: tmp sibling then replace (safer on crash mid-batch)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _dedupe_preserve(keys: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        s = str(k).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def run_batch(
    bvids: Iterable[str],
    out_dir: Path,
    *,
    delay: float = 0.4,
    jitter: float = 0.15,
    resume: bool = True,
    cookie: str | None = None,
    write_srt_files: bool = True,
    fetch_fn: FetchFn | None = None,
) -> Path:
    """
    Serial fetch with resume.

    `fetch_fn` is injectable for tests (defaults to fetch_subtitle).
    status ok/empty → marked done; status error → left for resume retry.
    """
    fetch = fetch_fn or fetch_subtitle
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items_dir = out_dir / "items"
    items_dir.mkdir(exist_ok=True)
    srt_dir = out_dir / "srt"
    if write_srt_files:
        srt_dir.mkdir(exist_ok=True)

    all_bvids = _dedupe_preserve(bvids)
    done_path = out_dir / "done.json"
    results_path = out_dir / "results.json"
    progress_path = out_dir / ".progress.json"

    done = load_done(done_path) if resume else set()
    results = load_results(results_path) if resume else []
    todo = pending_keys(all_bvids, done, resume=resume)

    print(
        f"subtitle(subbatch) total={len(all_bvids)} done={len(done)} "
        f"todo={len(todo)} delay={delay}±{jitter}s serial",
        flush=True,
    )

    for i, bvid in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {bvid}", flush=True)
        t0 = time.time()
        r = fetch(bvid, cookie=cookie)
        elapsed = time.time() - t0
        row = r.to_row(elapsed_s=elapsed)
        row["fetched_at"] = utc_now()
        print(
            f"  -> {r.status} cues={r.cue_count} src={r.source or '-'} {elapsed:.2f}s",
            flush=True,
        )

        results = upsert_result(results, row)
        write_json(items_dir / f"{bvid}.json", row)
        if write_srt_files and r.status == "ok" and r.data:
            write_srt(srt_dir / f"{bvid}.srt", r.data)

        if r.status in ("ok", "empty"):
            done.add(bvid)

        write_json(results_path, results)
        write_json(done_path, {"done": sorted(done), "updated_at": utc_now()})
        write_json(
            progress_path,
            {
                "current": bvid,
                "index": i,
                "todo": len(todo),
                "status": "running",
                "updated_at": utc_now(),
            },
        )

        if i < len(todo) and delay > 0:
            sl = max(0.0, delay + random.uniform(-jitter, jitter))
            time.sleep(sl)

    write_json(
        out_dir / "meta.json",
        {
            "tool": "loop-bilibili",
            "plugin": "bili_subbatch",
            "total_requested": len(all_bvids),
            "done_count": len(done),
            "results_count": len(results),
            "delay": delay,
            "exported_at": utc_now(),
        },
    )
    write_json(
        progress_path,
        {"status": "complete", "done_count": len(done), "updated_at": utc_now()},
    )
    print(f"complete -> {out_dir}", flush=True)
    return out_dir
