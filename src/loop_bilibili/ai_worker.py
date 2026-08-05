"""AI analyze worker stub — real LLM work comes later."""

from __future__ import annotations

from .database import Database
from .models import Job
from .worker import process_analyze_job


def analyze_stub(job: Job, db: Database) -> str:
    """Mark analyze jobs complete without calling an LLM."""
    return process_analyze_job(job, db)
