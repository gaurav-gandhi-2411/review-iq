"""Standalone cassette recorder — OpenRouter (primary) -> Gemini (secondary) fallback.

Context (2026-07-30, Wave 1 Section B): the 83 new consensus-grown fixtures had no
recorded cassettes. Recording them the standard way (`EVAL_CASSETTE_MODE=record
uv run python -m eval.runner --routed`) calls the REAL production `GROQ_API_KEY` —
attempting that exhausted Groq's `llama-3.3-70b-versatile` daily token budget
(99,770/100,000) mid-run, on the same key Cloud Run's live service uses. That must
not happen again this session (see the WIP checkpoint commit for the incident).

This script is a ONE-OFF, NOT wired into CI, and does NOT call GROQ_API_KEY at all.
It fills the remaining cassette gaps via a substitute provider so the eval-replay gate
has full coverage, while recording provenance so nobody mistakes these entries for a
clean Groq measurement. A real Groq re-recording pass should replace these once the
daily TPD budget window resets — see eval/cassettes/cassette_provenance.json and
docs/architecture/adr/0003-cassette-provenance-during-groq-quota-exhaustion.md.

Mechanism (must exactly mirror the real pipeline or the cassette key won't match):
  - Reuses the actual prompt-construction code: app.core.sanitize.{sanitize,wrap_for_llm},
    app.core.prompts.build_prompt, app.core.llm._SYSTEM_PROMPT.
  - Reuses app.core.providers.groq._make_cassette_key (the exact key function) --
    never reimplemented.
  - Reuses app.core.router._parse_response and app.core.routing_policy.escalation_triggers
    to decide, exactly as the real router does, whether a fixture needs ONLY a small-tier
    cassette or also a large-tier one (choose_tier() always starts on "small"; escalation
    is conditional on the small-tier response's schema validity / confidence / signal
    mismatch -- see routing_policy.py).
  - Reuses app.core.providers.cassette.{replay,record} for all reads/writes to
    eval/cassettes/cassettes.json -- this script never touches that file's schema or
    writes to it directly.

Model mapping (OpenRouter slugs confirmed live against GET /api/v1/models on 2026-07-30,
not guessed):
  - Groq "llama-3.1-8b-instant"      (small tier) -> OpenRouter "meta-llama/llama-3.1-8b-instruct"
  - Groq "llama-3.3-70b-versatile"   (large tier) -> OpenRouter "meta-llama/llama-3.3-70b-instruct"
Same nominal Llama model family/weights; different serving backend (OpenRouter routes to
whichever upstream host it has provisioned, e.g. DeepInfra) -- NOT guaranteed bit-identical
to what Groq's own hosting would return. That is exactly what the provenance file records.

Usage:
    uv run python scripts/record_cassettes_via_fallback.py            # record missing cassettes
    uv run python scripts/record_cassettes_via_fallback.py --dry-run   # report gaps, call nothing
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog
from dotenv import load_dotenv
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

log = structlog.get_logger(__name__)

PROVENANCE_PATH = REPO_ROOT / "eval" / "cassettes" / "cassette_provenance.json"
RECORDING_REASON = "groq TPD exhaustion during Section B fixture growth, 2026-07-30"

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# Confirmed live against OPENROUTER_MODELS_URL on 2026-07-30 -- see module docstring.
_OPENROUTER_MODEL_BY_TIER: dict[str, str] = {
    "small": "meta-llama/llama-3.1-8b-instruct",
    "large": "meta-llama/llama-3.3-70b-instruct",
}


@dataclass
class RecordingStats:
    """Tally of what happened during a recording run, for the final report."""

    openrouter_count: int = 0
    gemini_count: int = 0
    validation_retries: int = 0
    hard_failures: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (fixture_id, tier, error)
    already_present: int = 0


async def _verify_openrouter_models_available(api_key: str) -> None:
    """Assert both required OpenRouter model slugs are currently listed.

    Fails loudly and early rather than discovering a renamed/retired slug 80 calls in.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            OPENROUTER_MODELS_URL, headers={"Authorization": f"Bearer {api_key}"}
        )
        response.raise_for_status()
    available = {m["id"] for m in response.json().get("data", [])}
    missing = [slug for slug in _OPENROUTER_MODEL_BY_TIER.values() if slug not in available]
    if missing:
        raise RuntimeError(
            f"OpenRouter no longer lists required model slug(s): {missing}. "
            f"Re-query {OPENROUTER_MODELS_URL} and update _OPENROUTER_MODEL_BY_TIER."
        )


async def _call_openrouter(
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    *,
    retry: bool = False,
    timeout: float = 60.0,
) -> tuple[str, int, int]:
    """Call OpenRouter chat completions in JSON mode, mirroring GroqProvider.complete's contract.

    Returns (raw_text, tokens_in, tokens_out). Raises httpx.HTTPError on any network/HTTP failure
    -- callers treat that as "OpenRouter unavailable" and fall back to Gemini.
    """
    from app.core.providers.groq import _RETRY_SUFFIX

    prompt = user_prompt + (_RETRY_SUFFIX if retry else "")
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            OPENROUTER_CHAT_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
            },
        )
        response.raise_for_status()
        data = response.json()
    raw = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    return raw, tokens_in, tokens_out


