#!/usr/bin/env python
"""Structural guardrail eval for reply drafting.

Calls draft_reply on each fixture (using cassette replay in CI, live calls locally)
then runs structural guardrails on each output. Exit 0 = all pass; exit 1 = failures.

Cassettes must be pre-recorded once with EVAL_CASSETTE_MODE=record.
To record: EVAL_CASSETTE_MODE=record uv run python eval/reply/runner.py
To replay (CI default): EVAL_CASSETTE_MODE=replay uv run python eval/reply/runner.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Set cassette mode before importing anything that touches GroqProvider.
# Defaults to "replay" so this script is safe to run in CI without making network calls.
os.environ.setdefault("EVAL_CASSETTE_MODE", "replay")

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from app.core.reply.engine import draft_reply  # noqa: E402
from app.core.reply.guardrails import run_guardrails  # noqa: E402
from app.core.reply.schema import ReplyRequest, ReplyTone  # noqa: E402
from app.core.schemas import ReviewExtraction, Urgency  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_COL_WIDTH = 60


def _load_fixtures() -> list[dict]:
    paths = sorted(FIXTURES_DIR.glob("*.json"))
    if not paths:
        print("ERROR: No fixtures found in eval/reply/fixtures/", file=sys.stderr)
        sys.exit(1)
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def _build_request(fixture: dict) -> ReplyRequest:
    extraction = ReviewExtraction(
        product="unknown product",
        cons=fixture["pre_extracted_cons"],
        topics=fixture["pre_extracted_topics"],
        pros=[],
        feature_requests=[],
        competitor_mentions=[],
        language=fixture["language"],
        urgency=Urgency.low,
    )
    return ReplyRequest(
        text=fixture["review_text"],
        tone=ReplyTone(fixture["tone"]),
        brand_name=fixture.get("brand_name"),
        signature=fixture.get("signature"),
        extraction=extraction,
    )


async def _run_fixture(fixture: dict) -> dict:
    request = _build_request(fixture)
    try:
        draft, tokens_in, tokens_out = await draft_reply(request)
    except RuntimeError as exc:
        msg = str(exc)
        if "No cassette for key" in msg:
            return {
                "id": fixture["id"],
                "passed": False,
                "cassette_missing": True,
                "error": "Cassette not yet recorded. Run: EVAL_CASSETTE_MODE=record uv run python eval/reply/runner.py",
                "violations": [],
                "caveats": [],
                "reply_preview": "",
                "tokens_in": 0,
                "tokens_out": 0,
            }
        return {
            "id": fixture["id"],
            "passed": False,
            "cassette_missing": False,
            "error": f"draft_reply raised: {exc}",
            "violations": [],
            "caveats": [],
            "reply_preview": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }

    violations = run_guardrails(
        draft.reply_text,
        expected_language=fixture["language"],
        cons=fixture["pre_extracted_cons"],
        topics=fixture["pre_extracted_topics"],
    )
    expected_pass = fixture.get("expected_guardrail_pass", True)
    passed = (len(violations) == 0) == expected_pass

    return {
        "id": fixture["id"],
        "passed": passed,
        "cassette_missing": False,
        "error": None,
        "violations": violations,
        "caveats": draft.caveats,
        "language": draft.language,
        "model": draft.model_used,
        "reply_preview": draft.reply_text[:300],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


async def _run_all(fixtures: list[dict]) -> list[dict]:
    results = []
    for fixture in fixtures:
        result = await _run_fixture(fixture)
        results.append(result)
    return results


def _print_results(results: list[dict]) -> None:
    print()
    print("=" * _COL_WIDTH)
    print("  Review IQ — Reply Guardrail Eval")
    print("=" * _COL_WIDTH)
    cassette_missing = any(r.get("cassette_missing") for r in results)
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n[{status}] {result['id']}")
        if result.get("error"):
            print(f"       ERROR: {result['error']}")
        if result.get("violations"):
            for v in result["violations"]:
                print(f"       VIOLATION: {v}")
        if result.get("caveats"):
            for c in result["caveats"]:
                print(f"       CAVEAT:    {c}")
        if result.get("reply_preview"):
            print(f"       PREVIEW:   {result['reply_preview'][:120]!r}")

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print()
    print("=" * _COL_WIDTH)
    print(f"  Result: {passed}/{total} passed")
    if cassette_missing:
        print()
        print("  NOTE: Some cassettes are missing.")
        print("  Record them with:")
        print("    EVAL_CASSETTE_MODE=record uv run python eval/reply/runner.py")
    elif failed > 0:
        print(f"  FAILED: {failed} fixture(s) failed structural guardrails.")
    else:
        print("  All structural guardrails passed.")
    print("=" * _COL_WIDTH)
    print()


def main() -> int:
    fixtures = _load_fixtures()
    results = asyncio.run(_run_all(fixtures))
    _print_results(results)
    if any(not r["passed"] for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
