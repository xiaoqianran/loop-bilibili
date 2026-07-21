"""loop-bilibili 公共能力：opencli 运行、限速、进度、错误分类。

业务模块（catalog 等）只保留领域逻辑，复用本包流水线零件。
"""

from .errors import FetchError, log_error
from .progress import clear_progress, load_partial, load_progress, save_partial, save_progress
from .rate_limit import PROFILES, Profile, sleep_with_jitter
from .runner import OpencliRunner, extract_json
from .timeutil import utc_now_iso
from .workspace import ensure_sys_path, repo_root

__all__ = [
    "FetchError",
    "OpencliRunner",
    "PROFILES",
    "Profile",
    "clear_progress",
    "ensure_sys_path",
    "extract_json",
    "load_partial",
    "load_progress",
    "log_error",
    "repo_root",
    "save_partial",
    "save_progress",
    "sleep_with_jitter",
    "utc_now_iso",
]
