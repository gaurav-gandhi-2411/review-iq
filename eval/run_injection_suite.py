"""Session 13 P4d: run the injection suite against the two-layer defense, report pass rate.

"Pass" = the case was flagged as suspicious BEFORE extraction, by the regex layer
(app/core/sanitize.py::detect_prompt_injection) or the model layer
(app/core/injection_guard.py::classify_injection_risk) or both. Reports which layer(s) caught
each case, so a family caught only by one layer is distinguishable from one genuinely caught by
neither -- the honest failure mode this suite exists to surface.

Cost: 40 real classifier calls against meta-llama/llama-prompt-guard-2-86m's own separate
500K tokens/day budget (does not touch the extraction models' quota) -- see eval/injection_suite.py
module docstring for why this measures pre-filter detection, not full extraction-output
correctness.

Usage:
    uv run python eval/run_injection_suite.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.injection_guard import INJECTION_GUARD_THRESHOLD  # noqa: E402
from app.core.sanitize import detect_prompt_injection  # noqa: E402
from groq import AsyncGroq  # noqa: E402

from eval.injection_suite import CASES, FAMILIES  # noqa: E402

OUT_PATH = ROOT / "eval" / "results" / "injection_suite_n40.json"


async def _guard_score(client: AsyncGroq, text: str) -> float | None:
    try:
        response = await client.chat.completions.create(
            model="meta-llama/llama-prompt-guard-2-86m",
            messages=[{"role": "user", "content": text}],
            timeout=10.0,
        )
        return float(response.choices[0].message.content or "0")
    except Exception:
        return None


async def main() -> None:
    api_key = os.environ["GROQ_API_KEY"]
    client = AsyncGroq(api_key=api_key)

    per_case: list[dict[str, object]] = []
    for case in CASES:
        regex_flagged = detect_prompt_injection(case.text)
        guard_score = await _guard_score(client, case.text)
        # Fail-closed semantics apply here too: a None score (classifier error) counts as
        # the guard layer flagging the case, matching app/core/injection_guard.py's real
        # runtime behavior -- not treated as a pass-through in this measurement either.
        guard_flagged = guard_score is None or guard_score >= INJECTION_GUARD_THRESHOLD
        caught = regex_flagged or guard_flagged
        per_case.append(
            {
                "id": case.id,
                "family": case.family,
                "regex_flagged": regex_flagged,
                "guard_score": guard_score,
                "guard_flagged": guard_flagged,
                "caught_by_two_layer_defense": caught,
            }
        )
        await asyncio.sleep(0.1)

    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in per_case:
        by_family[row["family"]].append(row)  # type: ignore[index]

    family_summary = {}
    for family in FAMILIES:
        rows = by_family[family]
        caught = sum(1 for r in rows if r["caught_by_two_layer_defense"])
        regex_only = sum(1 for r in rows if r["regex_flagged"] and not r["guard_flagged"])
        guard_only = sum(1 for r in rows if r["guard_flagged"] and not r["regex_flagged"])
        both = sum(1 for r in rows if r["regex_flagged"] and r["guard_flagged"])
        family_summary[family] = {
            "n": len(rows),
            "caught": caught,
            "pass_rate": caught / len(rows) if rows else 0.0,
            "caught_by_regex_only": regex_only,
            "caught_by_guard_only": guard_only,
            "caught_by_both": both,
            "missed_ids": [r["id"] for r in rows if not r["caught_by_two_layer_defense"]],
        }

    total_caught = sum(1 for r in per_case if r["caught_by_two_layer_defense"])
    result = {
        "n_cases": len(per_case),
        "threshold": INJECTION_GUARD_THRESHOLD,
        "overall_pass_rate": total_caught / len(per_case),
        "per_family": family_summary,
        "per_case": per_case,
    }
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"Written: {OUT_PATH}\n")
    print(f"Overall: {total_caught}/{len(per_case)} ({result['overall_pass_rate']:.1%})\n")
    for family in FAMILIES:
        s = family_summary[family]
        print(
            f"  {family:20s} {s['caught']}/{s['n']} ({s['pass_rate']:.1%}) -- "
            f"regex-only={s['caught_by_regex_only']}, guard-only={s['caught_by_guard_only']}, "
            f"both={s['caught_by_both']}, missed={s['missed_ids']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
