# ADR 0030: Free-text fields need a normalizing scorer — the third instance of one bug class

Status: accepted (Session 15c, C2). Supersedes the exact-string scoring assumption in ADR 0026.

## Context

Exact string match is the wrong comparator for a field whose value is free text written
independently by two parties (the model and the corpus labelers). This is now the **third**
place the same shape has been found:

1. Session 10, `topics`: near-miss paraphrases (`battery` vs `battery_life`) scored 0.
2. Session 15b Q8, `product`: for a review that names no product, the model writes
   `unknown product` / `general product` (its schema default and habit) and the labelers wrote
   `unknown` / `product` — different, all-correct spellings of "no product named", scored wrong.
3. Found in 15c: `eval/analyze_known_gaps.py` — the source of the published "97 of 324
   confident-wrong" — does not use `score_fixture` at all. It re-compares every field with raw
   `==`, stricter than the harness for every field it disagreed on (`stars_inferred` is scored
   ±1 in the harness, `pros`/`cons` are token-F1, and `unknown product` counted as a committed
   answer). It was not touched by the first two fixes and could not have been.

`product` scored **0.236** on the n=106 held-out set (81 of 106 zero) before this change.

## Decision

Fix the scorer, not the schema. `product` stays a required string (making it nullable is a
breaking API-contract change that treats a symptom; see Alternatives).

`eval/free_text_scoring.py`, applied to **both** prediction and gold, symmetrically:

- **`product`**: null (no product named) iff every token is in a small vocabulary, or the whole
  string is a null literal; otherwise compared case-, punctuation-, plural-, order- and
  spacing-insensitively. No substring, superset or synonym credit — a hallucinated `Mast` or a
  category mismatch (`earphone` vs `headphones`) stays wrong.
- **`topics`**: structural normalization only (separators, generic prefix/suffix tokens
  `overall_`/`product_`/`_quality`/`_life`/`_speed`, two aliases). A partial fix; a semantic
  judge would do better and is not attempted.
- **`competitor_mentions`**: spacing/punctuation-insensitive (`real me` = `realme`).
- `analyze_known_gaps.py` now reads the recorded per-field scores (single source of truth) and
  treats a normalized-null product as an abstention. "Wrong" = full miss (score 0), the same
  definition `analyze_coverage_metrics.py` uses.
- `score_fixture(..., strict=True)` reproduces the old behaviour so every delta is attributable.
- Enum fields (`sentiment`, `buy_again`, `language`, `stars`) stay exact: an enum has no
  spelling variation to forgive.

### C2a — where the null vocabulary came from

Derived by inventorying every distinct `product` value in the held-out gold (36 distinct),
held-out predictions (41 as deployed, 36 forced) and the **independent** dev-set gold (34), then
keeping only tokens that appear in placeholder strings: `unknown`, `product`, `products`,
`general`, `this`, `the`, `a`, `an`, `item`, `items`, plus the literals `n/a na none null nil -`.
Placeholder strings found: gold `unknown` (27), `product` (18), `Product` (2), `products` (1);
model `unknown product` (29), `general product` (22, 51 when language is forced), `product`,
`this product`.

The dev-set gold was held out of the derivation as a check. It found one miss: `unspecified
product`, which the vocabulary classified as a *named* product. `unspecified` was added — a
synonym of `unknown`, found outside the set being scored, so not fitting. No false-null was
found: `OnePlus product`, `Osm products`, `Mast product` all remain named.

### C2b — every field's scorer, audited

| field | kind | scorer | exact match appropriate? | action |
|---|---|---|---|---|
| `product` | free text | exact, case-insensitive | **no** | null-canonicalization + cosmetic normalization |
| `topics` | free-text list | set-F1 on exact strings | **no** | structural normalization (partial) |
| `competitor_mentions` | brand-name list | set-F1 on exact strings | mostly | spacing/punct normalization; 0 flips on n=106 |
| `pros`, `cons` | free-text list | token-F1 (tokens >2 chars) | already non-exact | none; no stemming/synonyms, noted as a limit |
| `sentiment`, `buy_again` | enum | exact | yes | none; coverage/wrong-committed byte-identical |
| `language` | enum | exact | yes | none — separate model/router gap (C2e) |
| `stars_inferred` | int | tolerance ±1 | yes | none (but `analyze_known_gaps` disagreed — fixed) |
| `stars` | int/null | exact | yes | none, but **vacuous here** (below) |
| `urgency`, `feature_requests` | enum / list | not scored by the harness | n/a | `analyze_known_gaps` only; exact |
| `analyze_known_gaps` comparator | all 12 | raw `==` | **no** | now defers to the harness scorers |

