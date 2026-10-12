from __future__ import annotations

import json

from engine.labelling import leakage_ledger as ll
from engine.labelling import safety_split as ss


def test_expected_is_binomial_of_population() -> None:
    e = ss.expected(365)
    assert e["sealed_positives_mean"] == 146.0
    assert e["sealed_positives_sd"] == 9.4
    assert e["train_val_positives_mean"] == 219.0


def test_split_is_a_pure_function_of_the_hash() -> None:
    shas = [f"{i:016x}" for i in range(4000)]
    assert [ss.split_of(s) for s in shas] == [ss.split_of(s) for s in shas]
    share = sum(ss.split_of(s) == "test" for s in shas) / len(shas)
    assert abs(share - ss.TEST_SHARE) < 0.03


def test_neardup_clusters_group_case_and_punctuation_variants() -> None:
    a = ss.fingerprint("The heater started to smoke and smelled burnt after one hour of use.")
    b = ss.fingerprint("the heater started to smoke, and smelled burnt after one hour of use")
    c = ss.fingerprint("Completely unrelated review about a delivery that arrived two days late.")
    g = ss.neardup_clusters({"a": a, "b": b, "c": c})
    assert g["a"] == g["b"] != g["c"]


def test_safety_test_hashes_join_the_sealed_list_and_train_hashes_the_ledger(monkeypatch) -> None:
    items = [
        {"id": "x", "sha": "t" * 16, "split": "test"},
        {"id": "y", "sha": "r" * 16, "split": "train"},
        {"id": "z", "sha": "d" * 16, "split": "dropped"},
    ]
    monkeypatch.setattr(ll, "safety_items", lambda: items)
    assert "t" * 16 in ll.sealed_hashes()
    entries = ll.build_ledger()["entries"]
    assert entries["r" * 16] == "safety-train" and entries["d" * 16] == "safety-dropped"
    assert "t" * 16 not in entries


def test_committed_split_has_no_overlap_between_test_and_the_rest() -> None:
    rows = json.loads((ll.ROOT / ll.SAFETY).read_text(encoding="utf-8"))["items"]
    test = {r["sha"] for r in rows if r["split"] == "test"}
    assert test and not test & {r["sha"] for r in rows if r["split"] != "test"}
