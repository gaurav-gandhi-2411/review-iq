"""SYNTHETIC TESTBED, INTERIM validation harness for trend.py -- keyword-proxy topics/sentiment, NOT real extracted
data. `extractions.jsonl` (the real production-extraction contract trend.py's detector consumes)
takes ~70 minutes to generate via a background process and may not exist yet. This script proves
the detection ALGORITHM in trend.py works, using a cheap proxy built directly from
`reviews.jsonl` + each product's own `ground_truth.json` `keyword_regex`/`topic_keyword`:

  - topic proxy: the product's own planted `topic_keyword` if its `keyword_regex` matches the
    review text, else no topic at all. Products with no `keyword_regex` in ground truth (campaign
    and noise products) get no topic signal from any of their reviews.
  - sentiment proxy: "negative" if rating <= 2, "positive" if rating >= 4, else "neutral".

This is a stand-in for real extraction only -- do not read interim precision/recall as anything
more than "the phase/correlation/rise math in trend.py behaves correctly on these known patterns
when fed a topic/sentiment signal shaped roughly like what real extraction should produce".
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from trend import TrendFlag, _trend_parse_timestamp, _trend_ReviewRecord, scan_for_trends

REVIEWS_PATH = Path(__file__).resolve().parents[1] / "reviews.jsonl"
GROUND_TRUTH_PATH = Path(__file__).resolve().parents[1] / "ground_truth.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file into a list of dicts."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _sentiment_proxy(rating: str) -> str:
    """Crude rating -> sentiment proxy used ONLY for interim validation: rating<=2 negative,
    rating>=4 positive, else neutral."""
    rating_int = int(rating)
    if rating_int <= 2:
        return "negative"
    if rating_int >= 4:
        return "positive"
    return "neutral"


def build_interim_records(
    reviews: list[dict[str, Any]], ground_truth_products: dict[str, dict[str, Any]]
) -> list[_trend_ReviewRecord]:
    """Construct proxy annotated-review records directly from raw text + rating, standing in for
    real extracted topics/sentiment until `extractions.jsonl` is fully populated.

    Topic proxy is each product's OWN ground-truth `keyword_regex` -- products with no such field
    (campaign/noise products) contribute zero topic signal from any of their reviews, matching
    the real detector's expectation that only genuinely topic-bearing reviews carry a topic.
    """
    compiled: dict[str, re.Pattern[str]] = {}
    for product_id, gt in ground_truth_products.items():
        regex = gt.get("keyword_regex")
        if regex:
            compiled[product_id] = re.compile(regex, re.IGNORECASE)

    out: list[_trend_ReviewRecord] = []
    for review in reviews:
        product_id = review["product_id"]
        pattern = compiled.get(product_id)
        topics: tuple[str, ...] = ()
        if pattern is not None and pattern.search(review["text"]):
            topics = (ground_truth_products[product_id]["topic_keyword"],)
        out.append(
            _trend_ReviewRecord(
                review_id=review["review_id"],
                product_id=product_id,
                timestamp=_trend_parse_timestamp(review["timestamp"]),
                topics=topics,
                sentiment=_sentiment_proxy(review["rating"]),
            )
        )
    return out


def evaluate(
    flags: list[TrendFlag], ground_truth_products: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Compute precision/recall/false-positive breakdown against the 18-product ground truth,
    including the batch-defect discrimination check (spike shape must NOT be flagged as trend)."""
    flagged_products = {f.product_id for f in flags}
    all_products = set(ground_truth_products.keys())

    trend_planted = {
        pid
        for pid, gt in ground_truth_products.items()
        if gt.get("pattern") == "trend" and not gt.get("is_control")
    }
    negatives = all_products - trend_planted

    true_positives = trend_planted & flagged_products
    false_negatives = trend_planted - flagged_products
    false_positives = negatives & flagged_products
    true_negatives = negatives - flagged_products

    batch_defect_products = {
        pid
        for pid, gt in ground_truth_products.items()
        if gt.get("pattern") == "batch_defect" and not gt.get("is_control")
    }
    batch_defect_false_positives = batch_defect_products & flagged_products

    precision = (
        len(true_positives) / (len(true_positives) + len(false_positives))
        if (true_positives or false_positives)
        else float("nan")
    )
    recall = len(true_positives) / len(trend_planted) if trend_planted else float("nan")
    false_positive_rate = len(false_positives) / len(negatives) if negatives else float("nan")

    return {
        "trend_planted": sorted(trend_planted),
        "true_positives": sorted(true_positives),
        "false_negatives": sorted(false_negatives),
        "false_positives": sorted(false_positives),
        "true_negatives_count": len(true_negatives),
        "precision": precision,
        "recall": recall,
        "false_positive_rate": false_positive_rate,
        "n_products_total": len(all_products),
        "batch_defect_products": sorted(batch_defect_products),
        "batch_defect_false_positives": sorted(batch_defect_false_positives),
    }


def _fmt(value: float) -> str:
    """Format a float metric, or 'n/a' if it's NaN (undefined, e.g. zero denominator)."""
    return "n/a" if math.isnan(value) else f"{value:.3f}"


def main() -> None:
    """Run the interim keyword-proxy validation and print a full report."""
    reviews = _load_jsonl(REVIEWS_PATH)
    ground_truth = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    products = ground_truth["products"]

    records = build_interim_records(reviews, products)
    flags = scan_for_trends(records)
    results = evaluate(flags, products)

    print("=== interim validation: keyword-proxy topics/sentiment (NOT real extractions) ===")
    print(f"reviews: {len(reviews)}, products: {results['n_products_total']}")
    print(f"planted trend products: {results['trend_planted']}")
    print(f"caught (true positives): {results['true_positives']}")
    print(f"missed (false negatives): {results['false_negatives']}")
    print(f"false positives (any non-trend-planted product flagged): {results['false_positives']}")
    print(
        f"  of which batch-defect discrimination check: {results['batch_defect_false_positives']}"
    )
    print(f"true negatives: {results['true_negatives_count']}")
    print(f"precision: {_fmt(results['precision'])}")
    print(f"recall: {_fmt(results['recall'])}")
    print(f"false_positive_rate: {_fmt(results['false_positive_rate'])}")
    print()
    print(f"all {len(flags)} flags (sorted by confidence):")
    for flag in flags:
        shape = (
            f"phase_counts={flag.evidence['phase_counts']}"
            if "phase_counts" in flag.evidence
            else f"phase_ratios={flag.evidence['phase_ratios']}"
        )
        print(
            f"  {flag.product_id} / {flag.trend_type} / {flag.topic}: "
            f"confidence={flag.confidence} {shape} correlation={flag.evidence['correlation']}"
        )


if __name__ == "__main__":
    main()
