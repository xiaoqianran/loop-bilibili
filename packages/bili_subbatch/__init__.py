"""SubBatch-protocol Bilibili subtitle client + extensible pipeline."""

from .client import BiliClient, SubtitleResult, fetch_subtitle
from .homepage import (
    HomepageCard,
    fetch_homepage,
    fetch_rcmd_page,
    parse_rcmd_item,
    write_homepage_export,
)
from .hub import rebuild_all_hubs, write_up_hub
from .models import BatchConfig, BatchStats
from .pipeline import SubtitlePipeline, run_batch
from .util import extract_bvid, pick_track, to_cues

__version__ = "0.4.0"
__all__ = [
    "BiliClient",
    "BatchConfig",
    "BatchStats",
    "HomepageCard",
    "SubtitlePipeline",
    "SubtitleResult",
    "extract_bvid",
    "fetch_homepage",
    "fetch_rcmd_page",
    "fetch_subtitle",
    "parse_rcmd_item",
    "pick_track",
    "rebuild_all_hubs",
    "run_batch",
    "to_cues",
    "write_homepage_export",
    "write_up_hub",
    "__version__",
]
