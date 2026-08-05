"""SQLite WAL database — single source of truth for v2."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .models import Job, JobKind, JobStatus, Run, SubtitlePayload, SubtitleStatus, Video

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    bvid TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    owner_mid TEXT NOT NULL DEFAULT '',
    owner_name TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discoveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    bvid TEXT NOT NULL,
    source TEXT NOT NULL,
    position INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    seen_at TEXT NOT NULL,
    UNIQUE(run_id, bvid)
);

CREATE TABLE IF NOT EXISTS subtitles (
    bvid TEXT NOT NULL,
    language TEXT NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    cues_json TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL DEFAULT '',
    retry_at REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (bvid, language)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    bvid TEXT NOT NULL,
    status TEXT NOT NULL,
    run_after REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    UNIQUE(kind, bvid)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_jobs_pending
    ON jobs(status, run_after, id);
CREATE INDEX IF NOT EXISTS idx_discoveries_run
    ON discoveries(run_id);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    """Thin SQLite facade with WAL and explicit job transitions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA)

    # --- videos / discoveries ---

    def upsert_video(self, video: Video) -> None:
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO videos (
                bvid, title, owner_mid, owner_name, published_at,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                title = excluded.title,
                owner_mid = excluded.owner_mid,
                owner_name = excluded.owner_name,
                published_at = CASE
                    WHEN excluded.published_at != '' THEN excluded.published_at
                    ELSE videos.published_at
                END,
                updated_at = excluded.updated_at
            """,
            (
                video.bvid,
                video.title or "",
                str(video.owner_mid or ""),
                video.owner_name or "",
                video.published_at or "",
                now,
                now,
            ),
        )

    def get_video(self, bvid: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM videos WHERE bvid = ?", (bvid,)
        ).fetchone()
        return dict(row) if row else None

    def start_run(self, kind: str) -> Run:
        started = _utc_now_iso()
        cur = self._conn.execute(
            """
            INSERT INTO runs (kind, started_at, finished_at, status, error)
            VALUES (?, ?, '', 'running', '')
            """,
            (kind, started),
        )
        return Run(id=int(cur.lastrowid), kind=kind, started_at=started, status="running")

    def finish_run(
        self, run_id: int, status: str, error: str = ""
    ) -> None:
        self._conn.execute(
            """
            UPDATE runs
            SET finished_at = ?, status = ?, error = ?
            WHERE id = ?
            """,
            (_utc_now_iso(), status, error or "", run_id),
        )

    def save_discovery(
        self,
        *,
        run_id: int,
        bvid: str,
        source: str,
        position: int,
        reason: str = "",
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO discoveries (run_id, bvid, source, position, reason, seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, bvid) DO UPDATE SET
                position = excluded.position,
                reason = excluded.reason,
                seen_at = excluded.seen_at,
                source = excluded.source
            """,
            (run_id, bvid, source, position, reason or "", _utc_now_iso()),
        )

    def list_discoveries(self, run_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM discoveries
            WHERE run_id = ?
            ORDER BY position ASC, id ASC
            """,
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- jobs ---

    def enqueue_once(self, kind: JobKind | str, bvid: str) -> bool:
        """
        Insert a pending job if (kind, bvid) is new.

        Returns True if a row was inserted, False if it already existed.
        """
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO jobs (kind, bvid, status, run_after, attempts, last_error)
            VALUES (?, ?, 'pending', 0, 0, '')
            """,
            (kind, bvid),
        )
        return cur.rowcount > 0

    def claim_next_job(
        self, kinds: Sequence[str] | None = None
    ) -> Job | None:
        """Atomically claim the oldest ready pending job."""
        now = time.time()
        kind_filter = ""
        params: list[Any] = [now]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            kind_filter = f" AND kind IN ({placeholders})"
            params.extend(kinds)

        with self.transaction():
            row = self._conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE status = 'pending' AND run_after <= ?{kind_filter}
                ORDER BY id ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if not row:
                return None
            job_id = int(row["id"])
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'running', attempts = attempts + 1
                WHERE id = ? AND status = 'pending'
                """,
                (job_id,),
            )
            claimed = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND status = 'running'",
                (job_id,),
            ).fetchone()
            if not claimed:
                return None
            return self._row_to_job(claimed)

    def complete_job(self, job_id: int) -> None:
        self._conn.execute(
            """
            UPDATE jobs
            SET status = 'done', last_error = ''
            WHERE id = ?
            """,
            (job_id,),
        )

    def retry_job(
        self,
        job_id: int,
        *,
        error: str,
        delay_seconds: float = 60.0,
    ) -> None:
        self._conn.execute(
            """
            UPDATE jobs
            SET status = 'pending',
                run_after = ?,
                last_error = ?
            WHERE id = ?
            """,
            (time.time() + max(0.0, delay_seconds), error or "", job_id),
        )

    def fail_job(self, job_id: int, *, error: str) -> None:
        self._conn.execute(
            """
            UPDATE jobs
            SET status = 'failed', last_error = ?
            WHERE id = ?
            """,
            (error or "", job_id),
        )

    def get_job(self, kind: str, bvid: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE kind = ? AND bvid = ?",
            (kind, bvid),
        ).fetchone()
        return self._row_to_job(row) if row else None

    def count_jobs(self, status: str | None = None) -> int:
        if status is None:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status = ?",
                (status,),
            ).fetchone()
        return int(row["c"]) if row else 0

    # --- subtitles ---

    def save_subtitle(self, payload: SubtitlePayload) -> None:
        now = _utc_now_iso()
        retry_at = 0.0
        if payload.status == "empty":
            # re-check in ~3 days
            retry_at = time.time() + 3 * 24 * 3600
        elif payload.status == "retry":
            retry_at = time.time() + 300

        self._conn.execute(
            """
            INSERT INTO subtitles (
                bvid, language, status, text, cues_json,
                fetched_at, retry_at, attempts, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(bvid, language) DO UPDATE SET
                status = excluded.status,
                text = excluded.text,
                cues_json = excluded.cues_json,
                fetched_at = excluded.fetched_at,
                retry_at = excluded.retry_at,
                attempts = subtitles.attempts + 1,
                error = excluded.error
            """,
            (
                payload.bvid,
                payload.language,
                payload.status,
                payload.text or "",
                payload.cues_json or "[]",
                now,
                retry_at,
                payload.error or "",
            ),
        )

    def get_subtitle(self, bvid: str, language: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM subtitles WHERE bvid = ? AND language = ?",
            (bvid, language),
        ).fetchone()
        return dict(row) if row else None

    def count_subtitles(self, status: str | None = None) -> int:
        if status is None:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM subtitles").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM subtitles WHERE status = ?",
                (status,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def count_videos(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()
        return int(row["c"]) if row else 0

    def count_runs(self, status: str | None = None) -> int:
        if status is None:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM runs WHERE status = ?",
                (status,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def latest_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def status_snapshot(self) -> dict[str, Any]:
        """Primary status payload for CLI."""
        job_by_status = {
            s: self.count_jobs(s)
            for s in ("pending", "running", "done", "failed")
        }
        sub_by_status = {
            s: self.count_subtitles(s)
            for s in ("pending", "ok", "empty", "retry", "failed")
        }
        return {
            "database": str(self.path),
            "videos": self.count_videos(),
            "runs": self.count_runs(),
            "jobs": {
                "total": self.count_jobs(),
                **job_by_status,
            },
            "subtitles": {
                "total": self.count_subtitles(),
                **sub_by_status,
            },
            "schema": "ready",
        }

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=int(row["id"]),
            kind=row["kind"],  # type: ignore[arg-type]
            bvid=str(row["bvid"]),
            status=row["status"],  # type: ignore[arg-type]
            run_after=float(row["run_after"] or 0),
            attempts=int(row["attempts"] or 0),
            last_error=str(row["last_error"] or ""),
        )


def dumps_cues(cues: list[dict[str, Any]] | None) -> str:
    return json.dumps(cues or [], ensure_ascii=False)
