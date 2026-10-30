"""Amendment 18 draw (prepared, unregistered): S4-only, disjointness, determinism, prior exclusion, top-up, registration guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from engine.labelling import fresh18_sample as F
from engine.labelling.fresh_sample import NearDupIndex
from tests.engine.test_fresh16_sample import _pool


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict]]:
    for name, q in (("BLOCK_S", 15), ("TOPUP_S", 5)):
        monkeypatch.setattr(F, name, dict.fromkeys(F.CATEGORIES, q))
    return {c: _pool(c, 400, seed=i) for i, c in enumerate(F.CATEGORIES)}


def test_every_item_is_s4_and_unique(small: dict) -> None:
    rows = F.draw_sealed(small, NearDupIndex())
    assert len(rows) == 45 and all(r["stars"] >= 4 and r["block"] == "S" for r in rows)
    assert all(r["split"] == "test" and r["id"].startswith("fresh18-S-") for r in rows)
    assert len({r["sha"] for r in rows}) == len(rows) and len({r["id"] for r in rows}) == len(rows)


def test_the_draw_is_a_pure_function_of_the_seed(small: dict) -> None:
    a = F.draw_sealed(small, NearDupIndex())
    b = F.draw_sealed(small, NearDupIndex())
    assert [r["id"] + r["sha"] for r in a] == [r["id"] + r["sha"] for r in b]


def test_prior_items_and_near_duplicates_are_never_drawn(small: dict) -> None:
    idx = NearDupIndex()
    victim = next(it for it in small["food"] if it["stars"] >= 4)["text"]
    idx.add(victim)
    assert victim not in {r["text"] for r in F.draw_sealed(small, idx)}


def test_the_top_up_uses_its_own_seed_and_block(small: dict) -> None:
    top = F.draw_sealed(small, NearDupIndex(), topup=True)
    assert len(top) == 15 and all(r["block"] == "S2" and r["stars"] >= 4 for r in top)
    main = F.draw_sealed(small, NearDupIndex())
    assert [r["sha"] for r in top] != [r["sha"] for r in main][:15]


def test_every_earlier_sealed_set_is_excluded() -> None:
    for m in ("fresh16", "train16", "fresh17"):
        assert f"reports/labelling/{m}/manifest.json" in F.EXCLUDE
    assert F.SEED_SEALED not in (49, 50) and F.SEED_TOPUP not in (49, 50)


def test_the_main_block_is_sized_for_300_s4_positives() -> None:
    # 3.8 percent S4 positive rate (s32-s4-llm-check.md): expected positives must clear 300.
    assert sum(F.BLOCK_S.values()) * 0.038 >= 300


def test_the_draw_refuses_without_the_registration(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "commit", "-q", "--allow-empty", "-m", "empty"], check=True)  # fmt: skip
    with pytest.raises(SystemExit, match="register it before drawing"):
        F.registered_commit(tmp_path)
