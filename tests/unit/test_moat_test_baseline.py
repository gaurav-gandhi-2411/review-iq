from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import app.core.providers.cassette as cassette_module
import httpx
import pytest
from eval.experiments import moat_test_baseline as mt


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here is offline: any DNS lookup (hence any remote connection) is a failure."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "getaddrinfo", boom)  # any remote host needs DNS first


@pytest.fixture
def tmp_cassettes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "moat_cassettes.json"
    monkeypatch.setattr(mt, "CASSETTE_PATH", path)
    monkeypatch.setattr(cassette_module, "CASSETTES_PATH", path)
    return path


def _gold_json(fx: dict[str, Any]) -> str:
    gt = fx["ground_truth"]
    keys = ("product", "stars", "stars_inferred", "pros", "cons", "buy_again", "sentiment")
    keys += ("topics", "competitor_mentions", "urgency", "feature_requests", "language")
    return json.dumps({k: gt.get(k) for k in keys if k in gt})


def test_unseen_set_is_the_70_unexposed_reviews() -> None:
    from eval.heldout_exposure import held_out_exposure

    fxs = mt.unseen_fixtures()
    assert len(fxs) == mt.EXPECTED_N_UNSEEN
    assert not {f["id"] for f in fxs} & set(held_out_exposure())
    assert [f["id"] for f in fxs] == sorted(f["id"] for f in fxs)


def test_headline_fields_match_the_fixtures_own_scoring_notes() -> None:
    notes = mt.unseen_fixtures()[0]["scoring_notes"]
    scored = set(notes["exact_match_fields"]) | set(notes["set_overlap_fields"])
    scored |= set(notes["fuzzy_fields"]) | set(notes["tolerance_fields"])
    assert set(mt.HEADLINE_FIELDS) == scored - {"stars", "language"}


def test_payload_is_zdr_seeded_deterministic_and_capped() -> None:
    p = mt.build_payload("openai/gpt-5.5", "sys", "usr")
    assert p["provider"] == {"zdr": True}
    assert p["seed"] == 42 and p["temperature"] == 0.0
    assert p["max_tokens"] == mt.MAX_TOKENS_CAP


def test_provider_is_privacy_safe_and_posts_zdr(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "provider": "DeepInfra",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    prov = mt.ZdrBaselineProvider(api_key="k", model="openai/gpt-oss-120b")
    assert prov.trains_on_input is False
    out = asyncio.run(prov.complete_with_meta("s", "u"))
    assert out == ("{}", 11, 7, "DeepInfra")
    assert seen["body"]["provider"] == {"zdr": True} and seen["body"]["seed"] == 42


def test_dry_run_is_offline_and_prints_no_review_text(capsys: pytest.CaptureFixture[str]) -> None:
    summaries = mt.run_dry(["openai/gpt-5.5", "anthropic/claude-haiku-5.5"], "plain")
    out = capsys.readouterr().out
    assert len(summaries) == 2 and all(s["n_calls"] == 70 for s in summaries)
    for fx in mt.unseen_fixtures():
        assert fx["review_text"][:30] not in out
    assert "TOTAL est_usd=" in out


def test_token_and_cost_arithmetic_is_exact() -> None:
    assert mt.estimate_tokens("a" * 300) == 100 and mt.estimate_tokens("a" * 301) == 101
    price = mt.ModelPrice(2.0, 10.0, False, 1)
    assert mt.usd(1_000_000, 1_000_000, price) == pytest.approx(12.0)
    assert mt.usd(500, 200, price) == pytest.approx(0.003)
    calls = mt.plan_calls("openai/gpt-5.5", mt.unseen_fixtures()[:2])
    s = mt.summarize_plan("openai/gpt-5.5", calls)
    assert s["est_usd"] == pytest.approx(sum(c["est_usd"] for c in calls))
    assert all(c["ceiling_usd"] >= c["est_usd"] for c in calls)


def test_pricing_table_only_has_zdr_eligible_models() -> None:
    assert all(p.zdr_endpoints > 0 for p in mt.PRICING.values())
    assert "anthropic/claude-fable-5.1" not in mt.PRICING  # 0 ZDR endpoints at snapshot


def test_cassette_key_is_stable_and_model_specific() -> None:
    a = mt.cassette_key("m1", "s", "u")
    assert a == mt.cassette_key("m1", "s", "u") != mt.cassette_key("m2", "s", "u")


def test_perfect_output_scores_one_and_garbage_is_flagged() -> None:
    fx = mt.unseen_fixtures()[0]
    good = mt.score_record(fx, _gold_json(fx), None)
    assert good["error"] is None
    scores = good["as_deployed"]["field_scores"]
    assert all(v == 1.0 for f, v in scores.items() if f != "stars")
    bad = mt.score_record(fx, "not json", None)
    assert bad["error"] and bad["error"].startswith("parse_error")
    assert mt.score_record(fx, None, "HTTPStatusError")["error"] == "HTTPStatusError"


def test_replay_end_to_end_is_offline_and_matches_gold(
    tmp_cassettes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for fx in mt.unseen_fixtures():
        system, user = mt.build_messages(fx["review_text"], "plain")
        key = mt.cassette_key("openai/gpt-5.5", system, user)
        cassette_module.record(key, _gold_json(fx), 5, 5)

    async def no_live(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("live call in replay mode")

    monkeypatch.setattr(mt.ZdrBaselineProvider, "complete_with_meta", no_live)
    log = tmp_path / "calls.jsonl"
    recs = asyncio.run(mt.run_model("openai/gpt-5.5", "plain", "replay", 0.0, "", log))
    assert len(recs) == 70 and not any(r["error"] for r in recs)
    s = mt.summarize_scores(recs, mt.load_production_scores())
    assert s["headline_split_excluded"]["score"] == pytest.approx(1.0)
    assert s["paired_vs_production"]["mean_diff_baseline_minus_production"] > 0
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 70 and all(r["status"] == "replay" and "usd_cost" in r for r in rows)


def test_replay_miss_is_scored_not_dropped(tmp_cassettes: Path, tmp_path: Path) -> None:
    recs = asyncio.run(
        mt.run_model("openai/gpt-5.5", "plain", "replay", 0.0, "", tmp_path / "c.jsonl")
    )
    assert len(recs) == 70 and all(r["error"] == "no cassette (replay mode)" for r in recs)


def test_record_budget_guard_stops_before_overspend(
    tmp_cassettes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def fake(self: Any, system: str, user: str) -> tuple[str, int, int, str]:
        calls.append(1)
        return "{}", 300, 4000, "FakeProvider"

    monkeypatch.setattr(mt.ZdrBaselineProvider, "complete_with_meta", fake)
    monkeypatch.setattr(mt, "PACING_SECONDS", 0.0)
    # Each fake call really costs ~$0.12 (4,000 output tokens at $30/M); a $0.30 cap allows 2.
    log = tmp_path / "c.jsonl"
    recs = asyncio.run(mt.run_model("openai/gpt-5.5", "plain", "record", 0.30, "k", log))
    assert len(calls) == 2 and len(recs) == 2
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert all(r["status"] == "live" and r["usd_cost"] > 0 for r in rows)
