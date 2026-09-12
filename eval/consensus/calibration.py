"""Calibration harness -- run the judge panel against an unambiguous control set FIRST.

Per the task spec: before using the panel for real labeling, every judge model must be
checked against a small set of cases whose "correct" answer is undebatable by
construction (`control_set.json`, 16 items, each with a `why_unambiguous` note). Any
judge that disagrees with the obvious answer beyond a documented tolerance is DROPPED
from the panel for this run -- silently keeping a failing judge is explicitly
disallowed by the spec, not just discouraged.

Tolerance: MAX_ALLOWED_MISSES = 2 (out of 33 total field-level checks across the 16
items, ~94% required). A single miss is tolerated -- even a genuinely obvious case can
trip a model on a wording quirk or a strict-JSON-schema slip, and 1/33 is noise, not
signal. Two or more misses signals a systematic problem with that judge for this task,
not bad luck, so it's dropped rather than kept "on probation."
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from eval.consensus import panel

CONTROL_SET_PATH = Path(__file__).resolve().parent / "control_set.json"

MAX_ALLOWED_MISSES = 2


def load_control_set() -> list[dict[str, Any]]:
    """Load the calibration control set from control_set.json."""
    return json.loads(CONTROL_SET_PATH.read_text(encoding="utf-8"))


def check_item_against_expected(
    item: dict[str, Any], judge_output: dict[str, Any] | None
) -> list[str]:
    """Return the list of field names the judge got WRONG for one control-set item.

    `judge_output` is None if the judge errored/returned unparseable output for this
    item -- every expected field is counted as a miss in that case (a judge that can't
    even produce valid output on an unambiguous case has failed that check).
    """
    misses: list[str] = []

    expected: dict[str, Any] = item.get("expected", {})
    for field, want in expected.items():
        if judge_output is None:
            misses.append(field)
            continue
        got = judge_output.get(field)
        if isinstance(want, str) and isinstance(got, str):
            if want.strip().lower() != got.strip().lower():
                misses.append(field)
        elif got != want:
            misses.append(field)

    expected_list_contains: dict[str, str] = item.get("expected_list_contains", {})
    for field, substring in expected_list_contains.items():
        if judge_output is None:
            misses.append(field)
            continue
        got_list = judge_output.get(field) or []
        joined = " ".join(str(x).lower() for x in got_list)
        if substring.lower() not in joined:
            misses.append(field)

    return misses


async def _label_item_with_judge(client: Any, model_id: str, text: str) -> dict[str, Any] | None:
    try:
        raw = await panel.call_judge(client, model_id, text)
    except Exception:  # noqa: BLE001 -- a calibration call failing is data, not a crash
        return None
    parsed = panel.parse_judge_response(raw)
    return parsed.model_dump() if parsed else None


async def run_calibration(
    judge_models: tuple[dict[str, str], ...] = panel.JUDGE_MODELS,
) -> dict[str, Any]:
    """Run every judge in `judge_models` against the control set; return a full report.

    Returns:
        {
          "control_set_size": int,
          "total_checks_per_judge": int,
          "judges": {model_id: {"misses": int, "miss_details": [...], "passed": bool}},
          "active_panel": [model_id, ...],   # judges that passed calibration
          "dropped": [{"model_id": ..., "misses": int, "reason": ...}],
        }
    """
    control_set = load_control_set()
    client = panel.make_groq_client()

    per_judge_misses: dict[str, list[dict[str, Any]]] = {m["id"]: [] for m in judge_models}
    total_checks = 0

    for item in control_set:
        item_checks = len(item.get("expected", {})) + len(item.get("expected_list_contains", {}))
        total_checks += item_checks

        outputs = await asyncio.gather(
            *[_label_item_with_judge(client, m["id"], item["text"]) for m in judge_models]
        )
        for model_cfg, output in zip(judge_models, outputs, strict=True):
            missed_fields = check_item_against_expected(item, output)
            if missed_fields:
                per_judge_misses[model_cfg["id"]].append(
                    {"item_id": item["id"], "fields": missed_fields}
                )

    judges_report: dict[str, Any] = {}
    active_panel: list[str] = []
    dropped: list[dict[str, Any]] = []

    for model_cfg in judge_models:
        model_id = model_cfg["id"]
        miss_details = per_judge_misses[model_id]
        n_misses = sum(len(d["fields"]) for d in miss_details)
        passed = n_misses <= MAX_ALLOWED_MISSES
        judges_report[model_id] = {
            "misses": n_misses,
            "miss_details": miss_details,
            "passed": passed,
        }
        if passed:
            active_panel.append(model_id)
        else:
            dropped.append(
                {
                    "model_id": model_id,
                    "misses": n_misses,
                    "reason": (
                        f"{n_misses}/{total_checks} control-set field checks failed "
                        f"(tolerance: <= {MAX_ALLOWED_MISSES}) -- dropped from the active "
                        "panel for this labeling run per the calibration policy."
                    ),
                }
            )

    return {
        "control_set_size": len(control_set),
        "total_checks_per_judge": total_checks,
        "judges": judges_report,
        "active_panel": active_panel,
        "dropped": dropped,
    }


def main() -> None:
    report = asyncio.run(run_calibration())
    out_path = Path(__file__).resolve().parent / "results" / "calibration_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWritten: {out_path}")
    if report["dropped"]:
        print(f"\nDROPPED from active panel: {[d['model_id'] for d in report['dropped']]}")
    print(f"Active panel: {report['active_panel']}")


if __name__ == "__main__":
    main()
