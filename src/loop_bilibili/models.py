"""Domain models for the v2 ingest service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SubtitleStatus = Literal["pending", "ok", "empty", "retry", "failed"]
JobKind = Literal["fetch_subtitle", "analyze"]
JobStatus = Literal["pending", "running", "done", "failed"]
SourceName = Literal["homepage", "creator"]


@dataclass
class Video:
    bvid: str
    title: str = ""
    owner_mid: str = ""
    owner_name: str = ""
    published_at: str = ""


@dataclass
class Candidate:
    """One discovery from a video source."""

    video: Video
    reason: str = ""
    source: SourceName = "creator"


@dataclass
class SubtitlePayload:
    bvid: str
    language: str
    status: SubtitleStatus
    text: str = ""
    cues_json: str = "[]"
    error: str = ""


@dataclass
class Job:
    id: int
    kind: JobKind
    bvid: str
    status: JobStatus
    run_after: float = 0.0
    attempts: int = 0
    last_error: str = ""


@dataclass
class Run:
    id: int
    kind: str
    started_at: str
    finished_at: str = ""
    status: str = "running"
    error: str = ""


@dataclass
class AppConfig:
    """Runtime config loaded from config.toml + env."""

    database_path: str = "data/v2/loop.db"
    creators: list[str] = field(default_factory=list)
    homepage_enabled: bool = True
    homepage_pages: int = 1
    homepage_ps: int = 12
    poll_interval: float = 2.0
    subtitle_language: str = "zh"
    # Inter-job pacing (SubBatch/v1 batch style)
    job_delay: float = 0.5
    job_jitter: float = 0.15
    retry_delay: float = 60.0
    risk_base_delay: float = 15.0
    risk_max_delay: float = 300.0
    require_cookie: bool = False
    cookie: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
