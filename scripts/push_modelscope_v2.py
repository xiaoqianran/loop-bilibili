#!/usr/bin/env python3
"""Deprecated: use push_hf_v2.py (Hugging Face). Forwards for old docs/scripts."""
from __future__ import annotations
import sys
from pathlib import Path

print(
    "warn: push_modelscope_v2.py is deprecated → scripts/push_hf_v2.py",
    file=sys.stderr,
)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from push_hf_v2 import main  # type: ignore

if __name__ == "__main__":
    raise SystemExit(main())
