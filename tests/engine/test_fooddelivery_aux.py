from __future__ import annotations

from engine.labelling import fooddelivery_aux as FD
from engine.labelling import leakage_neardup as ND

SEALED = "The jacket arrived with a torn zipper and the seller never answered my messages about it."


def _fp() -> dict:
    n = ND.norm(SEALED)
    return {
        "items": {
            "abc": {"n": len(n), "gj": sorted(ND.shingles(n, 4)), "g12": sorted(ND.shingles(n, 12))}
        }
    }


def test_near_sealed_catches_light_edit_and_passes_unrelated() -> None:
    items, idx = FD._norm_index(_fp())
    assert FD.near_sealed(SEALED.upper() + "!!", items, idx) == "abc"
    assert (
        FD.near_sealed("Driver left the food outside and the order was cold and late.", items, idx)
        is None
    )


def test_strict_tags() -> None:
    assert FD.tags("my order was cold and the driver was late", strict=True)["delivery"]
    assert FD.tags("customer support gave me a refund", strict=True)["service"]
    assert not any(FD.tags("great app love the layout", strict=True).values())


def test_is_english() -> None:
    assert FD.is_english("The app is great for ordering food")
    assert not FD.is_english("这个应用很好用而且很方便使用")