**Also found, opposite direction:** `stars` is null in gold *and* prediction for all 106
held-out reviews, so it contributes a constant 1.0 — a free 10% of the equal-weighted overall.
The published overall now carries the figure without constant fields beside it.

## Results (zero quota; recorded predictions byte-identical)

Source: `eval/results/held_out_scoring_v2.json` at `5c5c8e0` (cassette replay), scorer
`2026-09-20.free-text-v1`. Predictions were verified byte-identical before/after, and the strict
re-score reproduced the previously recorded scores exactly, which validates the harness. The
paired-bootstrap CIs of the deltas (10,000 resamples, seed 42) and the per-fixture flip lists
come from `eval/measure_scorer_delta.py` → `eval/results/scorer_delta_n106.json`, which ships
in the stacked follow-up PR so this one stays reviewable.

| n=106 held-out | old comparator | corrected | note |
|---|---|---|---|
| overall, as deployed | 68.3% | **72.7%** (95% CI 70.5–74.9) | +4.4 pp; paired-bootstrap CI of the delta +3.5 to +5.5 |
| overall, language forced | 72.9% | **77.4%** (75.4–79.3) | +4.5 pp |
| overall, as deployed, excluding constant `stars` | — | **69.7%** | the like-for-like figure |
| `product`, as deployed | 23.6% | 59.4% (50.0–68.9) | 43 zeros remain; 56.6% from null-canonicalization alone |
| `topics`, as deployed | 45.7% | 54.2% (47.2–61.5) | partial fix |
| `competitor_mentions` | 84.0% | 84.0% | 0 flips |

**How much of the gap was measurement.** Of the 31.7-point gap between the old overall and
100%, 4.4 points (14%) was the comparator. For `product` alone, 35.8 of the 76.4-point gap
(47%) was the comparator. No score went *down* on any fixture in any field (0 negative flips).
`language` (as deployed 48.1%, forced 100%) and `stars_inferred` (98.1%) are unchanged and are
model/router accuracy, not scorer, gaps.

Short-review confident-wrong (`analyze_known_gaps`, 27 reviews × 12 fields = 324 checks):

| | before | after |
|---|---|---|
| wrong-committed | 97 | **48** |
| real silent misses | 6 | **10** |
| abstention-correctness | 95.35% | **93.29%** |
| `product` wrong-committed | 25 | 5 |
| `topics` wrong-committed | 22 | 11 |

The unfavourable half is real and published with the favourable half: four `general product`
answers where the panel names a product were miscounted as confident guesses and are now
correctly recognised as silent misses, so abstention-correctness fell 2.1 points. The public
sentence no longer names `product` as the culprit; it names the top fields from the data.

The CI-gate dev set moved 77.1% → 78.6%. It is a contaminated change detector, never published
as accuracy; thresholds are unaffected (scores only rose).

## Consequences

- Published numbers rose because a measurement bug was fixed. The rise is disclosed beside the
  headline (README, generated; site, static) with the old figure, per rule 65c.
- Three comparators shared one bug class; a single scorer now owns free-text comparison and
  `analyze_known_gaps` reads its output instead of re-implementing it.
- `topics` remains a weak measure. A synonym-aware judge is the real fix and is out of scope.
- Honest limit: the corrected scorers were validated on this held-out set and the dev set. The
  null vocabulary is small and English-only; a new placeholder spelling would score wrong until
  added — the inventory step is the way to find it.
- Rule 85a applied: this scorer's surface is the `product`/`topics`/`competitor_mentions`
  fields on the harness and known-gaps paths. `eval/authenticity/runner.py` has its own scoring
  and is *not* covered; it is audited under C7.

## Alternatives

- **Make `product` nullable** (`str | None`): rejected. Breaking API-contract change with
  customer-visible semantics; it would fix the model's habit, not the labelers', so the gold would
  still disagree. Fix the measurement first.
- **LLM/embedding judge for `topics` and `product`**: better for synonymy, but adds a model
  dependency, cost and non-determinism to a gate that is currently $0 and byte-reproducible.
- **Only fix `product`**: rejected; that repeats the incident (fix the instance, leave the class).

## Correction note (Session 15d)

Dated 2026-09-20. The sentence "`language` (as deployed 48.1%, forced 100%) ... are model/router
accuracy, not scorer, gaps" is superseded. Those two figures are not model or router accuracy:
forced is 100% because the forced prompt states the label (echo), and as deployed 48.1% is the
detector's agreement with the corpus label (95% CI 38.8-57.5; label alpha 0.380, so this partly
measures label noise, not detector error). The published held-out headline now excludes `stars`
and `language`. See ADR 0021's Correction section. The scorer findings of this ADR (product,
topics, competitor_mentions) are unaffected.
