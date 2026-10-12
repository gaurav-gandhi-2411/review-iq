"""Nightly synthetic failover probe — Wave 1 Section F ("Reliability").

Context: `app/core/llm.py::extract_with_llm`'s org-key path had ZERO working failover
until this section (Section F) landed a real secondary provider (built by
`app.core.llm._build_secondary`: Groq when SECONDARY_PROVIDER_KIND=groq, as in production;
OpenRouter ZDR-only otherwise — see `app/core/providers/secondary.py`). "A config fix without
the probe is not a fix" — this script is that probe. It makes REAL live calls (no
mocks, no cassettes) against both failover paths on a fixed cadence and fails LOUDLY,
naming exactly which path broke, instead of silently degrading.

One path is exercised, independently of the primary Groq path:
  Secondary provider — the ONLY failover, built by the production factory so KIND/model/key
  match Cloud Run (production: Groq openai/gpt-oss-120b on the shared-account key). The
  Gemini fallback was retired in S17 (ADR 0035), so there is no longer a Gemini probe.

Each path either returns a valid `ReviewExtractionLLMOutput` or the probe records it
as a hard failure. Exit code is non-zero if either path fails — this is what should
page/alert someone, not a log line nobody reads.

Usage:
    uv run python scripts/probe_failover.py
    uv run python scripts/probe_failover.py --slack-webhook "$SLACK_WEBHOOK_URL"

Cost: 1 live Groq call per invocation (~2.1K tokens measured 2026-10-12 incl. the
production system prompt) on the Groq FREE tier, shared-account key: $0. Never the
dedicated-org primary key.

Session 15c amendment (D4, GG decision 2026-09-20) -- three-state result per path:
  PASS            live call succeeded and parsed.
  FAIL            configured but broken (call error, bad schema, privacy check, or a
                  PARTIAL config such as a key without a model). Always exits 1.
  NOT_CONFIGURED  the secret/var is absent, so the path is UNPROTECTED and UNTESTED.
                  Never printed or counted as PASS.

Historical context (pre-S17): GG only configured GEMINI_API_KEY and the OpenRouter/secondary
path was deliberately left unconfigured. Before this amendment an unconfigured path was a plain FAIL, so the job was
red every night and the signal was useless; a silent skip would be just as useless. So an
unconfigured path is instead always VISIBLE -- on every run it produces (1) a
`[NOT CONFIGURED]` console line, (2) a `::warning::` annotation, (3) a row in
$GITHUB_STEP_SUMMARY -- and its effect on the exit code is an explicit, reviewable choice:

  FAILOVER_ACKNOWLEDGED_UNCONFIGURED  comma/space-separated path names (`secondary`) whose absence is a deliberate decision (set in failover-probe.yml).

Exit code: 0 only if no path FAILed AND every NOT_CONFIGURED path is acknowledged.
Exit 1 if any path FAILed, or any NOT_CONFIGURED path is NOT acknowledged (e.g. a failover key that was never set or was deleted). Acknowledged-but-unconfigured still
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


async def probe_secondary() -> ProbeResult:
    """Exercise the secondary failover path production actually builds, with a live call.

    The provider comes from `app.core.llm._build_secondary` -- the SAME factory
    `extract_with_llm` uses -- so SECONDARY_PROVIDER_KIND is honoured (production: a Groq
    secondary; legacy default: OpenRouter ZDR). Hard-wiring a provider class here made this
    probe test an OpenRouter path production did not use (S23: decorative control).
    Calls `provider.complete()` directly rather than through `extract_with_llm()` --
    isolates this path from tiered-routing/Groq-primary config, which would otherwise be
    attempted first and mask a broken secondary path.
    """
    from app.core.config import get_settings
    from app.core.llm import _SYSTEM_PROMPT, _build_secondary, _parse_response
    from app.core.providers.base import assert_privacy_safe

    settings = get_settings()
    has_key = bool(settings.secondary_provider_api_key)
    has_model = bool(settings.secondary_provider_model)
    if not (has_key and has_model):  # same gate as extract_with_llm's secondary branch
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

    provider = _build_secondary(settings)
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
        # Mask the key defensively: this detail reaches CI logs, the step summary and Slack.
        detail = f"{type(exc).__name__}: {exc}".replace(settings.secondary_provider_api_key, "***")
        return ProbeResult("secondary", ProbeState.FAIL, latency_ms, detail)
    latency_ms = int((time.monotonic() - t0) * 1000)

    return ProbeResult(
        "secondary",
        ProbeState.PASS,
        latency_ms,
        f"kind={settings.secondary_provider_kind} provider={type(provider).__name__} "
        f"model={settings.secondary_provider_model} tokens_in={tokens_in} tokens_out={tokens_out}",
    )


# Obviously-invalid primary key, applied in-process only (never written to env or Secret
# Manager): it makes the primary Groq path fail with a 401 so the REAL failover code runs.
_INVALID_PRIMARY_KEY = "invalid-primary-key-failover-probe"


async def probe_failover() -> ProbeResult:
    """Prove the production failover works end to end: primary fails -> secondary answers.

    Runs `app.core.llm.extract_with_llm` -- the production code path, tiered routing and all --
    with the primary Groq key replaced IN-PROCESS by an invalid value, so the primary 401s
    and the secondary must serve the extraction. PASS needs a schema-valid extraction whose
    model is the secondary model (not a primary one). The real primary key is never read or
    sent; the dedicated-org primary secret must not be mounted into this job.
    """
    from app.core.config import get_settings
    from app.core.llm import extract_with_llm
    from structlog.testing import capture_logs

    settings = get_settings()
    has_key = bool(settings.secondary_provider_api_key)
    has_model = bool(settings.secondary_provider_model)
    if not (has_key and has_model):
        if has_key != has_model:
            missing = "SECONDARY_PROVIDER_MODEL" if has_key else "SECONDARY_PROVIDER_API_KEY"
            return ProbeResult(
                "failover", ProbeState.FAIL, 0, f"partially configured: {missing} absent"
            )
        return ProbeResult(
            "failover",
            ProbeState.NOT_CONFIGURED,
            0,
            "SECONDARY_PROVIDER_API_KEY / SECONDARY_PROVIDER_MODEL absent",
        )

    settings.groq_api_key = _INVALID_PRIMARY_KEY
    prompt = await _build_probe_prompt()
    t0 = time.monotonic()
    events: list[dict[str, object]] = []
    try:
        # The model name cannot prove which backend served (production's secondary model id
        # equals a primary tier id), so require production's own `llm.failover` success event.
        with capture_logs() as events:
            extraction, model, _lat, tokens_in, tokens_out, _degraded = await extract_with_llm(
                prompt
            )
    except Exception as exc:  # noqa: BLE001 -- any failure here is a probe finding, not a crash
        latency_ms = int((time.monotonic() - t0) * 1000)
        # Production swallows the provider errors into log events; surface them here (masked).
        causes = "; ".join(
            f"{e.get('event')}: {e.get('error') or e.get('outcome')}"
            for e in events
            if e.get("log_level") in ("warning", "error") and e.get("event") != "llm.failover"
        )[:600]
        detail = f"{type(exc).__name__}: {exc} [{causes}]".replace(
            settings.secondary_provider_api_key, "***"
        )
        return ProbeResult("failover", ProbeState.FAIL, latency_ms, detail)
    latency_ms = int((time.monotonic() - t0) * 1000)

    failed_over = any(
        e.get("event") == "llm.failover" and e.get("outcome") == "success" for e in events
    )
    if not failed_over or model != settings.secondary_provider_model:
        return ProbeResult(
            "failover",
            ProbeState.FAIL,
            latency_ms,
            f"no secondary failover success recorded (model={model}, failover_event={failed_over})",
        )
    return ProbeResult(
        "failover",
        ProbeState.PASS,
        latency_ms,
        f"primary forced invalid in-process; served by secondary "
        f"kind={settings.secondary_provider_kind} model={model} "
        f"tokens_in={tokens_in} tokens_out={tokens_out} schema_ok={extraction is not None}",
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


PATHS_ENV_VAR = "PROBE_PATHS"


async def run_probe() -> list[ProbeResult]:
    """Run the probes named in $PROBE_PATHS (default: secondary) and return their results.

    `secondary` = one direct call through the production factory; `failover` = forced
    primary failure through `extract_with_llm`. Each costs one ~2K-token call; the GCP job
    runs `failover` only, which exercises the secondary too. An unknown name is a FAIL so a
    typo cannot silently turn the probe into a no-op.
    """
    probes = {"secondary": probe_secondary, "failover": probe_failover}
    names = [n for n in re.split(r"[,\s]+", os.environ.get(PATHS_ENV_VAR, "secondary")) if n]
    results: list[ProbeResult] = []
    for name in names or ["secondary"]:
        probe = probes.get(name)
        if probe is None:
            results.append(
                ProbeResult(name, ProbeState.FAIL, 0, f"unknown probe path in {PATHS_ENV_VAR}")
            )
        else:
            results.append(await probe())
    return results


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
        # stderr -> severity ERROR on Cloud Run; the GCP log-based alert keys on this marker.
        print(
            "FAILOVER_PROBE_FAILED " + "; ".join(f"{r.path}={r.state.value}" for r in blocking),
            file=sys.stderr,
        )
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
