#!/usr/bin/env python3
"""兼容入口：转发到根目录 main.py catalog。

推荐直接使用:
  python3 main.py catalog 2071007724 --name 海安雨
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 将旧式「位置参数 target」转成 main.py catalog 子命令
argv = list(sys.argv[1:])
# 已是 subcommand 风格则原样
if argv and argv[0] in ("catalog", "modules", "status", "-h", "--help"):
    from main import main

    raise SystemExit(main(argv if argv[0] != "--help" else ["catalog", "--help"]))

# 默认注入 catalog
from main import main

raise SystemExit(main(["catalog", *argv]))
