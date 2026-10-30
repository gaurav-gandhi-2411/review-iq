"""Q3a: the pre-batch headroom guard refuses on real insufficiency and fails closed. Offline."""

from __future__ import annotations

import json
import time

import pytest
from eval.quota_guard import (
    HeadroomRefusedError,
    LiveSignal,
    check_persisted_floor,
    evaluate_model,
    ledger_used,
    merge_usage,
    parse_live_signal,
    parse_retry_after,
    parse_tpd_429,
    preflight,
    record_tpd_observation,
)

LARGE = "openai/gpt-oss-120b"

# Real headers from a 200 response (captured live 2026-10-08; same shape as ADR 0028's capture).
REAL_HEADERS = {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-requests": "999",
    "x-ratelimit-remaining-tokens": "7920",
    "x-ratelimit-reset-requests": "1m26.4s",
    "x-ratelimit-reset-tokens": "600ms",
}


def _live(remaining_requests: int) -> LiveSignal:
    return LiveSignal(1000, remaining_requests, 8000, 7920)


def test_parses_real_headers_case_insensitively() -> None:
    sig = parse_live_signal({k.upper(): v for k, v in REAL_HEADERS.items()})
    assert sig is not None and sig.requests_used_today == 1


@pytest.mark.parametrize("drop", ["x-ratelimit-remaining-requests", "x-ratelimit-limit-tokens"])
def test_missing_header_is_unparseable(drop: str) -> None:
    h = {k: v for k, v in REAL_HEADERS.items() if k != drop}
    assert parse_live_signal(h) is None


@pytest.mark.parametrize("bad", ["", "abc", "-3", "2000"])
def test_garbage_remaining_requests_is_unparseable(bad: str) -> None:
    assert parse_live_signal({**REAL_HEADERS, "x-ratelimit-remaining-requests": bad}) is None


def test_none_and_empty_headers_unparseable() -> None:
    assert parse_live_signal(None) is None
    assert parse_live_signal({}) is None


def test_passes_when_both_signals_have_room() -> None:
    r = evaluate_model(LARGE, 20_000, local_used=10_000, live=_live(990))
    assert r.ok


def test_refuses_when_ledger_floor_insufficient() -> None:
    r = evaluate_model(LARGE, 20_000, local_used=80_000, live=_live(990))
    assert not r.ok and "ledger floor" in r.reason


def test_refuses_when_extraction_costs_low_but_live_header_high() -> None:
    # The S19 incident shape: the local tables say 5K used, but the key's real daily request
    # counter shows 600 requests burned by consumers the tables never see.
    r = evaluate_model(LARGE, 20_000, local_used=5_000, live=_live(400))
    assert not r.ok
    assert "live signal" in r.reason
    assert r.ceiling_headroom == 90_000 and r.live_headroom == 95_000 - 600_000


def test_fails_closed_when_live_signal_missing() -> None:
    r = evaluate_model(LARGE, 1, local_used=0, live=None)
    assert not r.ok and "fail closed" in r.reason


def test_refuses_when_daily_requests_exhausted() -> None:
    assert not evaluate_model(LARGE, 1, local_used=0, live=_live(0)).ok


def test_boundary_exactly_fits_passes_one_over_refuses() -> None:
    assert evaluate_model(LARGE, 90_000, local_used=5_000, live=_live(1000)).ok
    assert not evaluate_model(LARGE, 90_001, local_used=5_000, live=_live(1000)).ok


def test_preflight_raises_with_report_and_passes_when_sufficient() -> None:
    live = {LARGE: _live(990)}
    ok = preflight({LARGE: 10_000}, local_used_by_model={LARGE: 1}, live_by_model=live)
    assert ok.ok
    with pytest.raises(HeadroomRefusedError) as ei:
        preflight({LARGE: 10_000}, local_used_by_model={LARGE: 1}, live_by_model={LARGE: None})
    assert not ei.value.report.ok


def test_preflight_without_key_and_without_injected_live_refuses() -> None:
    with pytest.raises(HeadroomRefusedError):
        preflight({LARGE: 1}, api_key=None)


def test_every_model_must_pass() -> None:
    live = {"a": _live(990), "b": _live(100)}
    with pytest.raises(HeadroomRefusedError):
        preflight({"a": 1_000, "b": 1_000}, live_by_model=live)


