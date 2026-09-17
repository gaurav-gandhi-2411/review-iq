"""Measure the two "Known gaps" claims on site/index.html against the 106-item held-out
corpus, honestly -- Session 14 P2.

Zero quota: reuses ground truth already produced by the 3-judge consensus panel when the
held-out corpus was built (eval/consensus/build_held_out_corpus.py, Session 9 P3) --
`eval/fixtures/_held_out_hindi_hinglish/hien-*.json`'s `ground_truth` and
`labeling_meta.agreement_per_field` -- and the model's own `as_deployed` predictions
already recorded in `eval/results/held_out_scoring_v2.json`. No new LLM calls.

Two claims measured:

1. "Short reviews (<10 words) occasionally miss fields." For every (short review, field)
   pair where the model's prediction is null/empty, checks whether the panel's ground
   truth is ALSO null/empty (CORRECT ABSTENTION -- the information genuinely isn't in the
   text, not a gap) or has a real value (REAL GAP -- a genuine miss). Separately reports
   WRONG-COMMITTED (model gave a confident value that doesn't match the panel) and
   CORRECT, since collapsing everything into "misses fields" hides that on short reviews
   the dominant failure mode is confidently wrong, not silently missing.

2. "Sarcastic Hinglish with backhanded compliments scores lower." Sarcasm/backhanded
   phrasing isn't a tagged field in this corpus, so this scans the actual review text for
   the two recognizable shapes examined case-by-case below (an explicit self-contradiction
   like "nice X (not really)", or an ironic idiom used to frame a genuinely mixed review)
   and reports how many of the 106 qualify, plus each one's actual model-vs-panel outcome.
   If n is too small for a statistical claim (bootstrap CIs need a real sample), this says
   so instead of computing one.

Usage:
    uv run python eval/analyze_known_gaps.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
SCORING_PATH = REPO_ROOT / "eval" / "results" / "held_out_scoring_v2.json"
OUTPUT_PATH = REPO_ROOT / "eval" / "results" / "known_gaps_n106.json"

SHORT_WORD_THRESHOLD = 10

FIELDS = (
    "product",
    "stars",
    "buy_again",
    "sentiment",
    "language",
    "topics",
    "competitor_mentions",
    "pros",
    "cons",
    "stars_inferred",
    "urgency",
    "feature_requests",
)

# Every review in the 106-item corpus was read directly (see this module's docstring) to
# find genuine sarcasm/backhanded-compliment shapes -- not a keyword heuristic, which would
# either miss idiomatic cases or false-positive on ordinary mixed reviews. These are the
# ones found: an explicit self-contradiction ("nice X (not really)") or an ironic idiom
# used to frame the review. Recorded here, not detected at run time, so this list is the
# actual audit trail -- re-justify any addition/removal in a commit message, not silently.
SARCASM_OR_BACKHANDED_IDS: tuple[str, ...] = (
    "hien-0006",  # ironic idiom ("if it runs, to the moon; if not, dies by night") framing a mixed review
    "hien-0059",  # "is best....... Not good... Bakwas" -- states then immediately negates
    "hien-0075",  # "Nice connectivity(utna bhi nahi)" = "nice connectivity (not really)"
)


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    return isinstance(v, list) and len(v) == 0


def _load_fixtures() -> list[dict[str, Any]]:
    return [
        json.loads(f.read_text(encoding="utf-8")) for f in sorted(FIXTURES_DIR.glob("hien-*.json"))
    ]


def analyze_short_reviews(
    fixtures: list[dict[str, Any]], records_by_id: dict[str, Any]
) -> dict[str, Any]:
    short = [
        (fx, len(fx["review_text"].split()))
        for fx in fixtures
        if len(fx["review_text"].split()) < SHORT_WORD_THRESHOLD
    ]

    buckets: dict[str, list[dict[str, Any]]] = {
        "correct": [],
        "correct_abstention": [],
        "real_gap": [],
        "wrong_committed": [],
    }
    per_field: dict[str, Counter[str]] = {f: Counter() for f in FIELDS}

    for fx, n_words in short:
        rec = records_by_id.get(fx["id"])
        if rec is None:
            continue
        predicted = rec["as_deployed"]["predicted"]
        gt = fx["ground_truth"]
        agreement = fx.get("labeling_meta", {}).get("agreement_per_field", {})

        for field in FIELDS:
            gt_val, pred_val = gt.get(field), predicted.get(field)
            gt_empty, pred_empty = _is_empty(gt_val), _is_empty(pred_val)
            entry = {
                "id": fx["id"],
                "field": field,
                "n_words": n_words,
                "text": fx["review_text"],
                "gt": gt_val,
                "pred": pred_val,
                "panel_agreement": agreement.get(field, "unknown"),
            }
            if pred_empty and gt_empty:
                bucket = "correct_abstention"
            elif pred_empty and not gt_empty:
                bucket = "real_gap"
            elif not pred_empty and gt_empty:
                bucket = "wrong_committed"
            else:
                bucket = "correct" if pred_val == gt_val else "wrong_committed"
            buckets[bucket].append(entry)
            per_field[field][bucket] += 1

    total = len(short) * len(FIELDS)
    return {
        "n_short_reviews": len(short),
        "short_review_word_threshold": SHORT_WORD_THRESHOLD,
        "total_field_checks": total,
        "counts": {k: len(v) for k, v in buckets.items()},
        "rates": {k: round(len(v) / total, 4) for k, v in buckets.items()} if total else {},
        # Abstention-only rate: of the cases where the model chose not to commit, how often
        # was that the right call (matches P2a's actual question -- not "did it miss info"
        # but "when it stayed silent, was silence correct").
        "abstention_correctness_rate": (
            round(
                len(buckets["correct_abstention"])
                / (len(buckets["correct_abstention"]) + len(buckets["real_gap"])),
                4,
            )
            if (buckets["correct_abstention"] or buckets["real_gap"])
            else None
        ),
        "real_gaps": buckets["real_gap"],
        "per_field": {f: dict(c) for f, c in per_field.items()},
    }


def analyze_sarcasm(
    fixtures: list[dict[str, Any]], records_by_id: dict[str, Any]
) -> dict[str, Any]:
    by_id = {fx["id"]: fx for fx in fixtures}
    cases = []
    for iid in SARCASM_OR_BACKHANDED_IDS:
        fx = by_id[iid]
        rec = records_by_id[iid]
        pred = rec["as_deployed"]["predicted"]
        gt = fx["ground_truth"]
        sentiment_match = pred.get("sentiment") == gt.get("sentiment")
        cases.append(
            {
                "id": iid,
                "text": fx["review_text"],
                "gt_sentiment": gt.get("sentiment"),
                "pred_sentiment": pred.get("sentiment"),
                "sentiment_match": sentiment_match,
                "gt_buy_again": gt.get("buy_again"),
                "pred_buy_again": pred.get("buy_again"),
                # "confident_wrong" = model committed to a value that mismatches the panel;
                # "abstained" = model said null/unclear; "correct" = matched the panel.
                "sentiment_outcome": (
                    "correct"
                    if sentiment_match
                    else "abstained"
                    if pred.get("sentiment") is None
                    else "confident_wrong"
                ),
            }
        )
    n = len(cases)
    return {
        "n_reviews_in_corpus": len(fixtures),
        "n_sarcastic_or_backhanded_found": n,
        "identification_method": (
            "direct manual read of all 106 review texts for two recognizable shapes "
            "(explicit self-contradiction, or an ironic idiom framing a mixed review) -- "
            "not a keyword heuristic"
        ),
        "statistically_meaningful": False,
        "why_not_meaningful": (
            f"n={n} out of 106 -- too small for a bootstrap CI or any accuracy/coverage "
            "claim. Reported as individual cases, not a rate."
        ),
        "cases": cases,
    }


def main() -> int:
    fixtures = _load_fixtures()
    scoring = json.loads(SCORING_PATH.read_text(encoding="utf-8"))
    records_by_id = {r["id"]: r for r in scoring["records"]}

    result: dict[str, Any] = {
        "source_fixtures": FIXTURES_DIR.relative_to(REPO_ROOT).as_posix(),
        "source_scoring": SCORING_PATH.relative_to(REPO_ROOT).as_posix(),
        "n_fixtures_total": len(fixtures),
        "short_reviews": analyze_short_reviews(fixtures, records_by_id),
        "sarcasm": analyze_sarcasm(fixtures, records_by_id),
    }
    OUTPUT_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT).as_posix()}")
    print(json.dumps(result["short_reviews"]["counts"], indent=2))
    print(f"abstention_correctness_rate: {result['short_reviews']['abstention_correctness_rate']}")
    print(f"sarcasm cases found: {result['sarcasm']['n_sarcastic_or_backhanded_found']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
