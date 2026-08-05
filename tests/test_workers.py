"""Tests for refresh_source + subtitle worker with fake sources."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loop_bilibili.database import Database
from loop_bilibili.ingest import refresh_source
from loop_bilibili.models import Candidate, Video
from loop_bilibili.sources.subtitle_bilibili import SubtitleFetchResult
from loop_bilibili.worker import process_subtitle_job, run_once, risk_retry_delay


class FakeVideoSource:
    def __init__(self, name: str, candidates: list[Candidate]):
        self.name = name
        self._candidates = candidates

    def fetch(self) -> list[Candidate]:
        return list(self._candidates)


class FakeSubtitleSource:
    def __init__(self, outcomes: dict[str, SubtitleFetchResult]):
        self.outcomes = outcomes
        self.calls: list[str] = []

    def fetch(self, bvid: str) -> SubtitleFetchResult:
        self.calls.append(bvid)
        if bvid in self.outcomes:
            return self.outcomes[bvid]
        return SubtitleFetchResult(bvid=bvid, status="failed", error="missing fixture")


class RefreshAndWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "w.db")
        self.db.init_schema()

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_refresh_upserts_discoveries_and_enqueues_once(self) -> None:
        cands = [
            Candidate(
                video=Video(bvid="BV1aa", title="a", owner_mid="1", owner_name="u"),
                reason="r0",
                source="homepage",
            ),
            Candidate(
                video=Video(bvid="BV1bb", title="b", owner_mid="2", owner_name="v"),
                reason="r1",
                source="homepage",
            ),
        ]
        src = FakeVideoSource("homepage", cands)
        summary = refresh_source(src, self.db)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["enqueued"], 2)
        self.assertEqual(self.db.count_videos(), 2)
        self.assertEqual(self.db.count_jobs("pending"), 2)
        disc = self.db.list_discoveries(summary["run_id"])
        self.assertEqual([d["bvid"] for d in disc], ["BV1aa", "BV1bb"])
        self.assertEqual(disc[0]["position"], 0)
        self.assertEqual(disc[0]["reason"], "r0")

        # second refresh: discoveries for new run, jobs still once
        summary2 = refresh_source(src, self.db)
        self.assertEqual(summary2["enqueued"], 0)
        self.assertEqual(self.db.count_jobs(), 2)
        self.assertEqual(self.db.count_runs("ok"), 2)

    def test_worker_ok_enqueues_analyze(self) -> None:
        self.db.upsert_video(Video(bvid="BVok"))
        self.db.enqueue_once("fetch_subtitle", "BVok")
        sub = FakeSubtitleSource(
            {
                "BVok": SubtitleFetchResult(
                    bvid="BVok",
                    status="ok",
                    text="hello",
                    cues=[{"content": "hello"}],
                    language="zh",
                )
            }
        )
        stats = run_once(self.db, sub, max_jobs=1)
        self.assertEqual(stats["ok"], 1)
        row = self.db.get_subtitle("BVok", "zh")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["text"], "hello")
        job = self.db.get_job("fetch_subtitle", "BVok")
        assert job is not None
        self.assertEqual(job.status, "done")
        analyze = self.db.get_job("analyze", "BVok")
        self.assertIsNotNone(analyze)
        assert analyze is not None
        self.assertEqual(analyze.status, "pending")

        # process analyze stub
        stats2 = run_once(self.db, sub, max_jobs=1)
        self.assertEqual(stats2["analyze"], 1)
        analyze2 = self.db.get_job("analyze", "BVok")
        assert analyze2 is not None
        self.assertEqual(analyze2.status, "done")

    def test_worker_empty_retry_failed(self) -> None:
        for bvid in ("BVempty", "BVretry", "BVfail"):
            self.db.upsert_video(Video(bvid=bvid))
            self.db.enqueue_once("fetch_subtitle", bvid)

        sub = FakeSubtitleSource(
            {
                "BVempty": SubtitleFetchResult(bvid="BVempty", status="empty"),
                "BVretry": SubtitleFetchResult(
                    bvid="BVretry", status="retry", error="429"
                ),
                "BVfail": SubtitleFetchResult(
                    bvid="BVfail", status="failed", error="gone"
                ),
            }
        )
        # process in order of enqueue
        for expected in ("empty", "retry", "failed"):
            job = self.db.claim_next_job(kinds=["fetch_subtitle"])
            self.assertIsNotNone(job)
            assert job is not None
            outcome = process_subtitle_job(job, self.db, sub)
            self.assertEqual(outcome, expected)

        empty_job = self.db.get_job("fetch_subtitle", "BVempty")
        assert empty_job is not None
        self.assertEqual(empty_job.status, "done")
        self.assertIsNone(self.db.get_job("analyze", "BVempty"))

        retry_job = self.db.get_job("fetch_subtitle", "BVretry")
        assert retry_job is not None
        self.assertEqual(retry_job.status, "pending")
        self.assertIn("429", retry_job.last_error)

        fail_job = self.db.get_job("fetch_subtitle", "BVfail")
        assert fail_job is not None
        self.assertEqual(fail_job.status, "failed")

        self.assertEqual(self.db.get_subtitle("BVempty", "zh")["status"], "empty")
        self.assertEqual(self.db.get_subtitle("BVretry", "zh")["status"], "retry")
        self.assertEqual(self.db.get_subtitle("BVfail", "zh")["status"], "failed")

    def test_risk_error_uses_long_backoff(self) -> None:
        self.db.upsert_video(Video(bvid="BVrisk"))
        self.db.enqueue_once("fetch_subtitle", "BVrisk")
        job = self.db.claim_next_job()
        assert job is not None
        sub = FakeSubtitleSource(
            {
                "BVrisk": SubtitleFetchResult(
                    bvid="BVrisk",
                    status="retry",
                    error="view/detail code=-352 风控校验失败",
                )
            }
        )
        outcome = process_subtitle_job(
            job,
            self.db,
            sub,
            risk_base_delay=15.0,
            risk_max_delay=300.0,
            retry_delay=5.0,
        )
        self.assertEqual(outcome, "retry")
        retried = self.db.get_job("fetch_subtitle", "BVrisk")
        assert retried is not None
        self.assertEqual(retried.status, "pending")
        # run_after should be well above "now" by ~15s risk base (not 5s generic)
        import time

        remaining = retried.run_after - time.time()
        self.assertGreaterEqual(remaining, 14.0)

    def test_run_once_paces_between_jobs(self) -> None:
        for b in ("BVa", "BVb"):
            self.db.upsert_video(Video(bvid=b))
            self.db.enqueue_once("fetch_subtitle", b)
        sub = FakeSubtitleSource(
            {
                "BVa": SubtitleFetchResult(bvid="BVa", status="empty"),
                "BVb": SubtitleFetchResult(bvid="BVb", status="empty"),
            }
        )
        stats = run_once(
            self.db,
            sub,
            max_jobs=2,
            job_delay=0.05,
            job_jitter=0.0,
            pace=True,
        )
        self.assertEqual(stats["empty"], 2)
        self.assertGreaterEqual(stats["slept_s"], 0.04)


if __name__ == "__main__":
    unittest.main()
