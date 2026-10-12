"""Make every benchmark/eval Groq call obey the machine-wide budget ledger.

Why: on 2026-10-11 the `GROQ_API_KEY_BENCHMARK` key made about 2,100 calls (about 300K tokens on
gpt-oss-120b and 230K on gpt-oss-20b) on the SHARED Personal Groq account, drained the rolling-24h pool
production draws on, and a TriageIQ deploy smoke test then failed on a 429. The ledger lives in the
triage-iq repo (`tools/groq_budget`, per-model rolling 24 h, per-project allocations, recording-active flag).

How: one patch of `httpx.Client.send` / `httpx.AsyncClient.send` for `api.groq.com/.../chat/completions`.
Every Groq path in this repo goes through httpx (the `groq` SDK, `GroqProvider`, and the raw `httpx.post`
calls in llm_moat / llm_baseline / quota_guard), so no call site changes and no request is altered:
  before a call   wait until the ledger allows the project (cap, pool, recording-active flag);
  after a 200     record prompt + completion tokens (and cached tokens when Groq reports them);
  after a TPD 429 fold Groq's "Used" figure in (`observe_429`).
Model choices, prompts, parameters and the scripts' own caps (100K per model per rolling 24 h) are untouched.

Fail closed: if the ledger module cannot be found, `install()` raises instead of letting calls through
ungated. Production never imports this module.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT = os.environ.get("GROQ_BUDGET_PROJECT", "benchmark")
HOST = "api.groq.com"
WAIT_S = float(os.environ.get("GROQ_BUDGET_WAIT_S", str(6 * 3600)))
POLL_S = 15.0
DEFAULT_COMPLETION = 600  # when a request sets no max_tokens (the ledger errs conservative)

_state: dict[str, Any] = {"installed": False, "ledger": None, "module": None}


class BudgetWaitTimeout(RuntimeError):
    """The ledger did not allow the call within GROQ_BUDGET_WAIT_S; nothing was sent."""


def _find_module_dir() -> Path:
    env = os.environ.get("GROQ_BUDGET_MODULE_DIR")
    candidates = [Path(env)] if env else []
    root = Path(__file__).resolve().parents[1]
    candidates += [root.parent / "triage-iq" / "tools" / "groq_budget"]
    for c in candidates:
        if (c / "groq_budget.py").exists():
            return c
    raise RuntimeError(
        "groq_budget module not found: set GROQ_BUDGET_MODULE_DIR to triage-iq/tools/groq_budget "
        f"(looked in {[str(c) for c in candidates]}). Refusing to make ungated Groq calls."
    )


def _load_ledger() -> tuple[Any, Any]:
    sys.path.insert(0, str(_find_module_dir()))
    gb = importlib.import_module("groq_budget")
    return gb, gb.Ledger()


def _describe(request: httpx.Request) -> tuple[str, int] | None:
    """(model, estimated tokens) for a Groq chat completion request, else None."""
    if request.url.host != HOST or not request.url.path.endswith("/chat/completions"):
        return None
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        body = {}
    model = str(body.get("model") or "unknown")
    chars = sum(
        len(str(m.get("content", ""))) for m in body.get("messages", []) if isinstance(m, dict)
    )
    completion = int(
        body.get("max_completion_tokens") or body.get("max_tokens") or DEFAULT_COMPLETION
    )
    return model, int(chars / 3.5) + completion


def _record(response: httpx.Response, model: str, est: int) -> None:
    led = _state["ledger"]
    if response.status_code == 429:
        led.observe_429(response.text, model)
        return
    if response.status_code != 200:
        return  # not counted by Groq
    try:
        usage = response.json().get("usage") or {}
        total = int(usage["total_tokens"])
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    except (ValueError, KeyError, TypeError, httpx.ResponseNotRead):
        total, cached = est, None  # streamed or unparsable: charge the estimate rather than nothing
    led.record(PROJECT, model, total, source=Path(sys.argv[0]).name, cached_tokens=cached)


def _deny_message(d: Any, model: str) -> str:
    return f"[{d.reason}] {d.detail} (project={PROJECT}, model={model}); nothing was sent"


def _wait_sync(model: str, est: int) -> None:
    led = _state["ledger"]
    deadline = time.monotonic() + WAIT_S
    while True:
        d = led.check(PROJECT, model, est)
        if d.allowed:
            return
        if time.monotonic() >= deadline:
            raise BudgetWaitTimeout(_deny_message(d, model))
        time.sleep(min(POLL_S, max(1.0, d.retry_after_s or POLL_S)))


async def _wait_async(model: str, est: int) -> None:
    led = _state["ledger"]
    deadline = time.monotonic() + WAIT_S
    while True:
        d = led.check(PROJECT, model, est)
        if d.allowed:
            return
        if time.monotonic() >= deadline:
            raise BudgetWaitTimeout(_deny_message(d, model))
        await asyncio.sleep(min(POLL_S, max(1.0, d.retry_after_s or POLL_S)))


def install(ledger: Any = None, module: Any = None) -> None:
    """Patch httpx once per process. `ledger`/`module` are injectable for tests.

    GROQ_BUDGET_SEPARATE_ORG=1 turns the gate off. Set it ONLY after the benchmark key has been moved to
    its own Groq org: that org has its own pools, so counting its tokens against the shared pool would
    just block benchmark runs for no reason.
    """
    if os.environ.get("GROQ_BUDGET_SEPARATE_ORG") == "1":
        return
    if _state["installed"]:
        return
    if ledger is None:
        module, ledger = _load_ledger()
    _state.update(ledger=ledger, module=module)

    orig_send, orig_asend = httpx.Client.send, httpx.AsyncClient.send

    def send(self: httpx.Client, request: httpx.Request, **kw: Any) -> httpx.Response:
        info = _describe(request)
        if info is None:
            return orig_send(self, request, **kw)
        _wait_sync(*info)
        response = orig_send(self, request, **kw)
        _record(response, *info)
        return response

    async def asend(self: httpx.AsyncClient, request: httpx.Request, **kw: Any) -> httpx.Response:
        info = _describe(request)
        if info is None:
            return await orig_asend(self, request, **kw)
        await _wait_async(*info)
        response = await orig_asend(self, request, **kw)
        _record(response, *info)
        return response

    httpx.Client.send = send  # type: ignore[method-assign]
    httpx.AsyncClient.send = asend  # type: ignore[method-assign]
    _state["installed"] = True
    _state["orig"] = (orig_send, orig_asend)


def uninstall() -> None:
    """Undo install() (tests)."""
    if _state["installed"]:
        httpx.Client.send, httpx.AsyncClient.send = _state["orig"]  # type: ignore[method-assign]
        _state.update(installed=False, ledger=None, module=None)
