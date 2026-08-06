"""Preference domain types (immutable where practical)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Interest:
    """One interest facet (e.g. gamedev, llm)."""

    id: str
    weight: float
    keywords: tuple[str, ...]
    related: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Interest.id must be non-empty")
        if self.weight < 0:
            raise ValueError(f"Interest.weight must be >= 0, got {self.weight}")
        # normalize empties out
        object.__setattr__(
            self,
            "keywords",
            tuple(k.strip() for k in self.keywords if k and str(k).strip()),
        )
        object.__setattr__(
            self,
            "related",
            tuple(k.strip() for k in self.related if k and str(k).strip()),
        )
        if not self.keywords and not self.related:
            raise ValueError(f"Interest {self.id!r} needs keywords or related terms")


@dataclass(frozen=True, slots=True)
class PreferenceProfile:
    """Full user preference profile used by PreferenceScorer."""

    interests: tuple[Interest, ...]
    must_not: tuple[str, ...] = ()
    # minimum total score to enqueue subtitle fetch
    threshold: float = 0.35
    # related-term hit relative to a keyword hit inside one interest
    related_weight: float = 0.65
    # cap for soft n-gram contribution per interest (0 disables soft)
    soft_weight: float = 0.40
    # ignore ultra-short tokens for soft matching
    min_term_len: int = 2

    def __post_init__(self) -> None:
        if self.threshold < 0:
            raise ValueError("threshold must be >= 0")
        if not (0.0 <= self.related_weight <= 1.5):
            raise ValueError("related_weight should be in [0, 1.5]")
        if not (0.0 <= self.soft_weight <= 1.5):
            raise ValueError("soft_weight should be in [0, 1.5]")
        object.__setattr__(
            self,
            "must_not",
            tuple(x.strip() for x in self.must_not if x and str(x).strip()),
        )
        if not self.interests:
            raise ValueError("PreferenceProfile.interests must not be empty")


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Explainable score for one text / candidate."""

    score: float
    selected: bool
    blocked: bool
    matched_keywords: tuple[str, ...]
    matched_related: tuple[str, ...]
    soft_terms: tuple[str, ...]
    # (interest_id, contribution) sorted by contribution desc
    interest_hits: tuple[tuple[str, float], ...]
    block_terms: tuple[str, ...] = ()

    def summary(self) -> str:
        if self.blocked:
            return f"blocked by {','.join(self.block_terms) or 'must_not'}"
        parts = [f"score={self.score:.3f}", "pass" if self.selected else "skip"]
        if self.matched_keywords:
            parts.append("kw=" + "|".join(self.matched_keywords[:5]))
        if self.matched_related:
            parts.append("rel=" + "|".join(self.matched_related[:5]))
        if self.soft_terms:
            parts.append("soft=" + "|".join(self.soft_terms[:5]))
        return " ".join(parts)
