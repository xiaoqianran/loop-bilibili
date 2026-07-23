"""Processor protocol — extension point for LLM / clean / notify / CI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..models import BatchConfig, BatchStats, SubtitleResult


@dataclass
class ProcessContext:
    """Per-item runtime context passed to processors."""

    out_dir: Path
    bvid: str
    index: int  # 1-based among current todo
    todo_total: int
    cookie: str
    config: BatchConfig
    # shared mutable bag for processors in the same batch
    state: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SubtitleProcessor(Protocol):
    """
    Pluggable step after each fetch (and optional batch hooks).

    Implementations should be pure-ish: prefer writing under out_dir and
    enriching `row` / `result.extras` instead of global side effects.
    Future: LLM summarize, embedding, quality filter, webhook.
    """

    name: str

    def on_batch_start(self, out_dir: Path, config: BatchConfig, state: dict[str, Any]) -> None:
        ...

    def on_result(
        self,
        result: SubtitleResult,
        row: dict[str, Any],
        ctx: ProcessContext,
    ) -> dict[str, Any]:
        """Return (possibly enriched) row dict."""
        ...

    def on_batch_end(
        self,
        out_dir: Path,
        stats: BatchStats,
        state: dict[str, Any],
    ) -> None:
        ...


class BaseProcessor:
    """Convenience base with no-op hooks."""

    name = "base"

    def on_batch_start(self, out_dir: Path, config: BatchConfig, state: dict[str, Any]) -> None:
        return None

    def on_result(
        self,
        result: SubtitleResult,
        row: dict[str, Any],
        ctx: ProcessContext,
    ) -> dict[str, Any]:
        return row

    def on_batch_end(
        self,
        out_dir: Path,
        stats: BatchStats,
        state: dict[str, Any],
    ) -> None:
        return None
