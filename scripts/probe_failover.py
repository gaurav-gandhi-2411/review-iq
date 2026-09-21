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

Session 15c amendment (D4, GG decision 2026-09-20) -- three-state result per path:
  PASS            live call succeeded and parsed.
  FAIL            configured but broken (call error, bad schema, privacy check, or a
                  PARTIAL config such as a key without a model). Always exits 1.
  NOT_CONFIGURED  the secret/var is absent, so the path is UNPROTECTED and UNTESTED.
                  Never printed or counted as PASS.

GG only configures GEMINI_API_KEY; the OpenRouter/secondary path is deliberately left
unconfigured. Before this amendment an unconfigured path was a plain FAIL, so the job was
red every night and the signal was useless; a silent skip would be just as useless. So an
unconfigured path is instead always VISIBLE -- on every run it produces (1) a
`[NOT CONFIGURED]` console line, (2) a `::warning::` annotation, (3) a row in
$GITHUB_STEP_SUMMARY -- and its effect on the exit code is an explicit, reviewable choice:

  FAILOVER_ACKNOWLEDGED_UNCONFIGURED  comma/space-separated path names (`gemini`,
      `secondary`) whose absence is a deliberate decision (set in failover-probe.yml).

Exit code: 0 only if no path FAILed AND every NOT_CONFIGURED path is acknowledged.
Exit 1 if any path FAILed, or any NOT_CONFIGURED path is NOT acknowledged (e.g. a primary
GEMINI_API_KEY that was never set or was deleted). Acknowledged-but-unconfigured still
prints the warning/summary row every run; acknowledgement only stops it failing the job.

Machine-readable output: the last stdout line is `PROBE_SUMMARY {json}` with
`paths` ({name: PASS|FAIL|NOT_CONFIGURED}), `acknowledged` (names), `exit_code`. If
$GITHUB_OUTPUT is set, `states=<name>=<state>[ (acknowledged)], ...` is written for the
workflow's alert step to quote.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Fixed, deterministic test review — same input every run so a failure is attributable
# to the provider/path, never to input variance. Not PII, not a real customer review.
_PROBE_REVIEW_TEXT = (
    "The wireless earbuds arrived on time and the sound quality is great. "
    "Battery lasts all day. Would buy again."
)


ACK_ENV_VAR = "FAILOVER_ACKNOWLEDGED_UNCONFIGURED"


class ProbeState(StrEnum):
    """Three-state outcome of one failover path (see module docstring)."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass
class ProbeResult:
    """Outcome of exercising one failover path."""

    path: str
    state: ProbeState
    latency_ms: int
    detail: str

    @property
    def ok(self) -> bool:
        """True only for PASS -- NOT_CONFIGURED is never 'ok'."""
        return self.state is ProbeState.PASS


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
        return ProbeResult("gemini", ProbeState.NOT_CONFIGURED, 0, "GEMINI_API_KEY absent")

    prompt = await _build_probe_prompt()
    t0 = time.monotonic()
    try:
        extraction, tokens_in, tokens_out = await _call_gemini(prompt)
    except Exception as exc:  # noqa: BLE001 -- any failure here is a probe finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult("gemini", ProbeState.FAIL, latency_ms, f"{type(exc).__name__}: {exc}")
    latency_ms = int((time.monotonic() - t0) * 1000)

    if not isinstance(extraction, ReviewExtractionLLMOutput):
        return ProbeResult(
            "gemini", ProbeState.FAIL, latency_ms, "response did not parse to expected schema"
        )
    return ProbeResult(
        "gemini",
        ProbeState.PASS,
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
        has_key = bool(settings.secondary_provider_api_key)
        has_model = bool(settings.secondary_provider_model)
        if has_key != has_model:
            # Someone tried to configure this path and got it half-wrong: that is a broken
            # config (FAIL, not acknowledgeable), not a deliberate "not configured".
            missing = "SECONDARY_PROVIDER_MODEL" if has_key else "SECONDARY_PROVIDER_API_KEY"
            return ProbeResult(
                "secondary", ProbeState.FAIL, 0, f"partially configured: {missing} absent"
            )
        return ProbeResult(
            "secondary",
            ProbeState.NOT_CONFIGURED,
            0,
            "SECONDARY_PROVIDER_API_KEY / SECONDARY_PROVIDER_MODEL absent",
        )

    try:
        assert_privacy_safe(provider, context="nightly failover probe")
    except RuntimeError as exc:
        return ProbeResult("secondary", ProbeState.FAIL, 0, f"privacy check failed: {exc}")

    prompt = await _build_probe_prompt()
    t0 = time.monotonic()
    try:
        raw, tokens_in, tokens_out = await provider.complete(prompt, system_prompt=_SYSTEM_PROMPT)
        _parse_response(raw)  # raises on schema mismatch -- that IS a probe failure
    except Exception as exc:  # noqa: BLE001 -- any failure here is a probe finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult("secondary", ProbeState.FAIL, latency_ms, f"{type(exc).__name__}: {exc}")
    latency_ms = int((time.monotonic() - t0) * 1000)

    return ProbeResult(
        "secondary",
        ProbeState.PASS,
        latency_ms,
        f"model={settings.secondary_provider_model} tokens_in={tokens_in} tokens_out={tokens_out}",
    )


def parse_acknowledged(raw: str) -> set[str]:
    """Parse FAILOVER_ACKNOWLEDGED_UNCONFIGURED (comma/space-separated, case-insensitive)."""
    return {token for token in re.split(r"[,\s]+", raw.strip().lower()) if token}


def is_blocking(result: ProbeResult, acknowledged: set[str]) -> bool:
    """True if this result must make the probe exit 1.

    FAIL always blocks. NOT_CONFIGURED blocks unless its path is explicitly acknowledged.
    """
    if result.state is ProbeState.FAIL:
        return True
    return result.state is ProbeState.NOT_CONFIGURED and result.path not in acknowledged


def state_label(result: ProbeResult, acknowledged: set[str]) -> str:
    """Human-readable state; NOT_CONFIGURED always carries its acknowledgement status."""
    if result.state is not ProbeState.NOT_CONFIGURED:
        return result.state.value
    if result.path in acknowledged:
        return "NOT CONFIGURED (acknowledged)"
    return "NOT CONFIGURED (NOT acknowledged)"


def _not_configured_text(result: ProbeResult, acknowledged: set[str]) -> str:
    """The unmistakable one-line description of an unconfigured (unprotected) path."""
    if result.path in acknowledged:
        ack = f"acknowledged via {ACK_ENV_VAR}: does not fail the job"
    else:
        ack = f"NOT acknowledged in {ACK_ENV_VAR}: this FAILS the job"
    return (
        f"{result.path}  -- {result.detail}: this failover path is UNPROTECTED and untested ({ack})"
    )


def _escape_annotation(message: str) -> str:
    """Escape a GitHub workflow-command message (%, CR, LF)."""
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _cell(text: str) -> str:
    """Make text safe for a single markdown table cell."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def build_step_summary(results: list[ProbeResult], acknowledged: set[str], exit_code: int) -> str:
    """Markdown for $GITHUB_STEP_SUMMARY: one table row per path, with its state."""
    rows = [
        "### Failover probe",
        "",
        "| Path | State | Latency | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        rows.append(
            f"| {r.path} | {state_label(r, acknowledged)} | {r.latency_ms}ms | {_cell(r.detail)} |"
        )
    unprotected = [r.path for r in results if r.state is ProbeState.NOT_CONFIGURED]
    rows.append("")
    if unprotected:
        rows.append(
            f"**UNPROTECTED and untested failover path(s): {', '.join(unprotected)}.** "
            f"Acknowledged list ({ACK_ENV_VAR}): "
            f"{', '.join(sorted(acknowledged)) or '(empty)'}."
        )
    rows.append(f"Exit code: {exit_code}")
    return "\n".join(rows) + "\n"


