"""Amendment 17 draw: blocks, disjointness, determinism, prior exclusion, top-up and the registration guard (synthetic pools, no corpus)."""

from __future__ import annotations

from pathlib import Path

import pytest
from engine.labelling import fresh17_sample as F
from engine.labelling.fresh_sample import NearDupIndex
from tests.engine.test_fresh16_sample import _pool


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict]]:
    for name, q in (("BLOCK_A", 12), ("BLOCK_B", 15), ("TOPUP_B", 5)):
        monkeypatch.setattr(F, name, dict.fromkeys(F.CATEGORIES, q))
    return {c: _pool(c, 400, seed=i) for i, c in enumerate(F.CATEGORIES)}


def test_blocks_follow_their_definitions_and_are_disjoint(small: dict) -> None:
    rows = F.draw_sealed(small, NearDupIndex())
    a = [r for r in rows if r["block"] == "A"]
    b = [r for r in rows if r["block"] == "B"]
    assert len(a) == 36 and len(b) == 45 and all(r["stars"] >= 4 for r in b)
    assert all(r["split"] == "test" and r["id"].startswith("fresh17-") for r in rows)
    assert len({r["sha"] for r in rows}) == len(rows) and len({r["id"] for r in rows}) == len(rows)


def test_the_draw_is_a_pure_function_of_the_seed(small: dict) -> None:
    a = F.draw_sealed(small, NearDupIndex())
    b = F.draw_sealed(small, NearDupIndex())
    assert [r["id"] + r["sha"] for r in a] == [r["id"] + r["sha"] for r in b]


def test_prior_items_and_near_duplicates_are_never_drawn(small: dict) -> None:
    idx = NearDupIndex()
    victim = small["food"][7]["text"]
    idx.add(victim)
    assert victim not in {r["text"] for r in F.draw_sealed(small, idx)}


def test_the_top_up_uses_its_own_seed_and_block(small: dict) -> None:
    top = F.draw_sealed(small, NearDupIndex(), topup=True)
    assert len(top) == 15 and all(r["block"] == "B2" and r["stars"] >= 4 for r in top)


def test_earlier_amendment16_sets_are_excluded() -> None:
    assert "reports/labelling/fresh16/manifest.json" in F.EXCLUDE
    assert "reports/labelling/train16/manifest.json" in F.EXCLUDE


def test_the_draw_refuses_without_the_registration(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "commit", "-q", "--allow-empty", "-m", "empty"], check=True)  # fmt: skip
    with pytest.raises(SystemExit, match="register it before drawing"):
        F.registered_commit(tmp_path)
