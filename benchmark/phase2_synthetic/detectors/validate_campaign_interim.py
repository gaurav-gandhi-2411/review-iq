"""SYNTHETIC-VALIDATED interim check script for `campaign_synthetic.py`.

Not a pytest suite (out of scope for this task's file list) -- a standalone verification script
that runs the detector against `reviews.jsonl` and asserts the specific confound checks called
out in this detector's build spec:

  1. Both planted campaigns (SYN-CAMPAIGN-01/02) are caught.
  2. Both matched controls (SYN-CAMPAIGN-CTRL-01/02) are NOT flagged. CTRL-02's reviews are
     spread evenly enough that no window even clears the timing-burst gate. CTRL-01 DOES have a
     real qualifying timing-burst window (organic review volume can genuinely cluster) -- this
     control is rejected by the CONFIDENCE gate instead: zero reviewer/text concentration in that
     window drives confidence to 0, proving the detector isn't just alerting on "popular product,
     lots of reviews," even when that popularity happens to produce a real timing burst.
  3. The 2 planted batch-defect products are NOT flagged -- their anomaly is topic-content
     concentration within a spike window, not raw review-volume/timing concentration, so the
     timing-burst gate should reject them too. This is a real potential confound (both patterns
     plant a "spike window" in ground truth) and is checked explicitly, not assumed.
  4. All other 10 non-campaign products (2 batch-defect control, 2 trend planted, 2 trend
     control, 6 noise) are NOT flagged.

Run: `uv run python benchmark/phase2_synthetic/detectors/validate_campaign_interim.py`
"""

from __future__ import annotations

import json

from campaign_synthetic import (
    _GROUND_TRUTH_PATH,
    _REVIEWS_PATH,
    BURST_RATIO_THRESHOLD,
    find_best_burst_window,
    load_reviews,
    scan_corpus,
)

_PLANTED_CAMPAIGNS = {"SYN-CAMPAIGN-01", "SYN-CAMPAIGN-02"}
_MATCHED_CONTROLS = {"SYN-CAMPAIGN-CTRL-01", "SYN-CAMPAIGN-CTRL-02"}
_PLANTED_BATCH_DEFECTS = {"SYN-BATCH-01", "SYN-BATCH-02"}


def _group_by_product(reviews: list) -> dict[str, list]:
    """Group loaded Review records by product_id."""
    by_product: dict[str, list] = {}
    for review in reviews:
        by_product.setdefault(review.product_id, []).append(review)
    return by_product


def main() -> None:
    """Run the detector and print pass/fail for each confound check, then exit non-zero on
    any failure so this can be wired into CI later without modification.
    """
    reviews = load_reviews(_REVIEWS_PATH)
    by_product = _group_by_product(reviews)
    reviewer_products: dict[str, set] = {}
    for review in reviews:
        reviewer_products.setdefault(review.reviewer_id, set()).add(review.product_id)
    flags = scan_corpus(reviews)
    flagged_ids = {f.product_id for f in flags}
    ground_truth = json.loads(_GROUND_TRUTH_PATH.read_text(encoding="utf-8"))

    failures: list[str] = []

    print("== check 1: both planted campaigns caught ==")
    for pid in sorted(_PLANTED_CAMPAIGNS):
        ok = pid in flagged_ids
        print(f"  {pid}: {'FLAGGED' if ok else 'MISSED'}")
        if not ok:
            failures.append(f"{pid} was not flagged (expected: flagged)")

    print("\n== check 2: matched controls NOT flagged (gate OR confidence) ==")
    for pid in sorted(_MATCHED_CONTROLS):
        ok = pid not in flagged_ids
        found = find_best_burst_window(by_product[pid], reviewer_products)
        best_ratio = found[0].ratio_vs_baseline if found else 0.0
        confidence = found[1] if found else 0.0
        print(
            f"  {pid}: {'not flagged (correct)' if ok else 'FLAGGED (incorrect)'} "
            f"-- best burst window found: {'yes' if found else 'none'}, "
            f"ratio={best_ratio:.3f} (threshold={BURST_RATIO_THRESHOLD}), "
            f"confidence={confidence:.3f}"
        )
        if not ok:
            failures.append(f"{pid} was incorrectly flagged (expected: not flagged)")

    print("\n== check 3: batch-defect products NOT flagged (topic-spike != timing-burst) ==")
    for pid in sorted(_PLANTED_BATCH_DEFECTS):
        ok = pid not in flagged_ids
        found = find_best_burst_window(by_product[pid], reviewer_products)
        best_ratio = found[0].ratio_vs_baseline if found else 0.0
        print(
            f"  {pid}: {'not flagged (correct)' if ok else 'FLAGGED (incorrect)'} "
            f"-- best burst window found: {'yes' if found else 'none'}, "
            f"ratio={best_ratio:.3f} (threshold={BURST_RATIO_THRESHOLD})"
        )
        if not ok:
            failures.append(f"{pid} was incorrectly flagged (expected: not flagged)")

    print("\n== check 4: all other non-campaign products NOT flagged ==")
    all_products = set(ground_truth["products"].keys())
    other_products = all_products - _PLANTED_CAMPAIGNS - _MATCHED_CONTROLS - _PLANTED_BATCH_DEFECTS
    for pid in sorted(other_products):
        ok = pid not in flagged_ids
        print(f"  {pid}: {'not flagged (correct)' if ok else 'FLAGGED (incorrect)'}")
        if not ok:
            failures.append(f"{pid} was incorrectly flagged (expected: not flagged)")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES:'}")
    for failure in failures:
        print(f"  - {failure}")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
