"""Consistency check of the committed injection-strip false-positive result artifact."""

from __future__ import annotations

import json
from pathlib import Path

from app.core import injection_controls as ic
from eval import measure_injection_strip_fp as m

ROOT = Path(__file__).resolve().parents[2]


def test_committed_result_is_consistent_and_carries_no_review_text() -> None:
    d = json.loads((ROOT / "eval" / "results" / "injection_strip_fp.json").read_text("utf-8"))
    for key in ("generated_at", "git_sha", "rule_set"):
        assert d[key]
    assert d["rule_set"]["rules"] == sorted(ic.RULES)
    assert d["first_party_combined"]["n"] == d["held_out"]["n"] + d["dev"]["n"] == 148
    blob = json.dumps(d)
    held, dev, _ = m.load_first_party()
    # No fixture text may leak into the committed artifact (licence rule).
    assert all(text[:40] not in blob for _, text in held + dev if len(text) >= 40)
