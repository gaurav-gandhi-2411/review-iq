# eval/fixtures/hi-en — Hinglish Eval Fixtures

15 hand-auditable ground-truth fixtures for evaluating Hinglish (Roman-script Hindi/English code-mix) review extraction.

## Source

- **Dataset:** Flipkart product review Kaggle datasets (niraliivaghani, kabirnagpal, naushads)
- **Language detection:** Regex heuristics (strong Hinglish markers: `nahi`, `bahut`, `vasool`, `bakwaas`, etc.) applied across ~14k candidates; 48 hi-en candidates in the 30–600 char range after dedup
- **Selection:** Top 15 ranked by review length score (prefers 100–400 chars) + product name presence

## Labeler

- **Model:** `claude-sonnet-4-5` (Anthropic Claude Sonnet) — independent from production model (Groq Llama 3.3 70B)
- **Rationale:** Different model labels vs. is evaluated — keeps the eval honest
- **Script:** `eval/label-helper-llm.py`
- **Labeled at:** 2026-05-15T12:16:50 UTC

## Cost

| Metric | Value |
|---|---|
| Fixtures written | 15 |
| Candidates considered | 15 |
| Total input tokens | 9,315 |
| Total output tokens | 2,517 |
| **Total Anthropic API cost** | **$0.0657** |
| Average cost per fixture | $0.0044 |

## Fixture format

Each fixture follows the standard schema:

```json
{
  "id": "hi-en-001",
  "review_text": "...",
  "ground_truth": {
    "product": "earphone",
    "stars": null,
    "stars_inferred": 4,
    "pros": ["..."],
    "cons": ["..."],
    "buy_again": true,
    "sentiment": "positive",
    "urgency": "low",
    "topics": ["sound quality", "value"],
    "competitor_mentions": ["Apple"],
    "feature_requests": [],
    "language": "hi-en"
  },
  "scoring_notes": {
    "exact_match_fields": ["product", "stars", "buy_again", "sentiment", "language"],
    "set_overlap_fields": ["topics", "competitor_mentions"],
    "fuzzy_fields": ["pros", "cons"],
    "tolerance_fields": {"stars_inferred": 1}
  },
  "labeling_meta": {
    "labeled_by": "claude-sonnet-4-5",
    "labeled_at": "<ISO timestamp>",
    "model_version": "claude-sonnet-4-5-20250929",
    "input_tokens": 625,
    "output_tokens": 169
  }
}
```

## Auditing

All 15 fixtures are committed. To audit a label:
1. Read the `review_text`
2. Check each `ground_truth` field against the raw text
3. Raise a GitHub issue if a label is incorrect — labels can be corrected with explicit commit notes

Do **not** modify fixture ground truth without human review and an explicit note in the PR description.
