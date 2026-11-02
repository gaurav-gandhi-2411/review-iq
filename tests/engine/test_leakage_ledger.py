from __future__ import annotations

from engine.labelling import leakage_ledger as ll
from engine.labelling.pilot_sample import clean
from engine.labelling.scale_sample import text_sha


def test_committed_ledger_is_clean() -> None:
    assert ll.check() == []


def test_sealed_never_in_ledger_entries() -> None:
    import json

    ledger = json.loads(ll.LEDGER.read_text(encoding="utf-8"))
    assert not (ll.sealed_hashes() & set(ledger["entries"]))


def test_scan_finds_a_planted_sealed_text(tmp_path, monkeypatch) -> None:
    text = "this planted review text is long enough to count"
    (tmp_path / "f.txt").write_text("x\n" + text + "\n", encoding="utf-8")
    monkeypatch.setattr(ll, "ROOT", tmp_path)
    monkeypatch.setattr(ll, "tracked_files", lambda: ["f.txt"])
    assert ll.scan_repo({text_sha(clean(text))}) == [("f.txt", text_sha(clean(text)))]
    assert ll.scan_repo({"0" * 16}) == []


def test_amendment16_sets_are_registered_the_right_way(monkeypatch) -> None:
    sealed_item = {"id": "fresh16-A-food-0000", "sha": "a" * 16, "split": "test"}
    train_item = {"id": "train16-T1-food-0000", "sha": "b" * 16, "split": "val16"}
    monkeypatch.setattr(ll, "fresh_items", lambda: [sealed_item])
    monkeypatch.setattr(ll, "train16_items", lambda: [train_item])
    assert "a" * 16 in ll.sealed_hashes()  # fresh16 is sealed (test-only)
    entries = ll.build_ledger()["entries"]
    assert entries["b" * 16] == "train16-val16"  # training additions are SEEN items, never sealed
    assert "a" * 16 not in entries and "b" * 16 not in ll.sealed_hashes()


def test_a_train16_item_that_is_also_sealed_is_a_violation(monkeypatch) -> None:
    both = {"id": "train16-T1-food-0001", "sha": "c" * 16, "split": "train16"}
    monkeypatch.setattr(ll, "fresh_items", lambda: [{"id": "x", "sha": "c" * 16, "split": "test"}])
    monkeypatch.setattr(ll, "train16_items", lambda: [both])
    monkeypatch.setattr(ll, "scan_repo", lambda sealed: [])
    monkeypatch.setattr(ll, "near_hits", lambda: set())
    assert any("train16 items share a sealed hash" in p for p in ll.check())
