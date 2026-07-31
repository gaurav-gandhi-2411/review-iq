"""Nightly synthetic failover probe — Wave 1 Section F ("Reliability").

Context: `app/core/llm.py::extract_with_llm`'s org-key path had ZERO working failover
until this section (Section F) landed a real `SecondaryProvider` (OpenRouter, ZDR-
enforced — see `app/core/providers/secondary.py`'s docstring). "A config fix without
the probe is not a fix" — this script is that probe. It makes REAL live calls (no
mocks, no cassettes) against both failover paths on a fixed cadence and fails LOUDLY,
naming exactly which path broke, instead of silently degrading.

Two paths exercised, independently of each other and of the primary Groq path:
  1. Gemini fallback  — the demo/free-tier path's failover (`app.core.llm._call_gemini`).
  2. SecondaryProvider — the org-key path's ONLY failover today (OpenRouter, ZDR-only).

Each path either returns a valid `ReviewExtractionLLMOutput` or the probe records it
as a hard failure. Exit code is non-zero if either path fails — this is what should
page/alert someone, not a log line nobody reads.

Usage:
    uv run python scripts/probe_failover.py
    uv run python scripts/probe_failover.py --slack-webhook "$SLACK_WEBHOOK_URL"

Cost: 2 tiny live calls per invocation (~150-250 tokens total). At one nightly run,
this is a few hundred tokens/month — negligible against both providers' free/low
tiers (see PLAN.md Section F entry for the $/month estimate).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Fixed, deterministic test review — same input every run so a failure is attributable
# to the provider/path, never to input variance. Not PII, not a real customer review.
_PROBE_REVIEW_TEXT = (
    "The wireless earbuds arrived on time and the sound quality is great. "
    "Battery lasts all day. Would buy again."
)


@dataclass
class ProbeResult:
    """Outcome of exercising one failover path."""

    path: str
    ok: bool
    latency_ms: int
    detail: str


async def _build_probe_prompt() -> str:
    """Build the exact production prompt for the fixed probe review (English)."""
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm

    sanitized, _flagged = sanitize(_PROBE_REVIEW_TEXT)
    wrapped = wrap_for_llm(sanitized)
    return build_prompt(wrapped, "en")


async def probe_gemini() -> ProbeResult:
    """Exercise the Gemini fallback path (demo/free-tier failover) with a real live call."""
    from app.core.config import get_settings
    from app.core.llm import _call_gemini
    from app.core.schemas import ReviewExtractionLLMOutput

    settings = get_settings()
    if not settings.gemini_api_key:
        return ProbeResult("gemini", False, 0, "GEMINI_API_KEY is not configured")

    prompt = await _build_probe_prompt()
    t0 = time.monotonic()
    try:
        extraction, tokens_in, tokens_out = await _call_gemini(prompt)
    except Exception as exc:  # noqa: BLE001 -- any failure here is a probe finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult("gemini", False, latency_ms, f"{type(exc).__name__}: {exc}")
    latency_ms = int((time.monotonic() - t0) * 1000)

    if not isinstance(extraction, ReviewExtractionLLMOutput):
        return ProbeResult("gemini", False, latency_ms, "response did not parse to expected schema")
    return ProbeResult(
        "gemini",
        True,
        latency_ms,
        f"model={settings.gemini_model} tokens_in={tokens_in} tokens_out={tokens_out}",
    )


async def probe_secondary() -> ProbeResult:
    """Exercise the SecondaryProvider (OpenRouter, ZDR-only) path with a real live call.

    Calls `SecondaryProvider.complete()` directly rather than through
    `extract_with_llm()` -- isolates this path from tiered-routing/Groq-primary
    config, which would otherwise be attempted first and mask a broken secondary path.
    """
    from app.core.config import get_settings
    from app.core.llm import _SYSTEM_PROMPT, _parse_response
    from app.core.providers.base import assert_privacy_safe
    from app.core.providers.secondary import SecondaryProvider

    settings = get_settings()
    provider = SecondaryProvider(
        api_key=settings.secondary_provider_api_key,
        model=settings.secondary_provider_model,
    )
    if not provider.is_configured:
        return ProbeResult(
            "secondary",
            False,
            0,
            "SECONDARY_PROVIDER_API_KEY / SECONDARY_PROVIDER_MODEL not configured",
        )

    try:
        assert_privacy_safe(provider, context="nightly failover probe")
    except RuntimeError as exc:
        return ProbeResult("secondary", False, 0, f"privacy check failed: {exc}")

    prompt = await _build_probe_prompt()
    t0 = time.monotonic()
    try:
        raw, tokens_in, tokens_out = await provider.complete(prompt, system_prompt=_SYSTEM_PROMPT)
        _parse_response(raw)  # raises on schema mismatch -- that IS a probe failure
    except Exception as exc:  # noqa: BLE001 -- any failure here is a probe finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult("secondary", False, latency_ms, f"{type(exc).__name__}: {exc}")
    latency_ms = int((time.monotonic() - t0) * 1000)

    return ProbeResult(
        "secondary",
        True,
        latency_ms,
        f"model={settings.secondary_provider_model} tokens_in={tokens_in} tokens_out={tokens_out}",
    )


def _notify_slack(webhook_url: str, results: list[ProbeResult]) -> None:
    """Post a failure summary to Slack, reusing eval.slack_notify's send mechanism."""
    from eval.slack_notify import post

    failed = [r for r in results if not r.ok]
    lines = [f":red_circle: *{r.path}*: {r.detail}" for r in failed]
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":red_circle: Failover probe FAILED",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            },
        ]
    }
    post(webhook_url, payload)


async def run_probe() -> list[ProbeResult]:
    """Run both failover-path probes concurrently and return their results."""
    gemini_result, secondary_result = await asyncio.gather(probe_gemini(), probe_secondary())
    return [gemini_result, secondary_result]


def main() -> int:
    """Run the nightly failover probe. Exit 0 if both paths succeed, 1 otherwise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slack-webhook", default="", help="Slack incoming webhook URL for failure alerts"
    )
    args = parser.parse_args()

    results = asyncio.run(run_probe())

    print("=== Failover probe (Wave 1 Section F) ===")
    for r in results:
        status = "OK" if r.ok else "FAIL"
        print(f"  [{status}] {r.path:<10} {r.latency_ms:>6}ms  {r.detail}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} of {len(results)} failover path(s) FAILED:")
        for r in failed:
            print(f"  - {r.path}: {r.detail}")
        if args.slack_webhook:
            try:
                _notify_slack(args.slack_webhook, results)
            except Exception as exc:  # noqa: BLE001 -- alerting failure must not mask the probe failure
                print(f"  (Slack notification also failed: {exc})")
        return 1

    print("\nAll failover paths OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
