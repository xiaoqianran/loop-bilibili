"""Source protocol helpers."""

from __future__ import annotations

from typing import Protocol, Sequence

from loop_bilibili.models import Candidate


class VideoSource(Protocol):
    name: str

    def fetch(self) -> Sequence[Candidate]:
        ...
