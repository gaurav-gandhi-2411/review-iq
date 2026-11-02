"""S35: the live-parity check also reports ROUTING flips (R1 and R1+) between the served answers and the offline reference."""

from __future__ import annotations

from typing import Any

import pytest

# the script imports the threshold builder, which needs the eval extras (sklearn); the CI venv skips those together
pytest.importorskip("sklearn")
from scripts import classify_live_parity as L  # noqa: E402


def _t(p_yes: float, na: str = "no", urg: str = "low") -> dict[str, dict[str, Any]]:
    return {
        "needs_action": {
            "label": na,
            "candidate_label": na,
            "probabilities": {"yes": p_yes, "no": round(1 - p_yes, 6)},
        },
        "urgency": {"label": urg, "candidate_label": urg},
    }


def test_route_flips_counts_only_routing_changes() -> None:
    ids = ["a", "b", "c", "d"]
    offline = [_t(0.0699), _t(0.20), _t(0.01), _t(0.01, na="abstained")]
    served = [_t(0.0701), _t(0.19), _t(0.01, urg="abstained"), _t(0.01, na="abstained")]
    out = L.route_flips(ids, offline, served)
    # a: crosses tau upward (R1+ only); b: moves but stays above tau; c: urgency abstained flips R1 and R1+; d: unchanged
    assert out["r1"]["n_flips"] == 1 and out["r1"]["flips"][0]["id"] == "c"
    assert [f["id"] for f in out["r1_plus"]["flips"]] == ["a", "c"]
    a = out["r1_plus"]["flips"][0]
    assert (a["offline_route"], a["served_route"]) == (False, True)
    assert (a["offline_p_yes"], a["served_p_yes"]) == (0.0699, 0.0701)
    assert out["r1_plus"]["flip_rate"] == 0.5
    assert (out["r1_plus"]["n_offline_routed"], out["r1_plus"]["n_served_routed"]) == (2, 4)


def test_route_flips_empty() -> None:
    assert L.route_flips([], [], [])["r1_plus"]["flip_rate"] is None
