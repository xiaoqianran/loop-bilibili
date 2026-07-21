"""仓库根路径与 sys.path 引导。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root(start: Path | None = None) -> Path:
    env = os.environ.get("LOOP_BILIBILI_ROOT")
    if env:
        return Path(env).resolve()

    here = (start or Path(__file__)).resolve()
    # packages/loop_core/workspace.py -> parents[2] = repo root
    candidates = [
        here.parents[2] if len(here.parents) > 2 else here.parent,
        Path.cwd(),
    ]
    for p in candidates:
        if (p / "main.py").is_file() or (p / "config.example.yaml").is_file():
            return p
    return candidates[0]


def ensure_sys_path(root: Path | None = None) -> Path:
    """把 packages/ 与 modules/ 加入 sys.path。"""
    root = root or repo_root()
    packages = root / "packages"
    modules = root / "modules"
    for p in (packages, modules):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root
