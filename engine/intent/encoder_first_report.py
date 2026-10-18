"""Render reports/intent/encoder_first/README.md from result.json (Amendment 14). Every number sits in a METRICS block; the verdict
is computed from the criteria fixed in the amendment, not written by hand.

    python -m engine.intent.encoder_first_report --result reports/intent/encoder_first/result.json --out reports/intent/encoder_first/README.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def passes(rate: float, lower: float, point_min: float = 0.95, lower_min: float = 0.90) -> bool:
    return rate >= point_min and lower >= lower_min


def block(name: str, body: str) -> str:
    return f"<!-- METRICS:START:{name} -->\n{body}\n<!-- METRICS:END -->"


def render(res: dict) -> str:
    s, sf = res["sealed_set"], res["safety_stratum"]
    r1 = s["R1"]
    cap = r1["capacity"]
    f = r1["routed"]
    lines = [
        "# Encoder-first routing: capacity multiplier and urgent-review recall (spec Amendment 14, S27)",
        "",
        "Registered in `docs/specs/review-intent.md` Amendment 14 (commit ed74662) before any router output or safety-stratum LLM row existed. "
        "Served-model decisions only (static-int8 ONNX, committed thresholds, sha256 "
        f"`{res['thresholds_sha256'][:16]}...`). Labels are SILVER (four-judge panel). The safety stratum is ENRICHED and keyword-mined: "
        "it is never a base rate and is never pooled with the Amendment 5 sealed set. Feature flag `ENABLE_ENCODER_FIRST` stays OFF.",
        "",
        "## Current capacity against the pricing quotas (`eval/results/capacity_model.json`, committed)",
        "",
        block(
            "ef_capacity_now",
            f"binding pool: {cap['binding_tier']} (gpt-oss-120b); tokens per extraction {cap['tokens_per_extraction']}; traffic mix {cap['traffic_mix']}\n\n"
            "| | per day | per month (30 d) |\n|---|---|---|\n"
            f"| hard limit (200K tokens/day/model) | {cap['ceiling_per_day_hard_limit']} | {cap['per_month_hard_limit']} |\n"
            f"| 100K working budget | {cap['ceiling_per_day_100k_budget']} | {cap['per_month_100k_budget']} |\n"
            f"| Starter needs (5,000/month) | {cap['quota_need_per_day']['starter_5000']} | 5000 |\n"
            f"| Growth needs (25,000/month) | {cap['quota_need_per_day']['growth_25000']} | 25000 |\n\n"
            f"multiplier needed: Starter {cap['multiplier_needed']['starter_hard']} (hard limit) / {cap['multiplier_needed']['starter_100k']} (100K budget); "
            f"Growth {cap['multiplier_needed']['growth_hard']} / {cap['multiplier_needed']['growth_100k']}",
        ),
        "",
        "## Routing share and the capacity multiplier (Amendment 5 sealed set, 757 ids)",
        "",
        block(
            "ef_routing_share",
            f"R1 routes {f['k']} of {f['n']} = {f['rate']} (Wilson 95 {f['wilson95']}); R2 (sensitivity only) routes "
            f"{s['R2']['routed']['k']} = {s['R2']['routed']['rate']}.\n"
            f"reasons (overlapping): {r1['reason_counts_overlapping']}\n\n"
            "| open rate o | multiplier | extractions/day hard | /day 100K | /month hard | /month 100K | Starter (hard/100K) | Growth (hard/100K) |\n|---|---|---|---|---|---|---|---|\n"
            + "\n".join(
                f"| {o} | {v['multiplier']} | {v['per_day_hard_limit']} | {v['per_day_100k_budget']} | {v['per_month_hard_limit']} | {v['per_month_100k_budget']} | "
                f"{v['starter_met_hard']}/{v['starter_met_100k']} | {v['growth_met_hard']}/{v['growth_met_100k']} |"
                for o, v in cap["by_open_rate"].items()
            ),
        ),
        "",
        "## Urgent-review and needs-action recall (criteria C1 to C4)",
        "",
    ]
    c1 = sf["C1_routing_recall"]["R1"]
    c3 = s["C3_needs_action_yes"]["routing_recall_R1"]
    c2 = s["C2_urgency_high"]["routing_recall_R1"]
    rows = [
        f"| C1 safety stratum test, gold urgency=high, R1 routing recall (groups) | {c1['independent_positive_groups']} groups | {c1['recall']} | {c1['recall_wilson95']} | "
        f"{passes(c1['recall'], c1['recall_wilson95'][0])} |",
        f"| C2 Amendment 5 set, gold urgency=high (reported only) | {c2['n']} items | {c2['rate']} | {c2['wilson95']} | n/a (insufficient data) |",
        f"| C3 Amendment 5 set, gold needs_action=yes, R1 routing recall | {c3['n']} items | {c3['rate']} | {c3['wilson95']} | {passes(c3['rate'], c3['wilson95'][0])} |",
    ]
    c4_ok: bool | None = None
    llm = sf.get("llm")
    if llm:
        c4 = llm["C4_routing_recall_on_llm_caught"]["R1"]
        c4_ok = c4["recall"] >= 0.97
        rows.append(
            f"| C4 safety test, urgent items the LLM path caught, R1 routing recall (groups) | {c4['independent_positive_groups']} groups ({llm['llm_caught_items']} items) | "
            f"{c4['recall']} | {c4['recall_wilson95']} | {c4_ok} |"
        )
    lines += [
        block(
            "ef_criteria",
            "| criterion | n | recall | Wilson 95 | passes (point >= 0.95 and lower >= 0.90; C4 point >= 0.97) |\n|---|---|---|---|---|\n"
            + "\n".join(rows),
        ),
        "",
    ]
    if llm:
        mp = llm["matched_precision_table"]
        lines += [
            "## Matched-precision reading on the same safety test items (groups)",
            "",
            block(
                "ef_matched_precision",
                f"LLM rows {llm['rows']}; empty or unparseable or missing {llm['empty_or_unparseable_or_missing']}; ended at the token cap {llm['ended_at_token_cap']}\n\n"
                "| flag | recall | precision |\n|---|---|---|\n"
                + "\n".join(
                    f"| {k} | {v['recall']} ({v['recall_wilson95']}) | {v['precision']} ({v['precision_wilson95']}) |"
                    for k, v in mp.items()
                ),
            ),
            "",
        ]
    ok1 = passes(c1["recall"], c1["recall_wilson95"][0])
    ok3 = passes(c3["rate"], c3["wilson95"][0])
    failing = [n for n, ok in (("C1", ok1), ("C3", ok3), ("C4", c4_ok)) if ok is not True]
    verdict = (
        "may be enabled behind its flag"
        if not failing
        else f"must NOT be enabled (not met: {', '.join(failing) or 'none'}; C4 None = not measured)"
    )
    lines += [
        "## Verdict under the registered rule",
        "",
        block("ef_verdict", f"encoder-first R1 {verdict}"),
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    a.out.write_text(render(json.loads(a.result.read_text(encoding="utf-8"))), encoding="utf-8")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
