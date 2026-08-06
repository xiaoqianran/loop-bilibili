"""Tests for durable multi-cycle cadence (refresh → score → enqueue → jobs)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loop_bilibili.cadence import run_cadence
from loop_bilibili.cli import main
from loop_bilibili.database import Database
from loop_bilibili.ingest import refresh_source
from loop_bilibili.models import Candidate, Video
from loop_bilibili.preference.models import Interest, PreferenceProfile
from loop_bilibili.preference.scorer import PreferenceScorer
from loop_bilibili.sources.subtitle_bilibili import SubtitleFetchResult
from loop_bilibili.worker import job_pace_sleep, risk_retry_delay


class FakeHomepage:
    def __init__(self, batches: list[list[Candidate]]):
        self.name = "homepage"
        self._batches = list(batches)
        self.calls = 0

    def fetch(self) -> list[Candidate]:
        self.calls += 1
        if not self._batches:
            return []
        # last batch repeats for further cycles
        if len(self._batches) == 1:
            return list(self._batches[0])
        return list(self._batches.pop(0))


class FakeSubs:
    def __init__(self, text_by: dict[str, str] | None = None):
        self.text_by = text_by or {}
        self.calls: list[str] = []

    def fetch(self, bvid: str) -> SubtitleFetchResult:
        self.calls.append(bvid)
        if bvid in self.text_by:
            return SubtitleFetchResult(
                bvid=bvid,
                status="ok",
                text=self.text_by[bvid],
                cues=[{"content": self.text_by[bvid]}],
                language="zh",
            )
        return SubtitleFetchResult(bvid=bvid, status="empty")


def _scorer() -> PreferenceScorer:
    return PreferenceScorer(
        PreferenceProfile(
            interests=(
                Interest(
                    id="llm",
                    weight=1.0,
                    keywords=("LLM", "大模型", "Agent"),
                    related=("RAG", "微调"),
                ),
            ),
            must_not=("抽奖", "带货"),
            threshold=0.35,
            related_weight=0.65,
            soft_weight=0.4,
        )
    )


class CadenceTest(unittest.TestCase):
    def test_two_cycles_dedupe_and_selective_enqueue(self) -> None:
        """Same feed twice: videos persist, subtitle job at most once."""
        cands = [
            Candidate(
                video=Video(bvid="BVllm1", title="LLM Agent 入门", owner_name="u"),
                source="homepage",
            ),
            Candidate(
                video=Video(bvid="BVvlog", title="周末逛街 vlog", owner_name="v"),
                source="homepage",
            ),
            Candidate(
                video=Video(bvid="BVads", title="LLM 课程 直播抽奖", owner_name="a"),
                source="homepage",
            ),
        ]
        src = FakeHomepage([cands, cands])
        subs = FakeSubs({"BVllm1": "hello agent"})
        sleeps: list[float] = []

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "c.db")
            db.init_schema()
            stats = run_cadence(
                db,
                [src],
                subs,
                scorer=_scorer(),
                prefer_enabled=True,
                homepage_interval_s=0.0,
                poll_interval=0.0,
                jobs_per_cycle=10,
                max_cycles=2,
                pace=False,
                sleep=lambda s: sleeps.append(s),
            )
            self.assertEqual(stats["cycles"], 2)
            self.assertEqual(stats["candidates"], 6)  # 3 per cycle
            # only BVllm1 selected once
            self.assertEqual(stats["enqueued"], 1)
            self.assertEqual(stats["skipped"], 2)  # vlog each cycle? wait
            # cycle1: skip 1 vlog, block 1 ads, enqueue 1
            # cycle2: same decisions but enqueue_once → enqueued=0
            # totals: enqueued=1, skipped=2 (1+1), blocked=2 (1+1)
            self.assertEqual(stats["skipped"], 2)
            self.assertEqual(stats["blocked"], 2)
            self.assertEqual(db.count_videos(), 3)
            job = db.get_job("fetch_subtitle", "BVllm1")
            self.assertIsNotNone(job)
            # only one job row for subtitle
            self.assertEqual(db.count_jobs(), 2)  # fetch_subtitle + analyze after ok
            self.assertEqual(stats["ok"], 1)
            self.assertGreaterEqual(stats["processed"], 1)
            # no second subtitle fetch for same bvid
            self.assertEqual(subs.calls.count("BVllm1"), 1)
            self.assertIsNone(db.get_job("fetch_subtitle", "BVvlog"))
            self.assertIsNone(db.get_job("fetch_subtitle", "BVads"))
            # both cycles recorded
            self.assertEqual(len(stats["cycle_summaries"]), 2)
            self.assertEqual(stats["cycle_summaries"][0]["enqueued"], 1)
            self.assertEqual(stats["cycle_summaries"][1]["enqueued"], 0)
            db.close()

    def test_dual_process_launch_consistent(self) -> None:
        """Two separate run_cadence launches on same DB: stable metrics."""
        cands = [
            Candidate(
                video=Video(bvid="BVrag", title="RAG 微调实战"),
                source="homepage",
            ),
            Candidate(
                video=Video(bvid="BVfood", title="探店吃吃喝喝"),
                source="homepage",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dual.db"

            def launch() -> dict:
                db = Database(path)
                db.init_schema()
                src = FakeHomepage([cands])
                subs = FakeSubs({"BVrag": "rag text"})
                stats = run_cadence(
                    db,
                    [src],
                    subs,
                    scorer=_scorer(),
                    homepage_interval_s=0.0,
                    poll_interval=0.0,
                    jobs_per_cycle=5,
                    max_cycles=1,
                    pace=False,
                    sleep=lambda _s: None,
                )
                snap = db.status_snapshot()
                db.close()
                return {"stats": stats, "snap": snap}

            r1 = launch()
            r2 = launch()
            self.assertEqual(r1["stats"]["cycles"], 1)
            self.assertEqual(r2["stats"]["cycles"], 1)
            self.assertEqual(r1["stats"]["candidates"], 2)
            self.assertEqual(r2["stats"]["candidates"], 2)
            # first launch enqueues; second sees same feed but enqueue_once → 0
            self.assertEqual(r1["stats"]["enqueued"], 1)
            self.assertEqual(r2["stats"]["enqueued"], 0)
            self.assertEqual(r1["snap"]["videos"], 2)
            self.assertEqual(r2["snap"]["videos"], 2)
            # jobs already done on second launch
            self.assertEqual(r2["snap"]["jobs"]["pending"], 0)

    def test_refresh_same_bvid_single_job(self) -> None:
        c = Candidate(
            video=Video(bvid="BVonce", title="LLM 教程"),
            source="homepage",
        )

        class S:
            name = "homepage"

            def fetch(self):
                return [c]

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "d.db")
            db.init_schema()
            scorer = _scorer()
            s1 = refresh_source(S(), db, scorer=scorer)
            s2 = refresh_source(S(), db, scorer=scorer)
            self.assertEqual(s1["enqueued"], 1)
            self.assertEqual(s2["enqueued"], 0)
            self.assertEqual(s1["candidates"], 1)
            self.assertEqual(s2["candidates"], 1)
            self.assertEqual(db.count_videos(), 1)
            self.assertEqual(db.count_jobs("pending") + db.count_jobs("done"), 1)
            db.close()


class PaceHelpersTest(unittest.TestCase):
    def test_risk_backoff_grows_and_caps(self) -> None:
        d1 = risk_retry_delay(1, base=10.0, maximum=100.0)
        d2 = risk_retry_delay(2, base=10.0, maximum=100.0)
        d3 = risk_retry_delay(3, base=10.0, maximum=100.0)
        d_big = risk_retry_delay(20, base=10.0, maximum=100.0)
        self.assertGreaterEqual(d1, 10.0)
        self.assertGreaterEqual(d2, d1 - 3.0)  # jitter noise
        self.assertGreaterEqual(d3, 20.0)
        self.assertLessEqual(d_big, 100.0 + 3.0)
        self.assertGreaterEqual(d_big, 100.0)

    def test_job_pace_sleep_non_negative(self) -> None:
        slept = job_pace_sleep(0.0, 0.0)
        self.assertEqual(slept, 0.0)
        slept2 = job_pace_sleep(0.02, 0.0)
        self.assertGreaterEqual(slept2, 0.0)


class CliRunOfflineTest(unittest.TestCase):
    def test_cli_run_max_cycles_with_fakes(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "loop.db"
            pref = root / "preferences.toml"
            pref.write_text(
                """