async def _call_gemini_raw(
    system_prompt: str,
    user_prompt: str,
    *,
    gemini_api_key: str,
    gemini_model: str,
    retry: bool = False,
) -> tuple[str, int, int]:
    """Call Gemini and return the RAW response text (not the parsed model).

    Deliberately does NOT reuse app.core.llm._call_gemini: that function returns a
    *parsed* ReviewExtractionLLMOutput and discards the raw text, but a cassette entry
    must store the raw text (replay() returns it verbatim to the router, which parses it
    itself). It also has no retry-suffix parameter. Otherwise mirrors it exactly --
    same client construction, same JSON-mode config, same temperature=0.0.
    """
    from app.core.providers.groq import _RETRY_SUFFIX
    from google import genai
    from google.genai import types

    prompt = user_prompt + (_RETRY_SUFFIX if retry else "")
    client = genai.Client(api_key=gemini_api_key)
    response = await client.aio.models.generate_content(
        model=gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    meta = getattr(response, "usage_metadata", None)
    tokens_in = int(getattr(meta, "prompt_token_count", 0) or 0) if meta else 0
    tokens_out = int(getattr(meta, "candidates_token_count", 0) or 0) if meta else 0
    return response.text or "", tokens_in, tokens_out


def _validate(raw: str) -> bool:
    """Return True if *raw* parses into a valid ReviewExtractionLLMOutput.

    Reuses the router's own parser (markdown-fence stripping + pydantic validation) so
    "valid" here means exactly what the real pipeline would accept.
    """
    from app.core.router import _parse_response

    try:
        _parse_response(raw)
        return True
    except (ValidationError, json.JSONDecodeError):
        return False


async def _record_one(
    tier: str,
    fixture_id: str,
    system_prompt: str,
    user_prompt: str,
    key: str,
    *,
    openrouter_api_key: str,
    gemini_api_key: str,
    gemini_model: str,
    stats: RecordingStats,
    provenance: dict[str, dict[str, Any]],
) -> bool:
    """Record one missing cassette key via OpenRouter, falling back to Gemini.

    OpenRouter gets up to 2 attempts (initial + 1 retry) for schema-validation failures;
    an HTTP-level failure skips straight to Gemini (no point retrying a dead endpoint).
    Gemini gets the same 2-attempt allowance. Writes the cassette entry via
    app.core.providers.cassette.record() only on a validated response; otherwise reports
    the failure and writes nothing (never a broken cassette entry).

    Returns True on success, False on total failure (both providers exhausted).
    """
    from app.core.providers.cassette import record
    from eval.provenance import now_iso

    or_model = _OPENROUTER_MODEL_BY_TIER[tier]
    raw: str | None = None
    tokens_in = tokens_out = 0
    source = ""
    model_requested = ""

    for attempt in range(2):
        try:
            raw, tokens_in, tokens_out = await _call_openrouter(
                or_model, system_prompt, user_prompt, openrouter_api_key, retry=(attempt > 0)
            )
        except httpx.HTTPError as exc:
            log.warning("openrouter.http_error", fixture=fixture_id, tier=tier, error=str(exc))
            raw = None
            break
        if _validate(raw):
            source, model_requested = "openrouter", or_model
            break
        log.warning("openrouter.schema_invalid", fixture=fixture_id, tier=tier, attempt=attempt)
        stats.validation_retries += 1
        raw = None

    if raw is None:
        for attempt in range(2):
            try:
                raw, tokens_in, tokens_out = await _call_gemini_raw(
                    system_prompt,
                    user_prompt,
                    gemini_api_key=gemini_api_key,
                    gemini_model=gemini_model,
                    retry=(attempt > 0),
                )
            except Exception as exc:  # noqa: BLE001 -- any Gemini SDK failure is a fallback dead-end
                log.warning("gemini.error", fixture=fixture_id, tier=tier, error=str(exc))
                raw = None
                break
            if _validate(raw):
                source, model_requested = "gemini", gemini_model
                break
            log.warning("gemini.schema_invalid", fixture=fixture_id, tier=tier, attempt=attempt)
            stats.validation_retries += 1
            raw = None

    if raw is None:
        stats.hard_failures.append(
            (fixture_id, tier, f"both providers failed/invalid for key {key}")
        )
        return False

    record(key, raw, tokens_in, tokens_out)
    provenance[key] = {
        "source": source,
        "model_requested": model_requested,
        "recorded_at": now_iso(),
        "reason": RECORDING_REASON,
    }
    if source == "openrouter":
        stats.openrouter_count += 1
    else:
        stats.gemini_count += 1
    print(f"  [{fixture_id}] {tier} tier <- {source} ({model_requested})")
    return True


async def _process_fixture(
    fixture: dict[str, Any],
    *,
    openrouter_api_key: str,
    gemini_api_key: str,
    gemini_model: str,
    groq_model_small: str,
    groq_model_large: str,
    stats: RecordingStats,
    provenance: dict[str, dict[str, Any]],
    dry_run: bool,
) -> None:
    """Fill in whichever cassette keys this fixture is missing (small, and large if escalated).

    Mirrors app.core.router.route_extraction's decision path exactly: every fixture starts
    on the small model (choose_tier always returns "small" -- see routing_policy.py); the
    large-tier cassette is only needed if the small-tier response would trigger escalation.
    """
    from app.core.llm import _SYSTEM_PROMPT
    from app.core.prompts import build_prompt
    from app.core.providers.cassette import replay
    from app.core.providers.groq import _make_cassette_key
    from app.core.router import _parse_response
    from app.core.routing_policy import escalation_triggers
    from app.core.sanitize import sanitize, wrap_for_llm

    fixture_id = fixture["id"]
    lang = fixture.get("ground_truth", {}).get("language", "en")
    text = fixture["review_text"]
    sanitized, _ = sanitize(text)
    wrapped = wrap_for_llm(sanitized)
    user_prompt = build_prompt(wrapped, lang)
    system_prompt = _SYSTEM_PROMPT

    small_key = _make_cassette_key(groq_model_small, system_prompt, user_prompt)
    small_entry = replay(small_key)
    if small_entry is None:
        if dry_run:
            print(f"  [{fixture_id}] small tier -- MISSING (dry-run, not recorded)")
            stats.hard_failures.append((fixture_id, "small", "dry-run"))
            return
        recorded = await _record_one(
            "small",
            fixture_id,
            system_prompt,
            user_prompt,
            small_key,
            openrouter_api_key=openrouter_api_key,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            stats=stats,
            provenance=provenance,
        )
        if not recorded:
            return
        small_entry = replay(small_key)
        assert small_entry is not None
    else:
        stats.already_present += 1

    raw_small, _, _ = small_entry
    schema_valid = _validate(raw_small)
    extraction = _parse_response(raw_small) if schema_valid else None
    triggers = escalation_triggers(extraction, schema_valid=schema_valid)
    if not triggers:
        return

    large_key = _make_cassette_key(groq_model_large, system_prompt, user_prompt)
    if replay(large_key) is not None:
        stats.already_present += 1
        return

    if dry_run:
        print(
            f"  [{fixture_id}] large tier -- MISSING (dry-run, not recorded), triggers={triggers}"
        )
        stats.hard_failures.append((fixture_id, "large", "dry-run"))
        return

    await _record_one(
        "large",
        fixture_id,
        system_prompt,
        user_prompt,
        large_key,
        openrouter_api_key=openrouter_api_key,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        stats=stats,
        provenance=provenance,
    )


def _load_provenance() -> dict[str, dict[str, Any]]:
    if not PROVENANCE_PATH.exists():
        return {}
    text = PROVENANCE_PATH.read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else {}


def _save_provenance(provenance: dict[str, dict[str, Any]]) -> None:
    PROVENANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROVENANCE_PATH.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")


async def main() -> int:
    """Record every currently-missing cassette entry via OpenRouter -> Gemini fallback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report missing cassette gaps; call no provider."
    )
    args = parser.parse_args()

    from app.core.config import get_settings
    from eval.runner import FIXTURES_DIR, _collect_fixture_paths

    settings = get_settings()
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")
    gemini_api_key = settings.gemini_api_key
    gemini_model = settings.gemini_model

    if not args.dry_run:
        if not openrouter_api_key:
            print("ERROR: OPENROUTER_API_KEY is not set.")
            return 1
        if not gemini_api_key:
            print("ERROR: GEMINI_API_KEY is not set (required as the fallback-of-fallback).")
            return 1
        await _verify_openrouter_models_available(openrouter_api_key)

    stats = RecordingStats()
    provenance = _load_provenance()

    paths = _collect_fixture_paths(FIXTURES_DIR)
    print("=== Cassette recorder (OpenRouter -> Gemini fallback) ===")
    print(f"Fixtures: {len(paths)}  dry_run={args.dry_run}\n")

    for path in paths:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        await _process_fixture(
            fixture,
            openrouter_api_key=openrouter_api_key,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            groq_model_small=settings.groq_model_small,
            groq_model_large=settings.groq_model_large,
            stats=stats,
            provenance=provenance,
            dry_run=args.dry_run,
        )

    if not args.dry_run:
        _save_provenance(provenance)

    print("\n=== Summary ===")
    print(f"Already had a cassette: {stats.already_present}")
    print(f"Recorded via OpenRouter: {stats.openrouter_count}")
    print(f"Recorded via Gemini fallback: {stats.gemini_count}")
    print(f"Validation retries consumed: {stats.validation_retries}")
    print(f"Hard failures: {len(stats.hard_failures)}")
    for fixture_id, tier, err in stats.hard_failures:
        print(f"  - {fixture_id} ({tier}): {err}")

    return 1 if stats.hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
