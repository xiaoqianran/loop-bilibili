"""Default artifact writers (srt / txt / slim index)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import BatchConfig, BatchStats, SubtitleResult
from ..srt import write_srt
from .base import BaseProcessor, ProcessContext


def cues_to_txt(cues: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in cues:
        t = str(c.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if t:
            lines.append(t)
    return "\n".join(lines) + ("\n" if lines else "")


class WriteSrtProcessor(BaseProcessor):
    name = "write_srt"

    def on_result(
        self,
        result: SubtitleResult,
        row: dict[str, Any],
        ctx: ProcessContext,
    ) -> dict[str, Any]:
        if not ctx.config.write_srt:
            return row
        if result.status != "ok" or not result.data:
            return row
        path = ctx.out_dir / "srt" / f"{result.bvid}.srt"
        write_srt(path, result.data)
        row["srt"] = str(path.relative_to(ctx.out_dir))
        return row


class WriteTxtProcessor(BaseProcessor):
    """Plain text for RAG / future LLM (written live during crawl)."""

    name = "write_txt"

    def on_result(
        self,
        result: SubtitleResult,
        row: dict[str, Any],
        ctx: ProcessContext,
    ) -> dict[str, Any]:
        if not ctx.config.write_txt:
            return row
        if result.status != "ok" or not result.data:
            return row
        path = ctx.out_dir / "txt" / f"{result.bvid}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = cues_to_txt(result.data)
        path.write_text(text, encoding="utf-8")
        row["txt"] = str(path.relative_to(ctx.out_dir))
        return row


class IndexAppendProcessor(BaseProcessor):
    """Append slim index.jsonl line after each item (pack-ready, streamable)."""

    name = "index_jsonl"

    def on_batch_start(self, out_dir: Path, config: BatchConfig, state: dict[str, Any]) -> None:
        if not config.write_index:
            return
        path = out_dir / "index.jsonl"
        # on resume, rewrite from scratch at end is safer; here we truncate once per run
        # only if not resume-merging — pipeline sets state["index_mode"]
        if state.get("index_reset"):
            path.write_text("", encoding="utf-8")
        state["index_path"] = path

    def on_result(
        self,
        result: SubtitleResult,
        row: dict[str, Any],
        ctx: ProcessContext,
    ) -> dict[str, Any]:
        if not ctx.config.write_index:
            return row
        slim = {
            "bvid": result.bvid,
            "status": result.status,
            "title": result.title,
            "cue_count": result.cue_count,
            "lan": result.lan,
            "source": result.source,
            "author": result.author,
        }
        if row.get("srt"):
            slim["srt"] = row["srt"]
        if row.get("txt"):
            slim["txt"] = row["txt"]
        if result.status == "empty":
            slim["reason"] = row.get("reason") or result.error or "no_subtitle"
        if result.status == "error" and result.error:
            slim["error"] = result.error
        path = ctx.state.get("index_path") or (ctx.out_dir / "index.jsonl")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
        return row


class NormalizeCuesProcessor(BaseProcessor):
    """
    Lightweight in-fetch cleanup (foundation for richer LLM steps later).

    - drop empty content
    - collapse whitespace
    - reindex sequential
    """

    name = "normalize_cues"

    def on_result(
        self,
        result: SubtitleResult,
        row: dict[str, Any],
        ctx: ProcessContext,
    ) -> dict[str, Any]:
        if result.status != "ok" or not result.data:
            return row
        cleaned: list[dict[str, Any]] = []
        for c in result.data:
            text = " ".join(str(c.get("content") or "").split())
            if not text:
                continue
            nc = dict(c)
            nc["content"] = text
            nc["index"] = len(cleaned) + 1
            cleaned.append(nc)
        result.data = cleaned
        result.cue_count = len(cleaned)
        row["data"] = cleaned
        row["cue_count"] = len(cleaned)
        if not cleaned:
            result.status = "empty"
            result.error = "empty_after_normalize"
            row["status"] = "empty"
            row["reason"] = "empty_after_normalize"
        return row


def default_processors(config: BatchConfig) -> list[BaseProcessor]:
    """Built-in chain: normalize → srt → txt → index."""
    chain: list[BaseProcessor] = [NormalizeCuesProcessor()]
    if config.write_srt:
        chain.append(WriteSrtProcessor())
    if config.write_txt:
        chain.append(WriteTxtProcessor())
    if config.write_index:
        chain.append(IndexAppendProcessor())
    return chain
