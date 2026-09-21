# Held-out gold label review (Session 15d, U6)

Status: findings and a decision request. **No gold label and no scorer was changed by this PR.**
Every number below comes from `eval/results/split_gold_audit_n106.json`
(`scripts/audit_split_gold.py`, zero quota, recorded predictions) unless a path says otherwise.

## 1. The finding: "no consensus" is stored as if it were a label (VERIFIED)

`eval/consensus/build_held_out_corpus.py::build_fixture` writes each gold field as
`silver_or_none(field) or <default>`, where the panel's `silver` is kept only when agreement is
`unanimous` or `majority`. When the three judges **split**, the gold silently becomes the default:
`"unknown"` for `product`, `[]` for list fields, `null` for enums. That is indistinguishable from
"the review genuinely names no product / has no pros". The fixture's own
`labeling_meta.agreement_per_field` does record `split`, but nothing in scoring reads it. It is the
exact-string-match bug class (ADR 0030) once more, this time in how the *gold* was built: the panel
voted on the exact free-text string, so "Boat earphones" / "Boat" / "Boat earphone/speaker" is a
split and becomes "unknown".

<!-- METRICS:HISTORICAL -->
Split-collapsed gold values on the 106 held-out fixtures (git sha of the artifact in its JSON):

| field | split (gold is a default, not a label) |
|---|---|
| `pros` | 30 |
| `product` | 26 |
| `topics` | 25 |
| `cons` | 21 |
| `feature_requests` | 1 |
| `buy_again`, `sentiment`, `language`, `stars`, `stars_inferred`, `competitor_mentions` | 0 |
<!-- /METRICS:HISTORICAL -->

## 2. What it does to the published numbers (symmetric, not a headline)

Excluding the split (fixture, field) pairs from scoring (not relabelling them):

<!-- METRICS:HISTORICAL -->
| as deployed, held-out n=106 | all pairs | excluding split pairs |
|---|---|---|
| overall, excluding constant `stars` | 69.7% | 76.0% (delta +6.3 pp, paired-bootstrap 95% CI +4.9 to +7.7) |
| `pros` | 53.9% | 73.8% |
| `cons` | 70.1% | 87.4% |
| `topics` | 54.2% | 68.5% |
| `product` | 59.4% | **57.5% (goes down)** |
<!-- /METRICS:HISTORICAL -->

The direction differs by field: an empty gold list scores 0 against any non-empty prediction, so
`pros`/`cons`/`topics` are penalised by split gold; `product` is not. This is a scorer-policy
decision, not a correction to slip into a headline (the change raises our own number, which is the
contamination pattern unless decided and disclosed first). **Decision requested from GG:** adopt
"split is not a label: exclude it from scoring" as the held-out policy (I recommend yes: it is
principled, symmetric, and fixed a priori), then a separate PR changes the scorer, regenerates the
artifacts and discloses the delta beside the headline.

## 3. Individual gold labels flagged in S7

Raw judge votes are in `eval/consensus/results/held_out_batch_log.jsonl`.

| fixture | gold | judge votes (qwen3.6 / qwen3.8 / gemini) | assessment |
|---|---|---|---|
| hien-0065 | `backup` | `backup` / `backup` / `product` | **Likely wrong.** Text is "Nice h betray backup mast h" (battery backup): `backup` is a feature, not a product. The 2-of-3 majority is two checkpoints of the same vendor making the same error; the independent judge said the placeholder. Better gold: no product named. |
| hien-0070 | `Osm products` | `Osm products` / `Osm products` / `sound system / speaker` | **Likely wrong.** "Osm" is slang for "awesome". The model's prediction was the identical string, so it currently scores *correct*: this label flatters us by one fixture. |
| hien-0028 | `audio device` | `audio device` / `speaker` / `audio device` | Legitimate category inference by majority; not an error. |
| hien-0043 | `audio device` | `audio device` / `audio device` / `speaker or audio device` | Legitimate category inference. |
| hien-0102 | `speaker` | `speaker` / `speaker` / `sound system` | Category inferred from weak cues ("bass", "sound"): defensible, weak. |

Adjudication needed (independent human or a genuinely disjoint judge), then a **separate PR** that
changes only the agreed fixtures with the adjudication attached and discloses the score effect of
each relabel in the same PR. I have not relabelled anything.

## 4. Should `product` infer a category from weak cues? (U6c, measured)

On the 106, as deployed, separating genuine gold from split defaults:

<!-- METRICS:HISTORICAL -->
| gold | model abstained | model named, correct | model named, wrong |
|---|---|---|---|
| category-only, consensus (n=35) | 13 | 18 | 4 |
| brand/model, consensus (n=23) | 4 | 8 | 11 |
| genuine "no product", consensus (n=22) | 20 correct | | 2 wrong |
| "unknown" only because the panel split (n=26) | 17 | | 9 |
<!-- /METRICS:HISTORICAL -->

- **Upside of inferring a category:** the model abstains on 13 of 35 category-gold reviews
  (about 12% of the corpus). When it does name a category it is exactly right 18 of 22 times.
- **Downside:** on genuinely no-product reviews it named a product on only 2 of 22 (both
  hallucinated adjectives: "Mast", "Mast product"). The apparent over-inference on gold `unknown`
  is mostly the split artifact above: on 9 of those 11 fixtures every judge named a product.
- **Recommendation: infer a category when there is a cue** (audio vocabulary, "bass", "sound",
  a device word) and abstain (`null`) when there is none. The measured cost of abstaining on
  category-inferable reviews is larger than the measured cost of over-naming. Caveat: n is small
  and the 13 recoverable cases are the weakest-cue ones, so expect below the 18/22 rate on them.
  This is a product decision, not a scorer change.

## Limits

Recorded predictions only (one run each); the panel's third judge (`gemini-3.5-flash-lite`) is a
different vendor but not verified disjoint on this task; "genuine null" means the panel agreed on
a placeholder, which is itself an LLM judgment.
