from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from engine.intent import fresh18_groq120 as G
from engine.intent import llm_moat as M
from engine.intent import llm_moat_local as L

from engine import groq_budget_hook


def _texts(tmp_path: Path) -> Path:
    p = tmp_path / "texts.jsonl"
    p.write_text(
        "".join(json.dumps({"id": f"x{i}", "text": f"review {i}"}) + "\n" for i in range(3)),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setenv("GROQ_EVAL_KEY", "test-not-a-key")
    monkeypatch.setattr(groq_budget_hook, "install", lambda: None)
    # run() sets the 120b generation settings on the shared module dict; a copy keeps them out of other tests
    monkeypatch.setattr(L, "GEN", dict(L.GEN))

    def fake(key: str, text: str) -> tuple[dict, int, str]:
        calls.append(text)
        return {"needs_action": {"label": "yes"}}, 500, "stop"

    monkeypatch.setattr(G, "ask_groq", fake)
    return calls


def test_runs_in_subsample_order_and_resumes(tmp_path: Path, offline: list[str]) -> None:
    out = tmp_path / "out"
    assert G.run(["x2", "x0"], _texts(tmp_path), out, limit=1) == 0
    assert G.run(["x2", "x0"], _texts(tmp_path), out, limit=None) == 0
    rows = [
        json.loads(ln)
        for ln in (out / "gpt-oss-120b.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [r["id"] for r in rows] == ["x2", "x0"]
    assert offline == ["review 2", "review 0"]  # no id is asked twice
    assert set(rows[0]) == {"id", "ans", "tokens", "finish_reason"}


def test_own_cap_stops_before_calling(tmp_path: Path, offline: list[str]) -> None:
    out = tmp_path / "out"
    out.mkdir()
    M.usage_path(out, G.MODEL_120B).write_text(
        json.dumps({"ts": time.time() - 60, "tokens": G.CAP_24H}) + "\n", encoding="utf-8"
    )
    assert G.run(["x0"], _texts(tmp_path), out, limit=None) == 4
    assert offline == []


def test_missing_text_or_key_fails_loud(
    tmp_path: Path, offline: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit, match="no text"):
        G.run(["nope"], _texts(tmp_path), tmp_path / "out", limit=None)
    monkeypatch.delenv("GROQ_EVAL_KEY")
    with pytest.raises(SystemExit, match="GROQ_EVAL_KEY"):
        G.run(["x0"], _texts(tmp_path), tmp_path / "out", limit=None)
    assert offline == []
