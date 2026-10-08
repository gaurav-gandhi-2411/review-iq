"""Unit tests for scripts/check_pricing_consistency.py and scripts/cost_model.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from scripts import check_pricing_consistency as chk
from scripts import cost_model

REPO = Path(__file__).resolve().parents[2]
FILES = (
    "docs/pricing.json",
    "docs/cost-model.md",
    "docs/payments-readiness.md",
    "site/index.html",
    "app/api/bff/router.py",
    "app/core/pricing.py",
    "eval/results/token_cost_measurement_n106.json",
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for rel in FILES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dest)
    return tmp_path


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    f = root / rel
    text = f.read_text(encoding="utf-8")
    assert old in text, f"fixture drift: {old!r} not in {rel}"
    f.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_real_repo_is_consistent() -> None:
    assert chk.run(REPO) == []


def test_site_parse_finds_all_five_tiers() -> None:
    html = (REPO / "site" / "index.html").read_text(encoding="utf-8")
    plans = chk.parse_site_plans(html)
    assert plans["scale"] == {"quota": 100000, "usd": 199, "inr": 12999}
    assert plans["agency"] == {"quota": 200000, "usd": None, "inr": None}
    assert set(plans) == {"free", "starter", "growth", "scale", "agency"}


@pytest.mark.parametrize(
    ("rel", "old", "new", "needle"),
    [
        ("site/index.html", "$29<small", "$30<small", "starter.usd"),
        ("site/index.html", "25,000 reviews/mo", "50,000 reviews/mo", "growth.quota"),
        ("site/index.html", "&#8377;12,999/mo", "&#8377;13,999/mo", "scale.inr"),
        ("docs/cost-model.md", "| Starter | 5,000 | $29 |", "| Starter | 5,000 | $39 |", "stale"),
        ("docs/payments-readiness.md", "Growth $79/INR 4,999", "Growth $89/INR 4,999", "Growth"),
        ("app/api/bff/router.py", '"free": 100,', '"free": 1000,', "PLAN_QUOTA_LIMITS"),
        (
            "app/core/pricing.py",
            "USD_TO_INR_RATE = 95.6943",
            "USD_TO_INR_RATE = 90.0",
            "USD_TO_INR",
        ),
    ],
)
def test_mutation_is_detected(root: Path, rel: str, old: str, new: str, needle: str) -> None:
    _edit(root, rel, old, new)
    errs = chk.run(root)
    assert errs, f"mutation in {rel} went undetected"
    assert any(needle in e for e in errs)


def test_pricing_json_change_without_rerender_fails(root: Path) -> None:
    _edit(root, "docs/pricing.json", '"usd": 79', '"usd": 89')
    assert chk.run(root)


def test_missing_surface_fails_closed(root: Path) -> None:
    (root / "site" / "index.html").unlink()
    assert any("could not verify" in e for e in chk.run(root))


def test_margin_hand_computed() -> None:
    # Starter $29, 10 customers, worst case: 2.67 + 92.89/10 + 0.0354*29 = 12.9857; margin 55.2%
    p = cost_model.load()
    m = cost_model.margin(p, 29, 5000, 10, 1.0, cost_model.fixed_total(p))
    assert round(m, 3) == 0.552


def test_customers_for_margin_and_never() -> None:
    p = cost_model.load()
    f = cost_model.fixed_total(p)
    assert cost_model.customers_for_margin(p, 29, 5000, 1.0, f, 0.45) == 8
    assert cost_model.customers_for_margin(p, 15, 5000, 1.0, f, 0.45) == 19
    # price too low to ever cover variable cost at the target: no customer count works
    assert cost_model.customers_for_margin(p, 5, 5000, 1.0, f, 0.45) is None


def test_fixed_total() -> None:
    p = cost_model.load()
    assert cost_model.fixed_total(p) == 92.89
    assert cost_model.fixed_total(p, True) == 112.89
