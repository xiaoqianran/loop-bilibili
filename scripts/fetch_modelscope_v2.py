#!/usr/bin/env python3
"""Deprecated: use fetch_hf_v2.py (Hugging Face). Forwards for old CI/docs."""
from __future__ import annotations
import sys
from pathlib import Path

print(
    "warn: fetch_modelscope_v2.py is deprecated → scripts/fetch_hf_v2.py",
    file=sys.stderr,
)
# Re-exec HF fetch with same argv (map nothing special)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_hf_v2 import main  # type: ignore

if __name__ == "__main__":
    raise SystemExit(main())
