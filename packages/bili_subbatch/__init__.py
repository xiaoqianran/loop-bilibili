"""SubBatch-protocol Bilibili subtitle client (vendored into loop-bilibili)."""

from .client import SubtitleResult, fetch_subtitle
from .util import extract_bvid, pick_track, to_cues

__version__ = "0.2.0"
__all__ = [
    "SubtitleResult",
    "extract_bvid",
    "fetch_subtitle",
    "pick_track",
    "to_cues",
    "__version__",
]
