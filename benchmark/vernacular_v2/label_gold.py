"""Human gold-labeling session for the real-data vernacular benchmark (v2).

Thin wrapper around benchmark/data/label_helper.py's run() — same interactive CLI,
same SENT/URG/LANG rubrics, same resumability — just pointed at this benchmark's own
candidates/gold files instead of the v0.1 internal benchmark's.

Usage:
    uv run python benchmark/vernacular_v2/label_gold.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmark.data.label_helper import run  # noqa: E402

CANDIDATES_PATH = ROOT / "benchmark" / "vernacular_v2" / "candidates.jsonl"
GOLD_PATH = ROOT / "benchmark" / "vernacular_v2" / "gold_labels.jsonl"

if __name__ == "__main__":
    run(CANDIDATES_PATH, GOLD_PATH)
