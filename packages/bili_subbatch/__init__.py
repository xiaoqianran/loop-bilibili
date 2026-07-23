"""SubBatch-protocol Bilibili subtitle client + extensible pipeline."""

from .client import BiliClient, SubtitleResult, fetch_subtitle
from .models import BatchConfig, BatchStats
from .pipeline import SubtitlePipeline, run_batch
from .util import extract_bvid, pick_track, to_cues

__version__ = "0.3.0"
__all__ = [
    "BiliClient",
    "BatchConfig",
    "BatchStats",
    "SubtitlePipeline",
    "SubtitleResult",
    "extract_bvid",
    "fetch_subtitle",
    "pick_track",
    "run_batch",
    "to_cues",
    "__version__",
]
