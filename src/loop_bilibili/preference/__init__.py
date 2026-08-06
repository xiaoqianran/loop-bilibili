"""User preference scoring: keywords + related terms + soft n-gram affinity."""

from .loader import load_preference_profile
from .models import Interest, PreferenceProfile, ScoreBreakdown
from .scorer import PreferenceScorer

__all__ = [
    "Interest",
    "PreferenceProfile",
    "PreferenceScorer",
    "ScoreBreakdown",
    "load_preference_profile",
]
