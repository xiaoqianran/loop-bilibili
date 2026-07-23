"""Shared domain models for subtitle fetch + pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Status = Literal["ok", "empty", "error"]


@dataclass
class SubtitleResult:
    """One video's subtitle fetch outcome."""

    bvid: str
    status: Status  # ok | empty | error
    cue_count: int = 0
    lan: str = ""
    aid: int | None = None
    cid: int | None = None
    title: str = ""
    author: str = ""
    data: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    source: str = ""  # player_wbi | dm_view | ai_stat
    # pipeline-enriched fields (optional processors)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_row(self, *, elapsed_s: float | None = None) -> dict[str, Any]:
        row: dict[str, Any] = {
            "bvid": self.bvid,
            "status": self.status,
            "cue_count": self.cue_count,
            "lan": self.lan,
            "aid": self.aid,
            "cid": self.cid,
            "title": self.title,
            "author": self.author,
            "source": self.source,
            "data": self.data,
            "plugin": "bili_subbatch",
        }
        if elapsed_s is not None:
            row["elapsed_s"] = round(elapsed_s, 3)
        if self.status == "empty":
            row["reason"] = self.error or "no_subtitle"
        if self.error and self.status == "error":
            row["error"] = self.error
        if self.extras:
            row["extras"] = self.extras
        return row

    def plain_text(self) -> str:
        """Join cue contents (for RAG / LLM)."""
        lines: list[str] = []
        for c in self.data:
            t = str(c.get("content") or "").strip()
            if t:
                lines.append(t)
        return "\n".join(lines)


@dataclass
class BatchConfig:
    """Serial batch options (extensible without breaking callers)."""

    delay: float = 0.4
    jitter: float = 0.15
    resume: bool = True
    cookie: str | None = None
    write_srt: bool = True
    write_txt: bool = True
    write_index: bool = True
    # When True, strip full cue arrays from results.json (items still full if needed)
    slim_results: bool = False
    # Keep full data in items/*.json (default True for re-pack)
    keep_item_cues: bool = True


@dataclass
class BatchStats:
    """Summary returned by pipeline.run()."""

    out_dir: str
    total: int = 0
    done: int = 0
    ok: int = 0
    empty: int = 0
    error: int = 0
    cues: int = 0
    processors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
