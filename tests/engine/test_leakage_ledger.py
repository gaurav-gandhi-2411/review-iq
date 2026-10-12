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
