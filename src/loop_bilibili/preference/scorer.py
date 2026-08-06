"""
PreferenceScorer: keyword + related + soft n-gram affinity.

Design notes
------------
* Not pure exact keywords: each Interest has `keywords` (core) and `related`
  (aliases / neighboring concepts). Related hits count less than keywords.
* Soft matching uses character bigrams for CJK so near-phrasings still score
  without a neural model. Soft contribution is capped per interest.
* must_not is a hard block (selected=False, score=0).
* Final score is a diminishing sum of per-interest contributions so one topic
  cannot explode the score unboundedly, while multi-topic videos still rise.

This layer is intentionally local and dependency-free (stdlib only).
"""

from __future__ import annotations

from typing import Iterable

from loop_bilibili.models import Candidate

from .models import Interest, PreferenceProfile, ScoreBreakdown
from .textnorm import contains_phrase, normalize_text, soft_term_coverage


def _diminishing_sum(values: Iterable[float], *, decay: float = 0.55) -> float:
    """
    Sort contributions desc and sum v_i * decay^i.

    First hit full, second ~55%, third ~30%, …
    """
    ordered = sorted((float(v) for v in values if v > 0), reverse=True)
    total = 0.0
    factor = 1.0
    for v in ordered:
        total += v * factor
        factor *= decay
    return total


class PreferenceScorer:
    """Score free text or Candidate titles against a PreferenceProfile."""

    def __init__(self, profile: PreferenceProfile):
        self.profile = profile

    def score_text(self, text: str) -> ScoreBreakdown:
        hay = normalize_text(text)
        if not hay:
            return ScoreBreakdown(
                score=0.0,
                selected=False,
                blocked=False,
                matched_keywords=(),
                matched_related=(),
                soft_terms=(),
                interest_hits=(),
            )

        blocked_by = [
            t
            for t in self.profile.must_not
            if contains_phrase(hay, normalize_text(t))
        ]
        if blocked_by:
            return ScoreBreakdown(
                score=0.0,
                selected=False,
                blocked=True,
                matched_keywords=(),
                matched_related=(),
                soft_terms=(),
                interest_hits=(),
                block_terms=tuple(blocked_by),
            )

        kw_hits: list[str] = []
        rel_hits: list[str] = []
        soft_hits: list[str] = []
        interest_parts: list[tuple[str, float]] = []

        for interest in self.profile.interests:
            contrib, kws, rels, softs = self._score_interest(hay, interest)
            if contrib > 0:
                interest_parts.append((interest.id, contrib))
            kw_hits.extend(kws)
            rel_hits.extend(rels)
            soft_hits.extend(softs)

        # global diminishing across interests
        raw = _diminishing_sum(c for _, c in interest_parts)
        # keep a stable, rounded score for logs / tests
        score = round(min(raw, 10.0), 4)
        selected = score >= self.profile.threshold and not blocked_by
        interest_parts.sort(key=lambda x: x[1], reverse=True)

        return ScoreBreakdown(
            score=score,
            selected=selected,
            blocked=False,
            matched_keywords=tuple(dict.fromkeys(kw_hits)),
            matched_related=tuple(dict.fromkeys(rel_hits)),
            soft_terms=tuple(dict.fromkeys(soft_hits)),
            interest_hits=tuple(interest_parts),
        )

    def score_candidate(self, candidate: Candidate) -> ScoreBreakdown:
        """Score title + owner + discovery reason (cheap metadata only)."""
        v = candidate.video
        blob = " ".join(
            x
            for x in (
                v.title,
                v.owner_name,
                candidate.reason,
            )
            if x
        )
        return self.score_text(blob)

    def accept(self, candidate: Candidate) -> bool:
        return self.score_candidate(candidate).selected

    def _score_interest(
        self, hay: str, interest: Interest
    ) -> tuple[float, list[str], list[str], list[str]]:
        """
        Return (contribution, matched_kw, matched_rel, soft_terms).

        contribution ∈ [0, interest.weight * (1 + soft_weight)] roughly.
        """
        profile = self.profile
        kw_matched: list[str] = []
        rel_matched: list[str] = []
        soft_matched: list[str] = []

        best_core = 0.0  # 1.0 keyword, related_weight for related

        for term in interest.keywords:
            nt = normalize_text(term)
            if len(nt) < profile.min_term_len:
                continue
            if contains_phrase(hay, nt):
                kw_matched.append(term)
                best_core = max(best_core, 1.0)
            elif profile.soft_weight > 0:
                cov = soft_term_coverage(hay, term, min_len=profile.min_term_len)
                if cov >= 0.5:
                    soft_matched.append(term)
                    # soft never exceeds related_weight for keywords
                    best_core = max(best_core, profile.related_weight * cov)

        for term in interest.related:
            nt = normalize_text(term)
            if len(nt) < profile.min_term_len:
                continue
            if contains_phrase(hay, nt):
                rel_matched.append(term)
                best_core = max(best_core, profile.related_weight)
            elif profile.soft_weight > 0 and best_core < profile.related_weight:
                cov = soft_term_coverage(hay, term, min_len=profile.min_term_len)
                if cov >= 0.6:
                    soft_matched.append(term)
                    best_core = max(
                        best_core, profile.related_weight * cov * 0.85
                    )

        if best_core <= 0:
            return 0.0, kw_matched, rel_matched, soft_matched

        # multi-hit bonus within interest: more distinct matches → slight lift
        distinct = len(set(kw_matched) | set(rel_matched))
        multi = 1.0 + min(0.25, 0.05 * max(0, distinct - 1))

        soft_bonus = 0.0
        if soft_matched and profile.soft_weight > 0:
            # average soft coverage approximated by count cap
            soft_bonus = min(
                profile.soft_weight,
                0.1 * len(soft_matched),
            )

        contrib = interest.weight * (best_core * multi + soft_bonus)
        return contrib, kw_matched, rel_matched, soft_matched
