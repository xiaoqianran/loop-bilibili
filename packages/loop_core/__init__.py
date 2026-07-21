"""loop-bilibili 公共能力：opencli 运行、限速、进度、错误分类。

业务模块（catalog 等）只保留领域逻辑，复用本包流水线零件。
"""

from .batch import (
    extract_bvid,
    load_bvids_from_args,
    load_bvids_from_catalog,
    load_done_set,
    pending_keys,
    save_done_set,
)
from .errors import FetchError, log_error
from .progress import clear_progress, load_partial, load_progress, save_partial, save_progress
from .rate_limit import (
    ITEM_DEFAULT_PROFILE,
    LIST_DEFAULT_PROFILE,
    PROFILES,
    Profile,
    get_profile,
    item_is_stricter_than_list,
    sleep_item,
    sleep_page,
    sleep_with_jitter,
)
from .runner import OpencliRunner, extract_json
from .timeutil import utc_now_iso
from .workspace import ensure_sys_path, repo_root

__all__ = [
    "FetchError",
    "ITEM_DEFAULT_PROFILE",
    "LIST_DEFAULT_PROFILE",
    "OpencliRunner",
    "PROFILES",
    "Profile",
    "clear_progress",
    "ensure_sys_path",
    "extract_bvid",
    "extract_json",
    "get_profile",
    "item_is_stricter_than_list",
    "load_bvids_from_args",
    "load_bvids_from_catalog",
    "load_done_set",
    "load_partial",
    "load_progress",
    "log_error",
    "pending_keys",
    "repo_root",
    "save_done_set",
    "save_partial",
    "save_progress",
    "sleep_item",
    "sleep_page",
    "sleep_with_jitter",
    "utc_now_iso",
]
