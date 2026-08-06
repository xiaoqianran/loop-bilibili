"""Domain models for the v2 ingest service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SubtitleStatus = Literal["pending", "ok", "empty", "retry", "failed"]
JobKind = Literal["fetch_subtitle", "analyze"]
JobStatus = Literal["pending", "running", "done", "failed"]
SourceName = Literal["homepage", "creator"]
AnalysisStatus = Literal["pending", "ok", "failed", "empty"]

# Default dual-model stack (first = UI / list default)
DEFAULT_AI_MODELS: tuple[str, ...] = (
    "openai/gpt-oss-120b",
    "google/diffusiongemma-26b-a4b-it",
)


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
class AnalysisPayload:
    """LLM post-process result for one (bvid, model)."""

    bvid: str
    status: AnalysisStatus
    mode: str = "mermaid"
    model: str = ""
    markdown: str = ""
    diagrams_json: str = "[]"
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
    # For analyze jobs: which LLM to run. Empty for fetch_subtitle.
    model: str = ""


@dataclass
class Run:
    id: int
    kind: str
    started_at: str
    finished_at: str = ""
    status: str = "running"
    error: str = ""


@dataclass
class AiConfig:
    """LLM post-process settings (multi-model Mermaid diagrams)."""

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    # Ordered list; first entry is the UI / list default model.
    models: list[str] = field(default_factory=lambda: list(DEFAULT_AI_MODELS))
    temperature: float = 0.4
    max_tokens: int = 8192
    max_subtitle_chars: int = 80_000
    timeout_s: float = 180.0
    mode: str = "mermaid"
    custom_instruction: str = ""
    request_retries: int = 2
    request_retry_backoff_s: float = 2.0
    max_attempts: int = 3
    job_retry_delay_s: float = 60.0

    @property
    def model(self) -> str:
        """Backward-compat alias: default (first) model id."""
        return self.default_model

    @property
    def default_model(self) -> str:
        for m in self.models:
            if str(m).strip():
                return str(m).strip()
        return DEFAULT_AI_MODELS[0]

    def model_list(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for m in self.models:
            s = str(m).strip()
            if s and s not in seen:
                out.append(s)
                seen.add(s)
        return out or list(DEFAULT_AI_MODELS)


@dataclass
class AppConfig:
    """Runtime config loaded from config.toml + env."""

    database_path: str = "data/v2/loop.db"
    creators: list[str] = field(default_factory=list)
    homepage_enabled: bool = True
    homepage_pages: int = 1
    homepage_ps: int = 12
    homepage_interval_s: float = 600.0
    poll_interval: float = 2.0
    subtitle_language: str = "zh"
    job_delay: float = 0.5
    job_jitter: float = 0.15
    retry_delay: float = 60.0
    risk_base_delay: float = 15.0
    risk_max_delay: float = 300.0
    jobs_per_cycle: int = 50
    require_cookie: bool = False
    cookie: str = ""
    preference_enabled: bool = True
    preference_path: str = "preferences.toml"
    ai: AiConfig = field(default_factory=AiConfig)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