[preference]
threshold = 0.35
related_weight = 0.65
soft_weight = 0.4
must_not = ["抽奖"]

[[preference.interests]]
id = "llm"
weight = 1.0
keywords = ["LLM", "大模型"]
related = ["RAG"]
""",
                encoding="utf-8",
            )
            cfg = root / "config.toml"
            cfg.write_text(
                f"""
[database]
path = "{db_path}"

[sources]
homepage_enabled = true
homepage_pages = 1
homepage_ps = 12
homepage_interval_s = 0

[worker]
poll_interval = 0.0
job_delay = 0.0
job_jitter = 0.0
jobs_per_cycle = 10

[runtime]
cookie = ""
require_cookie = false

[preference]
enabled = true
path = "{pref}"
""",
                encoding="utf-8",
            )

            cands = [
                Candidate(
                    video=Video(bvid="BVcli1", title="大模型 RAG 系统"),
                    source="homepage",
                ),
                Candidate(
                    video=Video(bvid="BVcli2", title="今日美妆"),
                    source="homepage",
                ),
            ]

            class HP:
                name = "homepage"

                def fetch(self):
                    return cands

            class Sub:
                def fetch(self, bvid: str) -> SubtitleFetchResult:
                    return SubtitleFetchResult(
                        bvid=bvid, status="ok", text="t", cues=[{"content": "t"}]
                    )

            argv = [
                "--config",
                str(cfg),
                "--db",
                str(db_path),
                "run",
                "--max-cycles",
                "2",
                "--homepage-interval",
                "0",
                "--jobs-per-cycle",
                "10",
            ]
            with mock.patch("loop_bilibili.cli.HomepageRcmdSource", return_value=HP()), mock.patch(
                "loop_bilibili.cli.BilibiliSubtitleSource", return_value=Sub()
            ):
                rc1 = main(argv)
                rc2 = main(argv)
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)

            db = Database(db_path)
            snap = db.status_snapshot()
            self.assertEqual(snap["videos"], 2)
            # only selected got subtitle
            sub = db.get_subtitle("BVcli1", "zh")
            self.assertIsNotNone(sub)
            self.assertEqual(sub["status"], "ok")
            self.assertIsNone(db.get_job("fetch_subtitle", "BVcli2"))
            db.close()


if __name__ == "__main__":
    unittest.main()
