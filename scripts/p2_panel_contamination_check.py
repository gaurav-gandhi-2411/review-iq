"""Session 8 P2: checks whether the consensus panel used to adjudicate abstentions (PR #140,
Session 7 P2) shares model identity/family with review-iq's own production models, and if so,
re-derives the abstention classification using only the disjoint-family judge.

Verified fact (from eval/results/latest.json + eval/consensus/results/consensus_summary.json,
both already committed): the panel's `openai/gpt-oss-120b` judge is not merely "same family" as
production -- it IS `groq_model_large`, the exact model ID review-iq's own tiered router
escalates to. eval/consensus/panel.py's own docstring documents the correct exclusion rule
("do not use review-iq's own production tiered-router large-tier extraction model as a judge --
a real conflict of interest") and correctly applied it to the model that held that role UNTIL
production migrated away from llama-3.3-70b-versatile -- the rule was never re-applied to
whichever model took over the same production role (openai/gpt-oss-120b), which is exactly the
self-judging conflict the rule exists to prevent, recurring under a new name.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_consensus_by_id() -> dict[str, dict]:
    by_id = {}
    with (REPO_ROOT / "eval" / "consensus" / "results" / "consensus_labels.jsonl").open(
        encoding="utf-8"
    ) as f:
        for line in f:
            entry = json.loads(line)
            if entry["mode"] == "validate":
                by_id[entry["id"]] = entry
    return by_id


def main() -> None:
    results = json.loads(
        (REPO_ROOT / "eval" / "results" / "latest.json").read_text(encoding="utf-8")
    )
    consensus_by_id = load_consensus_by_id()

    production_large_model = results["groq_model_large"]
    production_small_model = results["groq_model_small"]
    print(f"Production models: small={production_small_model}, large={production_large_model}")

    summary = json.loads(
        (REPO_ROOT / "eval" / "consensus" / "results" / "consensus_summary.json").read_text(
            encoding="utf-8"
        )
    )
    panel_ids = [j["id"] for j in summary["active_panel"]]
    print(f"Active consensus panel: {panel_ids}")
    contaminated = [
        pid for pid in panel_ids if pid in (production_large_model, production_small_model)
    ]
    print(f"Panel judges that ARE a production model (exact ID match): {contaminated}")
    disjoint_judges = [pid for pid in panel_ids if pid not in contaminated]
    print(f"Disjoint-family judges remaining: {disjoint_judges}")
    if len(disjoint_judges) != 1:
        raise SystemExit(
            f"expected exactly 1 disjoint judge for a single-rater re-check, got {disjoint_judges}"
        )
    disjoint_judge = disjoint_judges[0]

    HEDGE_VALUE = {"buy_again": None, "sentiment": "mixed"}
    for field in ("buy_again", "sentiment"):
        hedge_value = HEDGE_VALUE[field]
        real_hedges = []
        for fx in results["fixtures"]:
            for f in fx["fields"]:
                if f["field"] != field or f["predicted"] != hedge_value:
                    continue
                if f["expected"] == hedge_value:
                    continue  # already-correct, not a real hedge -- see Session 7 P2
                real_hedges.append((fx["id"], fx["language"], f["expected"]))

        print(
            f"\n=== {field}: {len(real_hedges)} real hedges, disjoint-judge ({disjoint_judge}) re-check ==="
        )
        disjoint_decidable = 0
        disjoint_also_hedges = 0
        for fid, lang, expected in real_hedges:
            vote = consensus_by_id[fid]["consensus"][field]["votes"][disjoint_judge]
            contaminated_vote = consensus_by_id[fid]["consensus"][field]["votes"].get(
                contaminated[0] if contaminated else None
            )
            matches_expected = vote == expected
            if vote == hedge_value:
                disjoint_also_hedges += 1
                verdict = "disjoint judge ALSO hedges -- genuinely ambiguous"
            elif matches_expected:
                disjoint_decidable += 1
                verdict = "disjoint judge matches ORIGINAL ground truth -- decidable, headroom recoverable"
            else:
                disjoint_decidable += 1
                verdict = "disjoint judge commits to a DIFFERENT answer than ground truth -- decidable but disputed"
            print(
                f"  {fid:35s} lang={lang:5s} expected={str(expected):8s} "
                f"{disjoint_judge}={str(vote):8s} contaminated_judge_vote={str(contaminated_vote):8s} -> {verdict}"
            )
        n = len(real_hedges)
        print(
            f"  TOTAL: {disjoint_decidable}/{n} decidable per disjoint judge alone "
            f"({disjoint_decidable / n:.1%}), {disjoint_also_hedges}/{n} genuinely ambiguous "
            f"even to the disjoint judge ({disjoint_also_hedges / n:.1%})"
        )


if __name__ == "__main__":
    main()
