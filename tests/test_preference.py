"""High-signal unit tests for preference scoring foundation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loop_bilibili.database import Database
from loop_bilibili.ingest import refresh_source
from loop_bilibili.models import Candidate, Video
from loop_bilibili.preference.loader import load_preference_profile, profile_from_mapping
from loop_bilibili.preference.models import Interest, PreferenceProfile
from loop_bilibili.preference.scorer import PreferenceScorer
from loop_bilibili.preference.textnorm import (
    normalize_text,
    soft_term_coverage,
)


def _profile(**kwargs) -> PreferenceProfile:
    base = dict(
        interests=(
            Interest(
                id="llm",
                weight=1.0,
                keywords=("大模型", "LLM", "Agent"),
                related=("智能体", "RAG", "微调", "prompt"),
            ),
            Interest(
                id="gamedev",
                weight=1.0,
                keywords=("Godot", "Unity", "游戏开发"),
                related=("Shader", "虚幻", "UE5"),
            ),
            Interest(
                id="backend",
                weight=0.9,
                keywords=("Rust", "Go", "后端"),
                related=("并发", "微服务"),
            ),
        ),
        must_not=("带货", "抽奖"),
        threshold=0.35,
        related_weight=0.65,
        soft_weight=0.40,
    )
    base.update(kwargs)
    return PreferenceProfile(**base)


class TextNormTest(unittest.TestCase):
    def test_normalize_case_and_fullwidth(self) -> None:
        self.assertEqual(normalize_text("  Hello  LLM  "), "hello llm")
        self.assertIn("llm", normalize_text("全角ＬＬＭ测试"))

    def test_soft_coverage_cjk_near_form(self) -> None:
        # keyword 大模型 vs phrasing 大型语言模型
        hay = normalize_text("详解大型语言模型训练")
        cov = soft_term_coverage(hay, "大模型")
        self.assertGreaterEqual(cov, 0.5)


class ScorerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scorer = PreferenceScorer(_profile())

    def test_keyword_hit_selects(self) -> None:
        b = self.scorer.score_text("从零实现一个 LLM Agent 工作流")
        self.assertFalse(b.blocked)
        self.assertTrue(b.selected)
        self.assertGreaterEqual(b.score, 0.35)
        self.assertTrue(any("LLM" in k or "Agent" in k for k in b.matched_keywords))

    def test_related_only_can_select(self) -> None:
        # no core keyword, but related 微调
        b = self.scorer.score_text("LoRA 微调实战笔记")
        self.assertIn("微调", b.matched_related)
        self.assertTrue(b.selected)

    def test_soft_near_keyword(self) -> None:
        b = self.scorer.score_text("大型语言模型入门到精通")
        # should get soft credit toward 大模型 even without exact phrase
        self.assertGreater(b.score, 0.0)
        self.assertTrue(b.selected or b.soft_terms or b.matched_keywords)

    def test_unrelated_skipped(self) -> None:
        b = self.scorer.score_text("今日份美妆分享与逛街 vlog")
        self.assertFalse(b.selected)
        self.assertFalse(b.blocked)
        self.assertLess(b.score, 0.35)

    def test_latin_token_boundary_avoids_reaction_false_positive(self) -> None:
        # frontend interest keywords include React in repo profile; local profile has no React.
        # Ensure bare short tokens do not match as arbitrary substrings when present as words only.
        scorer = PreferenceScorer(
            PreferenceProfile(
                interests=(
                    Interest(
                        id="fe",
                        weight=1.0,
                        keywords=("React",),
                        related=(),
                    ),
                ),
                threshold=0.35,
            )
        )
        miss = scorer.score_text("TWS SODA SODA MV Reaction")
        self.assertFalse(miss.selected)
        hit = scorer.score_text("React 18 concurrent features")
        self.assertTrue(hit.selected)

    def test_must_not_blocks(self) -> None:
        b = self.scorer.score_text("Godot 游戏开发教程 关注抽奖")
        self.assertTrue(b.blocked)
        self.assertFalse(b.selected)
        self.assertEqual(b.score, 0.0)
        self.assertIn("抽奖", b.block_terms)

    def test_gamedev_keyword(self) -> None:
        b = self.scorer.score_text("Godot 4 Shader 入门")
        self.assertTrue(b.selected)
        self.assertTrue(
            "Godot" in b.matched_keywords or "Shader" in b.matched_related
        )

    def test_multi_interest_higher_than_single(self) -> None:
        single = self.scorer.score_text("Rust 并发编程")
        multi = self.scorer.score_text("Rust 写游戏后端 + Godot 客户端")
        self.assertTrue(single.selected)
        self.assertTrue(multi.selected)
        self.assertGreaterEqual(multi.score, single.score)

    def test_candidate_uses_title_and_reason(self) -> None:
        c = Candidate(
            video=Video(bvid="BV1", title="UE5 开放世界关卡设计"),
            reason="homepage",
            source="homepage",
        )
        b = self.scorer.score_candidate(c)
        self.assertTrue(b.selected)
        self.assertTrue(b.matched_related or b.matched_keywords)


class LoaderAndIngestTest(unittest.TestCase):
    def test_load_repo_preferences_toml(self) -> None:
        path = Path(__file__).resolve().parents[1] / "preferences.toml"
        if not path.is_file():
            self.skipTest("preferences.toml missing")
        profile = load_preference_profile(path)
        self.assertGreaterEqual(len(profile.interests), 5)
        scorer = PreferenceScorer(profile)
        b = scorer.score_text("用 Rust 写高并发后端服务")
        self.assertTrue(b.selected)

    def test_profile_from_mapping_validation(self) -> None:
        with self.assertRaises(ValueError):
            profile_from_mapping({"preference": {"interests": []}})

    def test_refresh_respects_scorer(self) -> None:
        scorer = PreferenceScorer(_profile(threshold=0.35))

        class FakeSource:
            name = "homepage"

            def fetch(self):
                return [
                    Candidate(
                        video=Video(bvid="BVgame", title="Godot 独立游戏开发日记"),
                        source="homepage",
                    ),
                    Candidate(
                        video=Video(bvid="BVvlog", title="周末探店吃吃喝喝"),
                        source="homepage",
                    ),
                    Candidate(
                        video=Video(bvid="BVads", title="Godot 插件 直播抽奖"),
                        source="homepage",
                    ),
                ]

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "t.db")
            db.init_schema()
            summary = refresh_source(FakeSource(), db, scorer=scorer)
            self.assertEqual(summary["candidates"], 3)
            self.assertEqual(summary["enqueued"], 1)
            self.assertEqual(summary["skipped"], 1)
            self.assertEqual(summary["blocked"], 1)
            self.assertIsNotNone(db.get_job("fetch_subtitle", "BVgame"))
            self.assertIsNone(db.get_job("fetch_subtitle", "BVvlog"))
            self.assertIsNone(db.get_job("fetch_subtitle", "BVads"))
            db.close()

    def test_refresh_without_scorer_enqueues_all(self) -> None:
        class FakeSource:
            name = "homepage"

            def fetch(self):
                return [
                    Candidate(video=Video(bvid="BV1", title="a"), source="homepage"),
                    Candidate(video=Video(bvid="BV2", title="b"), source="homepage"),
                ]

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "t.db")
            db.init_schema()
            summary = refresh_source(FakeSource(), db, scorer=None)
            self.assertEqual(summary["enqueued"], 2)
            db.close()


if __name__ == "__main__":
    unittest.main()
