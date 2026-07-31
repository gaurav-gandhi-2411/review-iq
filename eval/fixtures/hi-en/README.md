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

## Growth attempt (Wave 1 Section B, 2026-07-30) — 0 new fixtures added here

`claude-sonnet-4-5` labeling a different model's output (rationale above) is still a
**single-model** ground truth — an improvement over hand-labeling, but not the
multi-LLM consensus mechanism this session introduced for new growth (see
`eval/consensus/` and the main `README.md`'s "How eval fixtures are labeled" section).
These 15 fixtures are left as-is (historical, not rewritten), but any *new* hi-en
fixtures were meant to go through consensus labeling instead of a repeat
single-model pass.

**What actually happened this session:** `eval/data/flipkart_candidates.jsonl`
(regenerated fresh from the same 3 Kaggle datasets) yielded 48 hi-en candidates in the
30–600 char range — the same real ceiling documented above — of which 33 were new
(not already one of these 15). All 33 were submitted to the 2-judge consensus panel
(`openai/gpt-oss-120b` + `qwen/qwen3.6-27b`, see `eval/consensus/panel.py`), but **all
33 hit Groq free-tier rate-limit exhaustion on the dedicated benchmark key before
either judge could respond** (both judges errored on every one — confirmed via the
raw `judge_outputs` in `eval/consensus/results/consensus_labels.jsonl`, not inferred).
This is the same failure class already documented in
`benchmark/vernacular_v2/SILVER_REPORT.md` for the same dedicated key. **0 new hi-en
fixtures were added; this directory is still exactly the original 15.** Recoverable on
a fresh quota window — not retried further this session, to avoid repeatedly hitting
the same wall. See `docs/architecture/adr/0002-eval-set-growth-and-mde.md` for the
full growth accounting across all languages.
