"""engine/groq_budget_hook.py: benchmark Groq calls obey the shared budget ledger (budget gating only)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from engine import groq_budget_hook as hook

URL = "https://api.groq.com/openai/v1/chat/completions"
BODY = {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "x" * 350}], "max_tokens": 50}


@dataclass
class _D:
    allowed: bool
    reason: str = "ok"
    detail: str = ""
    retry_after_s: int = 0


class FakeLedger:
    def __init__(self, decisions: list[_D] | None = None) -> None:
        self.decisions = decisions or [_D(True)]
        self.checks: list[tuple[str, str, int]] = []
        self.records: list[tuple] = []
        self.observed: list[tuple[str, str]] = []

    def check(self, project: str, model: str, est: int) -> _D:
        self.checks.append((project, model, est))
        return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]

    def record(self, project, model, tokens, source="", cached_tokens=None) -> None:  # noqa: ANN001
        self.records.append((project, model, tokens, cached_tokens))

    def observe_429(self, text: str, model: str) -> int:
        self.observed.append((text, model))
        return 0


def _ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [],
            "usage": {"total_tokens": 1234, "prompt_tokens_details": {"cached_tokens": 1000}},
        },
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch):
    hook.uninstall()
    led = FakeLedger()
    monkeypatch.setattr(hook, "POLL_S", 0.01)
    hook.install(ledger=led, module=object())
    yield led
    hook.uninstall()


def _client(handler) -> httpx.Client:  # noqa: ANN001
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_groq_call_is_checked_then_recorded_with_usage_and_cached(fake: FakeLedger) -> None:
    r = _client(_ok).post(URL, json=BODY)
    assert r.status_code == 200
    project, model, est = fake.checks[0]
    assert (project, model) == ("benchmark", "openai/gpt-oss-20b")
    assert est == int(350 / 3.5) + 50  # prompt chars / 3.5 + max_tokens
    assert fake.records == [("benchmark", "openai/gpt-oss-20b", 1234, 1000)]


def test_request_is_not_altered(fake: FakeLedger) -> None:
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return _ok(req)

    _client(handler).post(URL, json=BODY)
    assert seen == [BODY]


def test_other_hosts_and_paths_are_untouched(fake: FakeLedger) -> None:
    _client(_ok).post("https://example.com/v1/chat/completions", json=BODY)
    _client(_ok).get("https://api.groq.com/openai/v1/models")
    assert fake.checks == [] and fake.records == []


def test_waits_while_the_ledger_says_wait_then_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    hook.uninstall()
    led = FakeLedger([_D(False, "recording_active", "TriageIQ is recording"), _D(False), _D(True)])
    monkeypatch.setattr(hook, "POLL_S", 0.01)
    hook.install(ledger=led, module=object())
    try:
        assert _client(_ok).post(URL, json=BODY).status_code == 200
        assert len(led.checks) == 3 and len(led.records) == 1
    finally:
        hook.uninstall()


def test_wait_timeout_raises_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    hook.uninstall()
    led = FakeLedger([_D(False, "project_cap", "benchmark has spent 99,500 of 100,000")])
    monkeypatch.setattr(hook, "POLL_S", 0.01)
    monkeypatch.setattr(hook, "WAIT_S", 0.05)
    hook.install(ledger=led, module=object())
    sent: list[int] = []
    try:
        with pytest.raises(hook.BudgetWaitTimeout) as ei:
            _client(lambda r: (sent.append(1), _ok(r))[1]).post(URL, json=BODY)
        assert "project_cap" in str(ei.value) and sent == []
    finally:
        hook.uninstall()


def test_tpd_429_is_folded_in_and_not_recorded(fake: FakeLedger) -> None:
    body = "tokens per day (TPD): Limit 200000, Used 199843, Requested 3551"
    r = _client(lambda req: httpx.Response(429, text=body)).post(URL, json=BODY)
    assert r.status_code == 429
    assert fake.observed == [(body, "openai/gpt-oss-20b")] and fake.records == []


def test_server_errors_are_not_counted(fake: FakeLedger) -> None:
    _client(lambda req: httpx.Response(500, text="boom")).post(URL, json=BODY)
    assert fake.records == [] and fake.observed == []


def test_unparsable_success_charges_the_estimate(fake: FakeLedger) -> None:
    _client(lambda req: httpx.Response(200, text="not json")).post(URL, json=BODY)
    assert fake.records[0][2] == fake.checks[0][2]


def test_async_client_is_gated_too(fake: FakeLedger) -> None:
    async def go() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as c:
            return (await c.post(URL, json=BODY)).status_code

    assert asyncio.run(go()) == 200
    assert fake.checks and fake.records == [("benchmark", "openai/gpt-oss-20b", 1234, 1000)]


def test_install_is_idempotent_and_uninstall_restores() -> None:
    hook.uninstall()
    original = httpx.Client.send
    hook.install(ledger=FakeLedger(), module=object())
    patched = httpx.Client.send
    hook.install(ledger=FakeLedger(), module=object())  # second call must not stack a wrapper
    assert httpx.Client.send is patched and patched is not original
    hook.uninstall()
    assert httpx.Client.send is original


def test_missing_ledger_module_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    hook.uninstall()
    monkeypatch.setenv("GROQ_BUDGET_MODULE_DIR", "/nonexistent")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    with pytest.raises(RuntimeError, match="Refusing to make ungated Groq calls"):
        hook.install()
    assert httpx.Client.send is hook.httpx.Client.send  # nothing was patched


def test_real_ledger_enforces_the_benchmark_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        gb_dir = hook._find_module_dir()
    except RuntimeError:
        pytest.skip("triage-iq tools/groq_budget not available on this machine")
    import importlib
    import sys

    sys.path.insert(0, str(gb_dir))
    gb = importlib.import_module("groq_budget")
    led = gb.Ledger(tmp_path / "root")
    led.record("benchmark", "openai/gpt-oss-120b", 99_900)  # 100K cap, ~100 left
    hook.uninstall()
    monkeypatch.setattr(hook, "POLL_S", 0.01)
    monkeypatch.setattr(hook, "WAIT_S", 0.05)
    hook.install(ledger=led, module=gb)
    try:
        with pytest.raises(hook.BudgetWaitTimeout):
            _client(_ok).post(URL, json={**BODY, "model": "openai/gpt-oss-120b"})
    finally:
        hook.uninstall()
