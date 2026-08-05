"""Unit tests for shipped Database (SQLite WAL) layer."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from loop_bilibili.database import Database
from loop_bilibili.models import SubtitlePayload, Video


class DatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.db = Database(self.db_path)
        self.db.init_schema()

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_schema_and_wal(self) -> None:
        mode = self.db._conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")
        tables = {
            r[0]
            for r in self.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for name in ("videos", "discoveries", "subtitles", "jobs", "runs"):
            self.assertIn(name, tables)

    def test_upsert_video(self) -> None:
        v = Video(
            bvid="BV1TEST00001",
            title="t1",
            owner_mid="123",
            owner_name="up",
            published_at="2024-01-01",
        )
        self.db.upsert_video(v)
        row = self.db.get_video("BV1TEST00001")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["title"], "t1")
        self.assertEqual(row["owner_mid"], "123")
        first_seen = row["first_seen_at"]

        self.db.upsert_video(
            Video(bvid="BV1TEST00001", title="t2", owner_mid="123", owner_name="up2")
        )
        row2 = self.db.get_video("BV1TEST00001")
        assert row2 is not None
        self.assertEqual(row2["title"], "t2")
        self.assertEqual(row2["owner_name"], "up2")
        self.assertEqual(row2["first_seen_at"], first_seen)
        self.assertEqual(row2["published_at"], "2024-01-01")

    def test_save_discovery_ordered(self) -> None:
        run = self.db.start_run("creator")
        self.db.upsert_video(Video(bvid="BVa"))
        self.db.upsert_video(Video(bvid="BVb"))
        self.db.save_discovery(
            run_id=run.id, bvid="BVa", source="creator", position=0, reason="new"
        )
        self.db.save_discovery(
            run_id=run.id, bvid="BVb", source="creator", position=1, reason=""
        )
        rows = self.db.list_discoveries(run.id)
        self.assertEqual([r["bvid"] for r in rows], ["BVa", "BVb"])
        self.assertEqual(rows[0]["position"], 0)
        self.assertEqual(rows[0]["reason"], "new")
        self.db.finish_run(run.id, "ok")
        self.assertEqual(self.db.count_runs("ok"), 1)

    def test_enqueue_once_idempotent(self) -> None:
        self.assertTrue(self.db.enqueue_once("fetch_subtitle", "BV1"))
        self.assertFalse(self.db.enqueue_once("fetch_subtitle", "BV1"))
        self.assertTrue(self.db.enqueue_once("analyze", "BV1"))
        self.assertEqual(self.db.count_jobs("pending"), 2)
        job = self.db.get_job("fetch_subtitle", "BV1")
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.bvid, "BV1")

    def test_claim_complete_retry_fail(self) -> None:
        self.db.enqueue_once("fetch_subtitle", "BV_ok")
        self.db.enqueue_once("fetch_subtitle", "BV_retry")
        self.db.enqueue_once("fetch_subtitle", "BV_fail")

        j1 = self.db.claim_next_job()
        self.assertIsNotNone(j1)
        assert j1 is not None
        self.assertEqual(j1.bvid, "BV_ok")
        self.assertEqual(j1.status, "running")
        self.assertEqual(j1.attempts, 1)
        self.db.complete_job(j1.id)
        done = self.db.get_job("fetch_subtitle", "BV_ok")
        assert done is not None
        self.assertEqual(done.status, "done")

        j2 = self.db.claim_next_job()
        assert j2 is not None
        self.assertEqual(j2.bvid, "BV_retry")
        self.db.retry_job(j2.id, error="rate limited", delay_seconds=3600)
        retried = self.db.get_job("fetch_subtitle", "BV_retry")
        assert retried is not None
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.last_error, "rate limited")
        self.assertGreater(retried.run_after, time.time())
        # not claimable yet
        j3 = self.db.claim_next_job(kinds=["fetch_subtitle"])
        assert j3 is not None
        self.assertEqual(j3.bvid, "BV_fail")
        self.db.fail_job(j3.id, error="permanent")
        failed = self.db.get_job("fetch_subtitle", "BV_fail")
        assert failed is not None
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.last_error, "permanent")

        # only delayed retry remains pending but not ready
        none = self.db.claim_next_job()
        self.assertIsNone(none)

    def test_save_subtitle_each_status(self) -> None:
        for status, bvid in (
            ("ok", "BVok"),
            ("empty", "BVempty"),
            ("retry", "BVretry"),
            ("failed", "BVfail"),
        ):
            text = "hello" if status == "ok" else ""
            cues = '[{"content":"hello"}]' if status == "ok" else "[]"
            self.db.save_subtitle(
                SubtitlePayload(
                    bvid=bvid,
                    language="zh",
                    status=status,  # type: ignore[arg-type]
                    text=text,
                    cues_json=cues,
                    error="" if status in ("ok", "empty") else "err",
                )
            )
            row = self.db.get_subtitle(bvid, "zh")
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["status"], status)
            if status == "ok":
                self.assertEqual(row["text"], "hello")
                self.assertIn("hello", row["cues_json"])
            if status == "empty":
                self.assertGreater(float(row["retry_at"]), 0)
            if status == "failed":
                self.assertEqual(row["error"], "err")

        # update increments attempts
        self.db.save_subtitle(
            SubtitlePayload(bvid="BVok", language="zh", status="ok", text="x")
        )
        row = self.db.get_subtitle("BVok", "zh")
        assert row is not None
        self.assertEqual(int(row["attempts"]), 2)

    def test_status_snapshot(self) -> None:
        self.db.upsert_video(Video(bvid="BV1"))
        self.db.enqueue_once("fetch_subtitle", "BV1")
        snap = self.db.status_snapshot()
        self.assertEqual(snap["schema"], "ready")
        self.assertEqual(snap["videos"], 1)
        self.assertEqual(snap["jobs"]["pending"], 1)
        self.assertEqual(snap["jobs"]["total"], 1)
        self.assertEqual(snap["subtitles"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
