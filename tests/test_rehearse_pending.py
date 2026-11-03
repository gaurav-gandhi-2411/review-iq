"""Unit tests for supabase/ci/rehearse_pending.py's pure logic (no database)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "supabase" / "ci" / "rehearse_pending.py"
_spec = importlib.util.spec_from_file_location("rehearse_pending", _PATH)
assert _spec
assert _spec.loader
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)


def test_compare_passes_when_counts_hold_or_grow(capsys: pytest.CaptureFixture[str]) -> None:
    assert rp.compare({"a": 3, "b": 0}, {"a": 3, "b": 2, "c": 0}) is True
    assert "(new table)" in capsys.readouterr().out


def test_compare_fails_on_lost_rows(capsys: pytest.CaptureFixture[str]) -> None:
    assert rp.compare({"a": 3}, {"a": 2}) is False
    assert "LOST ROWS" in capsys.readouterr().out


def test_compare_fails_on_dropped_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert rp.compare({"a": 3}, {}) is False
    assert "TABLE DROPPED" in capsys.readouterr().out


def test_connect_refuses_non_local_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REHEARSAL_HOST", "db.example.supabase.co")
    with pytest.raises(SystemExit, match="must be local"):
        rp.connect()
