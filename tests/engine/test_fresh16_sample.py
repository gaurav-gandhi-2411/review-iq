"""Amendment 16 draw: block definitions, disjointness, determinism, the registration guard (synthetic pools, no corpus)."""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest
from engine.labelling import fresh16_sample as F
from engine.labelling.fresh_sample import NearDupIndex
from engine.labelling.pilot_sample import clean
from engine.labelling.scale_sample import text_sha

WORDS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
]
PROBLEM = " it was too small and I had to return it"


def _pool(cat: str, n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        body = (
            " ".join(rng.choice(WORDS) for _ in range(rng.randint(8, 30)))
            + f" {cat} item {i} {rng.random()}"
        )
        if i % 4 == 0:
            body += PROBLEM
        text = clean(body)
        out.append(
            {
                "text": text,
                "stars": float(rng.choice([1, 2, 3, 4, 5])),
                "sha": text_sha(text),
                "ntok": rng.choice([40, 90, 200, 300]),
            }
        )
    return out


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict]]:
    for name, q in (("BLOCK_A", 12), ("BLOCK_B", 9), ("BLOCK_C", 6), ("T1", 8), ("T2", 8)):
        monkeypatch.setattr(F, name, dict.fromkeys(F.CATEGORIES, q))
    return {c: _pool(c, 400, seed=i) for i, c in enumerate(F.CATEGORIES)}


def test_blocks_follow_their_observable_definitions(small: dict) -> None:
    sealed, train = F.draw_all(small, NearDupIndex())
    by = lambda rows, b: [r for r in rows if r["block"] == b]  # noqa: E731
    assert len(by(sealed, "A")) == 36 and len(by(sealed, "B")) == 27 and len(by(sealed, "C")) == 18
    assert all(r["stars"] >= 4 for r in by(sealed, "B"))
    assert all(r["ntok"] > F.LONG_TOKENS for r in by(sealed, "C"))
    assert all(r["split"] == "test" for r in sealed)
    assert all(r["stars"] >= 4 and F.LEXICON.search(r["text"]) for r in by(train, "T1"))
    assert all(r["ntok"] > F.LONG_TOKENS for r in by(train, "T2"))
    assert {r["category"] for r in sealed} == set(F.CATEGORIES)


def test_nothing_is_drawn_twice_and_training_never_overlaps_the_sealed_set(small: dict) -> None:
    sealed, train = F.draw_all(small, NearDupIndex())
    shas = [r["sha"] for r in sealed + train]
    assert len(shas) == len(set(shas))
    assert not {r["sha"] for r in sealed} & {r["sha"] for r in train}
    ids = [r["id"] for r in sealed + train]
    assert len(ids) == len(set(ids)) and ids[0].startswith("fresh16-A-")


def test_the_draw_is_a_pure_function_of_the_seeds(small: dict) -> None:
    a = F.draw_all(small, NearDupIndex())
    b = F.draw_all(small, NearDupIndex())
    assert [r["id"] + r["sha"] for r in a[0] + a[1]] == [r["id"] + r["sha"] for r in b[0] + b[1]]


def test_prior_items_and_their_near_duplicates_are_never_drawn(small: dict) -> None:
    idx = NearDupIndex()
    victim = small["apparel"][3]["text"]
    idx.add(victim)
    idx.add(small["beauty"][5]["text"] + "!")  # a near-duplicate of a beauty pool item
    sealed, train = F.draw_all(small, idx)
    drawn = {r["text"] for r in sealed + train}
    assert victim not in drawn and small["beauty"][5]["text"] not in drawn


def test_training_split_is_by_content_hash_about_one_in_five(small: dict) -> None:
    _, train = F.draw_all(small, NearDupIndex())
    val = [r for r in train if r["split"] == "val16"]
    assert val and all(int(r["sha"][:8], 16) % F.VAL_TARGETED_SHARE == 0 for r in val)
    assert all(
        int(r["sha"][:8], 16) % F.VAL_TARGETED_SHARE != 0 for r in train if r["split"] == "train16"
    )


def test_a_pool_that_is_too_small_stops_instead_of_drawing_short(
    small: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(F, "BLOCK_A", dict.fromkeys(F.CATEGORIES, 5000))
    with pytest.raises(SystemExit, match="drawable"):
        F.draw_all(small, NearDupIndex())


def test_no_draw_before_the_registration(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs/specs").mkdir(parents=True)
    (tmp_path / "docs/specs/review-intent.md").write_text("### Amendment 15\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    with pytest.raises(SystemExit, match="register it"):
        F.registered_commit(tmp_path)
    (tmp_path / "docs/specs/review-intent.md").write_text(
        "### Amendment 15\n### Amendment 16 (x)\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-qm",
            "y",
        ],
        check=True,
    )
    assert len(F.registered_commit(tmp_path)) == 40


def test_the_real_repository_has_the_registration() -> None:
    assert len(F.registered_commit(Path(__file__).resolve().parents[2])) == 40