def _append_to_env_file(env_var: str, text: str) -> None:
    """Append text to the file named by env_var, if set. Never raises (reporting only)."""
    target = os.environ.get(env_var, "")
    if not target:
        return
    try:
        with Path(target).open("a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        print(f"  (could not write ${env_var}: {exc})")


def _notify_slack(webhook_url: str, blocking: list[ProbeResult]) -> None:
    """Post a failure summary to Slack, reusing eval.slack_notify's send mechanism."""
    from eval.slack_notify import post

    lines = [f":red_circle: *{r.path}* ({r.state.value}): {r.detail}" for r in blocking]
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
    """Run the nightly failover probe. See the module docstring for exit-code semantics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slack-webhook", default="", help="Slack incoming webhook URL for failure alerts"
    )
    args = parser.parse_args()

    results = asyncio.run(run_probe())
    acknowledged = parse_acknowledged(os.environ.get(ACK_ENV_VAR, ""))

    print("=== Failover probe (Wave 1 Section F) ===")
    for r in results:
        if r.state is ProbeState.NOT_CONFIGURED:
            print(f"  [NOT CONFIGURED] {_not_configured_text(r, acknowledged)}")
        else:
            print(f"  [{r.state.value}] {r.path:<10} {r.latency_ms:>6}ms  {r.detail}")

    # A typo'd acknowledgement would otherwise silently fail to cover anything.
    unknown = sorted(acknowledged - {r.path for r in results})
    if unknown:
        print(
            f"::warning title=Unknown failover acknowledgement::{ACK_ENV_VAR} names unknown "
            f"path(s) {', '.join(unknown)} (ignored)"
        )
    for r in results:
        if r.state is ProbeState.NOT_CONFIGURED:
            message = _escape_annotation(_not_configured_text(r, acknowledged))
            print(f"::warning title=Failover path UNPROTECTED::{message}")

    blocking = [r for r in results if is_blocking(r, acknowledged)]
    exit_code = 1 if blocking else 0

    if blocking:
        print(f"\n{len(blocking)} of {len(results)} failover path(s) BLOCKING the job:")
        for r in blocking:
            print(f"  - {r.path}: {state_label(r, acknowledged)}: {r.detail}")
        if args.slack_webhook:
            try:
                _notify_slack(args.slack_webhook, blocking)
            except Exception as exc:  # noqa: BLE001 -- alerting failure must not mask the probe failure
                print(f"  (Slack notification also failed: {exc})")
    elif any(r.state is ProbeState.NOT_CONFIGURED for r in results):
        print("\nAll CONFIGURED failover paths OK; unconfigured path(s) acknowledged (see above).")
    else:
        print("\nAll failover paths OK.")

    _append_to_env_file("GITHUB_STEP_SUMMARY", build_step_summary(results, acknowledged, exit_code))
    states = ", ".join(f"{r.path}={state_label(r, acknowledged)}" for r in results)
    _append_to_env_file("GITHUB_OUTPUT", f"states={states}\n")
    summary = {
        "paths": {r.path: r.state.value for r in results},
        "acknowledged": sorted(acknowledged),
        "exit_code": exit_code,
    }
    print(f"PROBE_SUMMARY {json.dumps(summary, sort_keys=True)}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
