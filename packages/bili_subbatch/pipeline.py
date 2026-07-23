"""
Subtitle pipeline — fetch + ordered processors + durable resume.

Extension points:
  - inject fetch_fn (tests / alternate sources)
  - inject processors (LLM, filters, webhooks) via BatchConfig or run(..., processors=)
  - BatchConfig flags for artifact layout
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .client import fetch_subtitle
from .models import BatchConfig, BatchStats, SubtitleResult
from .processors import ProcessContext, SubtitleProcessor, default_processors
from .util import pending_keys

FetchFn = Callable[..., SubtitleResult]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


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


def _slim_row_for_results(row: dict[str, Any]) -> dict[str, Any]:
    """Drop heavy cue bodies from aggregate results.json."""
    slim = {k: v for k, v in row.items() if k != "data"}
    return slim


class SubtitlePipeline:
    """
    Serial fetch pipeline with resume + processor chain.

    Example (future LLM)::

        pipe = SubtitlePipeline(BatchConfig(), processors=[NormalizeCues(), LlmSummary()])
        pipe.run(bvids, out_dir)
    """

    def __init__(
        self,
        config: BatchConfig | None = None,
        *,
        fetch_fn: FetchFn | None = None,
        processors: Sequence[SubtitleProcessor] | None = None,
    ):
        self.config = config or BatchConfig()
        self.fetch_fn = fetch_fn or fetch_subtitle
        if processors is not None:
            self.processors: list[SubtitleProcessor] = list(processors)
        else:
            self.processors = list(default_processors(self.config))

    def run(self, bvids: Iterable[str], out_dir: Path | str) -> BatchStats:
        cfg = self.config
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        items_dir = out_dir / "items"
        items_dir.mkdir(exist_ok=True)
        if cfg.write_srt:
            (out_dir / "srt").mkdir(exist_ok=True)
        if cfg.write_txt:
            (out_dir / "txt").mkdir(exist_ok=True)

        all_bvids = _dedupe_preserve(bvids)
        done_path = out_dir / "done.json"
        results_path = out_dir / "results.json"
        progress_path = out_dir / ".progress.json"

        done = load_done(done_path) if cfg.resume else set()
        results = load_results(results_path) if cfg.resume else []
        todo = pending_keys(all_bvids, done, resume=cfg.resume)

        state: dict[str, Any] = {
            # rebuild index each full non-resume run; on resume append only new
            "index_reset": not cfg.resume or not (out_dir / "index.jsonl").exists(),
        }
        for p in self.processors:
            p.on_batch_start(out_dir, cfg, state)

        print(
            f"subtitle(pipeline) total={len(all_bvids)} done={len(done)} "
            f"todo={len(todo)} delay={cfg.delay}±{cfg.jitter}s "
            f"processors=[{', '.join(getattr(p, 'name', '?') for p in self.processors)}]",
            flush=True,
        )

        stats = BatchStats(
            out_dir=str(out_dir),
            total=len(all_bvids),
            done=len(done),
            processors=[getattr(p, "name", type(p).__name__) for p in self.processors],
        )
        cookie = cfg.cookie or ""

        for i, bvid in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {bvid}", flush=True)
            t0 = time.time()
            try:
                r = self.fetch_fn(bvid, cookie=cfg.cookie)
            except Exception as e:
                r = SubtitleResult(bvid=bvid, status="error", error=str(e)[:500])
            elapsed = time.time() - t0
            row = r.to_row(elapsed_s=elapsed)
            row["fetched_at"] = utc_now()

            ctx = ProcessContext(
                out_dir=out_dir,
                bvid=bvid,
                index=i,
                todo_total=len(todo),
                cookie=cookie if isinstance(cookie, str) else "",
                config=cfg,
                state=state,
            )
            for proc in self.processors:
                row = proc.on_result(r, row, ctx)

            print(
                f"  -> {r.status} cues={r.cue_count} src={r.source or '-'} "
                f"{elapsed:.2f}s",
                flush=True,
            )

            # persist item (optionally without cues if keep_item_cues False)
            item_row = row
            if not cfg.keep_item_cues and "data" in item_row:
                item_row = {k: v for k, v in row.items() if k != "data"}
            write_json(items_dir / f"{bvid}.json", item_row)

            store_row = _slim_row_for_results(row) if cfg.slim_results else row
            results = upsert_result(results, store_row)

            if r.status in ("ok", "empty"):
                done.add(bvid)
            if r.status == "ok":
                stats.ok += 1
                stats.cues += int(r.cue_count or 0)
            elif r.status == "empty":
                stats.empty += 1
            else:
                stats.error += 1

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

            if i < len(todo) and cfg.delay > 0:
                sl = max(0.0, cfg.delay + random.uniform(-cfg.jitter, cfg.jitter))
                time.sleep(sl)

        stats.done = len(done)
        write_json(
            out_dir / "meta.json",
            {
                "tool": "loop-bilibili",
                "plugin": "bili_subbatch",
                "pipeline": True,
                "total_requested": len(all_bvids),
                "done_count": len(done),
                "results_count": len(results),
                "ok": stats.ok,
                "empty": stats.empty,
                "error": stats.error,
                "cues": stats.cues,
                "delay": cfg.delay,
                "jitter": cfg.jitter,
                "processors": stats.processors,
                "exported_at": utc_now(),
            },
        )
        write_json(
            progress_path,
            {
                "status": "complete",
                "done_count": len(done),
                "updated_at": utc_now(),
                **stats.to_dict(),
            },
        )
        for p in self.processors:
            p.on_batch_end(out_dir, stats, state)
        print(f"complete -> {out_dir} ok={stats.ok} empty={stats.empty} error={stats.error}", flush=True)
        return stats


def run_batch(
    bvids: Iterable[str],
    out_dir: Path,
    *,
    delay: float = 0.4,
    jitter: float = 0.15,
    resume: bool = True,
    cookie: str | None = None,
    write_srt_files: bool = True,
    write_txt: bool = True,
    fetch_fn: FetchFn | None = None,
    processors: Sequence[SubtitleProcessor] | None = None,
    config: BatchConfig | None = None,
) -> Path:
    """
    Backward-compatible entry (used by modules/subtitle/export.py).

    Prefer SubtitlePipeline for new code.
    """
    cfg = config or BatchConfig(
        delay=delay,
        jitter=jitter,
        resume=resume,
        cookie=cookie,
        write_srt=write_srt_files,
        write_txt=write_txt,
    )
    # allow kwargs to override config fields when config also passed
    if config is not None:
        cfg.delay = delay if delay != 0.4 or config.delay == 0.4 else config.delay
    pipe = SubtitlePipeline(cfg, fetch_fn=fetch_fn, processors=processors)
    pipe.run(bvids, out_dir)
    return Path(out_dir)