def test_ledger_sums_trailing_24h_and_fails_on_corrupt_file(tmp_path) -> None:
    p = tmp_path / "l.json"
    now = 1_000_000.0
    p.write_text(
        json.dumps(
            {
                "entries": [
                    {"ts": now - 10, "model": LARGE, "tokens": 700},
                    {"ts": now - 90_000, "model": LARGE, "tokens": 999},  # outside window
                ]
            }
        ),
        encoding="utf-8",
    )
    assert ledger_used([p, tmp_path / "missing.json"], now) == {LARGE: 700}
    assert merge_usage({LARGE: 700}, {LARGE: 300, "x": 1}) == {LARGE: 1000, "x": 1}
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ledger_used([p], now)


# --- 2026-10-08 incident: the request-count proxy was blind to rolling-window TPD draw ----------
REAL_429 = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` "
    "in organization `org_x` service tier `on_demand` on tokens per day (TPD): Limit 200000, "
    "Used 199409, Requested 1678. Please try again in 2m12.19s. Need more tokens?', "
    "'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)


def test_parse_tpd_429_real_shape_and_retry_hint() -> None:
    assert parse_tpd_429(REAL_429) == (200000, 199409, 1678)
    assert parse_retry_after(REAL_429) == pytest.approx(132.19)
    assert parse_retry_after("try again in 1h2m3s.") == 3723
    assert parse_retry_after("try again in 450ms") is None


@pytest.mark.parametrize(
    "text",
    ["", "Rate limit on tokens per minute (TPM): Limit 8000, Used 7000, Requested 2000", "x"],
)
def test_parse_tpd_429_rejects_non_tpd(text: str) -> None:
    assert parse_tpd_429(text) is None


def test_observation_makes_preflight_refuse_without_probing(tmp_path) -> None:
    obs = tmp_path / "obs.json"
    now = time.time()
    assert record_tpd_observation(LARGE, REAL_429, now=now, path=obs) is not None
    assert record_tpd_observation(LARGE, "not a 429", now=now, path=obs) is None
    # api_key given, no live injection: a probe would be attempted if the floor did not skip it.
    with pytest.raises(HeadroomRefusedError) as ei:
        preflight({LARGE: 1_678}, api_key="unused", observation_path=obs)
    assert "TPD 429 observed" in ei.value.report.models[0].reason
    assert check_persisted_floor({LARGE: 1}, now=now + 23 * 3600, path=obs)
    assert not check_persisted_floor({LARGE: 1}, now=now + 25 * 3600, path=obs)  # aged out
    assert not check_persisted_floor({"other/model": 1}, now=now, path=obs)  # per-model


def test_newer_low_observation_supersedes_and_corrupt_file_fails_closed(tmp_path) -> None:
    obs = tmp_path / "obs.json"
    record_tpd_observation(LARGE, REAL_429, now=100.0, path=obs)
    low = REAL_429.replace("Used 199409", "Used 1000")
    record_tpd_observation(LARGE, low, now=200.0, path=obs)
    assert not check_persisted_floor({LARGE: 1_678}, now=300.0, path=obs)
    obs.write_text("{broken", encoding="utf-8")
    assert LARGE in check_persisted_floor({LARGE: 1}, now=300.0, path=obs)


def test_record_hien_stops_on_first_tpd_429_without_sleeping(tmp_path, monkeypatch) -> None:
    import asyncio
    import importlib
    import json as _json

    import eval.quota_guard as qg

    monkeypatch.delenv(
        "EVAL_CASSETTE_MODE", raising=False
    )  # module import sets it; monkeypatch undoes
    monkeypatch.setattr(qg, "OBSERVATION_PATH", tmp_path / "obs.json")
    rh = importlib.import_module("eval.reply.record_hien")
    obs_path = tmp_path / "obs.json"
    monkeypatch.setattr(
        rh, "record_tpd_observation", lambda m, t: qg.record_tpd_observation(m, t, path=obs_path)
    )

    async def boom(_req):
        raise RuntimeError(REAL_429)

    async def no_sleep(*_a, **_k):
        raise AssertionError("must not sleep and retry on a TPD 429")

    monkeypatch.setattr(rh, "draft_reply", boom)
    monkeypatch.setattr(rh.asyncio, "sleep", no_sleep)
    fixture = next(
        _json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(rh.FIXTURES_DIR.glob("*.json"))
        if _json.loads(p.read_text(encoding="utf-8"))["language"] == "hi-en"
    )
    with pytest.raises(SystemExit) as ei:
        asyncio.run(rh._record_one(fixture))
    assert ei.value.code == 2
    assert (
        _json.loads(obs_path.read_text(encoding="utf-8"))[rh.get_settings().groq_model_large][
            "used"
        ]
        == 199409
    )
