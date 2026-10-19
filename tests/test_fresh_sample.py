from __future__ import annotations

import random

from engine.labelling.fresh_sample import NearDupIndex, draw_fresh
from engine.labelling.pilot_sample import clean
from engine.labelling.scale_sample import text_sha

BASE = "This blender works well and the jar is easy to clean after every single smoothie I make at home, the motor is strong enough for ice and frozen fruit, the lid seals tight, and it has lasted a full year of daily use without any problem so far"


def test_near_duplicate_index_catches_edits_but_not_different_reviews() -> None:
    idx = NearDupIndex()
    idx.add(BASE)
    assert idx.hit(BASE)
    assert idx.hit(BASE.upper() + "!!")  # re-cased, re-punctuated
    assert idx.hit(BASE.replace("clean", "cleen"))  # one-letter typo, Jaccard still >= 0.9
    assert not idx.hit(
        "Terrible shampoo, my scalp itched for two days and the bottle leaked in my bag"
    )
    assert not idx.hit(BASE[:30])  # a short prefix is a different, much smaller shingle set


def test_draw_fresh_is_deterministic_and_skips_prior_and_internal_near_duplicates() -> None:
    rng = random.Random(0)
    words = [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "kappa",
        "omega",
        "sigma",
        "theta",
        "lambda",
        "zeta",
    ]

    def mk() -> str:
        return " ".join(rng.choice(words) + str(rng.randint(0, 999)) for _ in range(14))

    texts = [clean(mk()) for _ in range(120)]
    pool = {
        "food": [(t, 5.0, text_sha(clean(t))) for t in texts]
        + [(texts[0] + " x", 5.0, text_sha(clean(texts[0] + " x")))]
    }
    prior = NearDupIndex()
    prior.add(texts[1])
    rows1, d1 = draw_fresh(pool, 50, prior, seed=7)
    prior2 = NearDupIndex()
    prior2.add(texts[1])
    rows2, _ = draw_fresh(pool, 50, prior2, seed=7)
    assert [r["id"] for r in rows1] == [r["id"] for r in rows2] and len(rows1) == 50
    assert all(r["split"] == "test" for r in rows1)
    assert texts[1] not in {r["text"] for r in rows1}  # prior item excluded
    assert not (
        {texts[0], texts[0] + " x"} <= {r["text"] for r in rows1}
    )  # no internal near-duplicate pair
    assert d1["food"]["kept"] == 50
