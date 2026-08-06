"""Tests for Mermaid extract / sanitize / AI analyze wiring."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop_bilibili.ai_client import AiClientError, chat_completion
from loop_bilibili.ai_mermaid import (
    build_messages,
    extract_mermaid_diagrams,
    format_cues_for_ai,
    sanitize_mermaid_in_markdown,
    strip_mermaid_timestamp_citations,
    truncate_subtitle,
)
from loop_bilibili.ai_worker import analyze_with_llm, build_analysis_from_markdown
from loop_bilibili.database import Database
from loop_bilibili.models import AiConfig, Job, SubtitlePayload, Video
from loop_bilibili.worker import process_analyze_job, run_once
from loop_bilibili.sources.subtitle_bilibili import SubtitleFetchResult


SAMPLE_MD = """# 学习 Python 函数

## 知识总览

```mermaid
flowchart TD
  A1["函数定义"] --> A2["参数 [BV1xx P1 01:23]"]
  A2 --> A3["返回值"]
```

## 调用流程

```mermaid
flowchart LR
  B1["调用"] --> B2["执行体"]
  B2 --> B3["返回"]
```
"""


class MermaidPureTest(unittest.TestCase):
    def test_strip_timestamps_in_nodes(self) -> None:
        code = 'A1["参数 [BV1xx P1 01:23]"] --> A2["结论"]'
        out = strip_mermaid_timestamp_citations(code)
        self.assertNotIn("BV1xx", out)
        self.assertNotIn("01:23", out)
        self.assertIn("参数", out)

    def test_extract_diagrams_and_titles(self) -> None:
        diagrams = extract_mermaid_diagrams(SAMPLE_MD)
        self.assertEqual(len(diagrams), 2)
        self.assertEqual(diagrams[0]["title"], "知识总览")
        self.assertEqual(diagrams[1]["title"], "调用流程")
        self.assertIn("flowchart TD", diagrams[0]["code"])
        # timestamps stripped from mermaid code
        self.assertNotIn("01:23", diagrams[0]["code"])

    def test_sanitize_leaves_body_timestamps(self) -> None:
        md = "正文引用 [BV1xx P1 00:10]\n\n```mermaid\nA[x [P1 00:10]]\n```"
        out = sanitize_mermaid_in_markdown(md)
        self.assertIn("[BV1xx P1 00:10]", out)
        self.assertNotIn("P1 00:10]]", out.split("```mermaid")[1])

    def test_truncate_subtitle(self) -> None:
        big = "行\n" * 50_000
        cut = truncate_subtitle(big, max_chars=5000)
        self.assertTrue(cut["truncated"])
        self.assertLess(len(cut["text"]), len(big))
        self.assertIn("中段采样", cut["text"])

    def test_format_cues(self) -> None:
        text = format_cues_for_ai(
            [
                {"content": "你好", "from_sec": 65},
                {"content": "你好", "from_sec": 66},  # dedupe consecutive
                {"content": "世界", "from_sec": 70},
            ],
            bvid="BV1TEST",
            page=2,
        )
        self.assertIn("[BV1TEST P2 01:05] 你好", text)
        self.assertIn("世界", text)
        self.assertEqual(text.count("你好"), 1)

    def test_build_messages_contains_mermaid_mode(self) -> None:
        msgs = build_messages(
            title="t", bvid="BV1", author="up", subtitle="hello world"
        )
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertIn("Mermaid", msgs[1]["content"])
        self.assertIn("hello world", msgs[1]["content"])


class ClientRetryTest(unittest.TestCase):
    def test_retries_on_retryable_then_ok(self) -> None:
        calls = {"n": 0}

        def fake_once(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise AiClientError("timeout after 1s", retryable=True)
            from loop_bilibili.ai_client import ChatResult

            return ChatResult(content="ok", model="m")

        with patch("loop_bilibili.ai_client._once", side_effect=fake_once), patch(
            "loop_bilibili.ai_client.time.sleep"
        ):
            r = chat_completion(
                base_url="https://example.invalid/v1",
                api_key="k",
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                retries=2,
                retry_backoff_s=0.01,
            )
        self.assertEqual(r.content, "ok")
        self.assertEqual(calls["n"], 3)

    def test_no_retry_on_auth_error(self) -> None:
        calls = {"n": 0}

        def fake_once(**kwargs):
            calls["n"] += 1
            raise AiClientError("HTTP 403: Authorization failed", retryable=False, status=403)

        with patch("loop_bilibili.ai_client._once", side_effect=fake_once), patch(
            "loop_bilibili.ai_client.time.sleep"
        ):
            with self.assertRaises(AiClientError):
                chat_completion(
                    base_url="https://example.invalid/v1",
                    api_key="k",
                    model="m",
                    messages=[{"role": "user", "content": "hi"}],
                    retries=3,
                )
        self.assertEqual(calls["n"], 1)


class AnalyzeWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "a.db")
        self.db.init_schema()
        self.db.upsert_video(Video(bvid="BV1mmd", title="图解", owner_name="测试UP"))
        self.db.save_subtitle(
            SubtitlePayload(
                bvid="BV1mmd",
                language="zh",
                status="ok",
                text="第一步做 A。第二步做 B。最后复盘。",
                cues_json=json.dumps(
                    [
                        {"content": "第一步做 A", "from_sec": 1},
                        {"content": "第二步做 B", "from_sec": 10},
                        {"content": "最后复盘", "from_sec": 20},
                    ],
                    ensure_ascii=False,
                ),
            )
        )
        self.db.enqueue_once("analyze", "BV1mmd", model="openai/gpt-oss-120b")
        job = self.db.claim_next_job(kinds=["analyze"])
        assert job is not None
        self.job = job

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_build_analysis_from_markdown(self) -> None:
        payload = build_analysis_from_markdown(
            bvid="BV1mmd", markdown=SAMPLE_MD, model="test-model"
        )
        self.assertEqual(payload.status, "ok")
        diagrams = json.loads(payload.diagrams_json)
        self.assertEqual(len(diagrams), 2)

    def test_analyze_with_mocked_llm(self) -> None:
        ai = AiConfig(
            enabled=True,
            base_url="https://example.invalid/v1",
            api_key="sk-test",
            models=["openai/gpt-oss-120b"],
            mode="mermaid",
        )

        class FakeResult:
            content = SAMPLE_MD
            model = "openai/gpt-oss-120b"

        with patch(
            "loop_bilibili.ai_worker.chat_completion", return_value=FakeResult()
        ) as mock_chat:
            outcome = analyze_with_llm(self.job, self.db, ai)
            self.assertEqual(outcome, "ok")
            mock_chat.assert_called_once()

        row = self.db.get_analysis("BV1mmd", model="openai/gpt-oss-120b")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "ok")
        diagrams = json.loads(row["diagrams_json"])
        self.assertGreaterEqual(len(diagrams), 2)
        job2 = self.db.get_job("analyze", "BV1mmd", model="openai/gpt-oss-120b")
        assert job2 is not None
        self.assertEqual(job2.status, "done")

    def test_job_requeues_on_timeout(self) -> None:
        ai = AiConfig(
            enabled=True,
            base_url="https://example.invalid/v1",
            api_key="sk",
            models=["m"],
            max_attempts=3,
            job_retry_delay_s=30.0,
        )
        with patch(
            "loop_bilibili.ai_worker.chat_completion",
            side_effect=AiClientError("timeout after 10s", retryable=True),
        ):
            outcome = analyze_with_llm(self.job, self.db, ai)
        self.assertEqual(outcome, "failed")
        job2 = self.db.get_job("analyze", "BV1mmd", model="openai/gpt-oss-120b")
        assert job2 is not None
        self.assertEqual(job2.status, "pending")  # requeued
        self.assertIn("timeout", job2.last_error)

    def test_stub_when_ai_disabled(self) -> None:
        outcome = process_analyze_job(self.job, self.db, ai=None)
        self.assertEqual(outcome, "ok")
        self.assertIsNone(self.db.get_analysis("BV1mmd"))

    def test_run_once_ok_then_analyze_with_ai(self) -> None:
        # fresh db path
        db = Database(Path(self._tmp.name) / "b.db")
        db.init_schema()
        db.upsert_video(Video(bvid="BVok2"))
        db.enqueue_once("fetch_subtitle", "BVok2")

        class FakeSub:
            def fetch(self, bvid: str) -> SubtitleFetchResult:
                return SubtitleFetchResult(
                    bvid=bvid,
                    status="ok",
                    text="hello",
                    cues=[{"content": "hello", "from_sec": 0}],
                    language="zh",
                )

        class FakeResult:
            content = SAMPLE_MD
            model = "m"

        ai = AiConfig(
            enabled=True,
            base_url="https://example.invalid/v1",
            api_key="sk",
            models=["m"],
        )
        with patch(
            "loop_bilibili.ai_worker.chat_completion", return_value=FakeResult()
        ):
            stats1 = run_once(db, FakeSub(), max_jobs=1, ai=ai, pace=False)
            self.assertEqual(stats1["ok"], 1)
            stats2 = run_once(db, FakeSub(), max_jobs=1, ai=ai, pace=False)
            self.assertEqual(stats2["analyze"], 1)
        analysis = db.get_analysis("BVok2")
        self.assertIsNotNone(analysis)
        assert analysis is not None
        self.assertEqual(analysis["status"], "ok")
        db.close()


class SiteBuilderMermaidTest(unittest.TestCase):
    def test_build_site_exports_diagrams(self) -> None:
        import sys
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "scripts" / "build_subtitle_site.py"
        spec = importlib.util.spec_from_file_location("build_subtitle_site", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["build_subtitle_site"] = mod
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            db_path = tdp / "demo.db"
            db = Database(db_path)
            db.init_schema()
            db.upsert_video(Video(bvid="BV1site", title="站点测试", owner_name="UP"))
            db.save_subtitle(
                SubtitlePayload(
                    bvid="BV1site",
                    language="zh",
                    status="ok",
                    text="字幕内容",
                    cues_json="[]",
                )
            )
            payload = build_analysis_from_markdown(
                bvid="BV1site", markdown=SAMPLE_MD, model="m"
            )
            db.save_analysis(payload)
            db.close()

            out = tdp / "site"
            rc = mod.main(
                [
                    "--db",
                    str(db_path),
                    "--slug",
                    "demo",
                    "--title",
                    "Demo",
                    "--out",
                    str(out),
                    "--base-url",
                    "",
                ]
            )
            self.assertEqual(rc, 0)
            video_json = json.loads(
                (out / "data" / "demo" / "v" / "BV1site.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(video_json["diagram_count"], 2)
            self.assertEqual(len(video_json["diagrams"]), 2)
            js = (out / "assets" / "app.js").read_text(encoding="utf-8")
            self.assertIn("ensureMermaid", js)
            self.assertIn("renderDiagramCard", js)
            self.assertIn("重绘", js)
            self.assertIn("_bsbRetry", js)
            self.assertIn("适宽", js)
            self.assertIn("buildModelBar", js)
            self.assertIn("getGlobalModel", js)
            meta = json.loads(
                (out / "data" / "demo" / "meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(meta["mermaid"], 1)



class DualModelTest(unittest.TestCase):
    def test_enqueue_two_models(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Database(Path(td) / "d.db")
            db.init_schema()
            db.upsert_video(Video(bvid="BVdual"))
            db.save_subtitle(
                SubtitlePayload(
                    bvid="BVdual", language="zh", status="ok",
                    text="a", cues_json="[]",
                )
            )
            from loop_bilibili.ai_worker import enqueue_analyze_for_models
            ai = AiConfig(
                enabled=True,
                base_url="https://x",
                api_key="k",
                models=[
                    "google/diffusiongemma-26b-a4b-it",
                    "openai/gpt-oss-120b",
                ],
            )
            n = enqueue_analyze_for_models(db, "BVdual", ai)
            self.assertEqual(n, 2)
            j1 = db.get_job("analyze", "BVdual", model="google/diffusiongemma-26b-a4b-it")
            j2 = db.get_job("analyze", "BVdual", model="openai/gpt-oss-120b")
            self.assertIsNotNone(j1)
            self.assertIsNotNone(j2)
            # save two analyses
            for mid, title in [
                ("google/diffusiongemma-26b-a4b-it", "Gemma图"),
                ("openai/gpt-oss-120b", "GPT图"),
            ]:
                md = f"## {title}\n\n```mermaid\nflowchart TD\n  A[\"x\"] --> B[\"y\"]\n```"
                payload = build_analysis_from_markdown(bvid="BVdual", markdown=md, model=mid)
                db.save_analysis(payload)
            a1 = db.get_analysis("BVdual", model="google/diffusiongemma-26b-a4b-it")
            a2 = db.get_analysis("BVdual", model="openai/gpt-oss-120b")
            self.assertEqual(a1["status"], "ok")
            self.assertEqual(a2["status"], "ok")
            self.assertEqual(len(db.list_analyses_for_bvid("BVdual")), 2)
            db.close()

    def test_default_models_order(self) -> None:
        from loop_bilibili.models import DEFAULT_AI_MODELS
        self.assertEqual(DEFAULT_AI_MODELS[0], "google/diffusiongemma-26b-a4b-it")
        self.assertIn("openai/gpt-oss-120b", DEFAULT_AI_MODELS)


if __name__ == "__main__":
    unittest.main()
