# Review intent: re-pilot 2 (S22 M2b), results and labelling-cost plan

Pre-registered in `docs/specs/review-intent.md` Amendment 4 (committed before any item was shown to a judge).
Prompt `ri-judge-v3`; four judges from four families run blind on Kaggle T4s; 300 FRESH random items (seed 44) plus
60 mined (30 `delivery`, 30 `service` candidates). Zero overlap with pilot 1 and re-pilot 1 (verified by hash:
0 of 360). All labels are panel consensus (SILVER), not truth. Data: `reports/labelling/pilot3/`. **This report stops here: no
labelling at scale starts until GG has decided on the cost plan in section 4.**

## 1. Verdict

1. **No task reaches PASS on the random 300; five reach USABLE-WITH-CAVEAT and every one clears the 0.667 alpha floor.**
   `needs_action` is closest: alpha 0.789 (PASS needs 0.80) with clear consensus 0.960, so it is the
   one task whose labels can cover about 96 percent of reviews. The 5-class broad intent passes the floor (0.711) where the 10-class
   task failed (0.653 in re-pilot 1). Coarsening worked for the classes that exist in the data.
2. **Coarsening did not fix the rare classes, it exposed them.** Across the random 300 plus 30 mined candidates each, the panel's
   consensus broad intent is `delivery` for 4 items and `service` for 4; none appears in the random 300 at all.
   The regexes retrieved candidates the panel did not call delivery or service, so mining public corpora cannot make these two
   classes measurable. A 0.95-precision claim for them needs the customers' own reviews.
3. **What is measurable now:** `needs_action` (prevalence 0.194 yes [0.153, 0.244]), `praise` versus `product_issue`
   versus `other` for the broad intent, sentiment, urgency `low` / `medium` (`high` has 2 consensus items), and aspect sentiment
   for beauty and food.
4. **Reading the numbers honestly.** Alpha on a skewed binary is conservative: pairwise agreement for `needs_action` is 0.931 and unanimity
   0.873, yet alpha is 0.789 because 80 percent of items are `no`. Gate thresholds are unchanged and were not reinterpreted.

## 2. Results (random 300; generated from `reports/labelling/pilot3/repilot2.json`)

<!-- METRICS:START -->
| Task | Re-pilot 1 alpha | Alpha | Kappa | Pairwise | Clear consensus (ceiling) | Unanimous | Gate | Mined 60: alpha / consensus |
|---|---|---|---|---|---|---|---|---|
| C1 needs action (yes / no) | n/a | 0.789 | 0.793 | 0.931 | 0.960 | 0.873 | USABLE-WITH-CAVEAT | 0.844 / 0.983 |
| C2 broad intent (5 classes) | 0.653 | 0.711 | 0.712 | 0.853 | 0.903 | 0.743 | USABLE-WITH-CAVEAT | 0.688 / 0.900 |
| T2 sentiment | 0.759 | 0.719 | 0.721 | 0.867 | 0.883 | 0.793 | USABLE-WITH-CAVEAT | 0.680 / 0.833 |
| T3 urgency | 0.683 | 0.704 | 0.644 | 0.895 | 0.930 | 0.817 | USABLE-WITH-CAVEAT | 0.820 / 0.883 |
| T6 aspect sentiment, beauty | 0.697 | 0.749 | 0.821 | 0.910 | 0.908 | 0.875 | USABLE-WITH-CAVEAT | 0.778 / 0.800 |
| T6 aspect sentiment, food | 0.740 | 0.744 | 0.759 | 0.866 | 0.844 | 0.833 | USABLE-WITH-CAVEAT | 0.672 / 0.913 |
<!-- METRICS:END -->

Rare-class rule of Amendment 4 (test positives needed for a per-class precision interval of half-width 0.05 at 0.90 is 138; labelled items needed =
138 / (prevalence x 0.15 test share)):

<!-- METRICS:START -->
| Broad class | Random 300 | Mined 60 | Total | Prevalence (random), Wilson 95% | Measurable (30+) | Labelled items needed for 138 test positives (point / at upper / at lower bound) |
|---|---|---|---|---|---|---|
| product_issue | 48 | 14 | 62 | 0.177 [0.136, 0.227] | yes | 5,195 / 4,053 / 6,750 |
| delivery | 0 | 4 | 4 | 0.000 [0.000, 0.014] | no | unbounded / 65,715 / unbounded |
| service | 0 | 4 | 4 | 0.000 [0.000, 0.014] | no | unbounded / 65,715 / unbounded |
| praise | 194 | 30 | 224 | 0.716 [0.659, 0.766] | yes | 1,286 / 1,201 / 1,396 |
| other | 29 | 2 | 31 | 0.107 [0.075, 0.149] | yes | 8,598 / 6,154 / 12,186 |
<!-- METRICS:END -->

Panel health: parse failures after one retry: llama 0, gemma 1, qwen 0, mistral 1 of 360 (mistral 0.067 in pilot 1, 0.021
in re-pilot 1). Aspects dropped for an unverifiable quote: llama 19, gemma 21, qwen 10, mistral 112.

## 3. Measured cost (VERIFIED: kernel status `reports/labelling/pilot3/kernel_status.json`)

| Judge | Seconds for 360 items | Seconds per item |
|---|---|---|
| llama31_8b | 1185 | 3.292 |
| gemma2_9b | 1581 | 4.392 |
| qwen3_8b | 1002 | 2.783 |
| mistral_7b | 1418 | 3.939 |

Four judges: **14.4 GPU-seconds per item** (re-pilot 1's v2 prompt: about 20 s). The v3 prompt is shorter (one field set, no
secondary intents), so output tokens fell. Each T4 ran two judges one after the other, so the kernel took under an hour of wall time.
For 10,000 items with all four judges: **40 GPU-hours**, about 20 hours of wall time on two T4s.

## 4. Labelling-cost plan (proposal; needs GG's decision and a dated Amendment 5 BEFORE it is used)

GG's direction: two judges at scale, a tiebreaker only on disagreement, Kaggle (2 sessions) and the local GPU in parallel, Colab Pro
overflow run by GG. I simulated it on the 300 random items (post-hoc, exploratory): pick a pair, take their label where they agree,
call a third judge only when any of the four tasks disagrees (one call returns all tasks), accept the third judge's label only if it
equals one of the two.

<!-- METRICS:START -->
| Judges (pair + tiebreak on disagreement) | Third-call rate | GPU-seconds per item | Coverage C1 / C2 / sentiment / urgency | Agreement with 4-panel consensus (NOT independent, see note) |
|---|---|---|---|---|
| llama31_8b + qwen3_8b, tiebreak mistral_7b | 0.26 | 7.10 | 1.00 / 0.98 / 0.94 / 0.99 | 1.00 / 1.00 / 1.00 / 1.00 |
| llama31_8b + qwen3_8b, tiebreak gemma2_9b | 0.26 | 7.22 | 1.00 / 0.99 / 0.98 / 0.99 | 1.00 / 1.00 / 1.00 / 1.00 |
| mistral_7b + qwen3_8b, tiebreak llama31_8b | 0.29 | 7.69 | 1.00 / 0.98 / 0.94 / 0.99 | 1.00 / 1.00 / 1.00 / 1.00 |
| llama31_8b + mistral_7b, tiebreak qwen3_8b | 0.26 | 7.95 | 1.00 / 0.98 / 0.94 / 0.99 | 1.00 / 1.00 / 1.00 / 1.00 |
| mistral_7b + qwen3_8b, tiebreak gemma2_9b | 0.29 | 8.01 | 1.00 / 1.00 / 0.98 / 1.00 | 1.00 / 1.00 / 1.00 / 1.00 |
<!-- METRICS:END -->

Cheapest scheme: **llama31_8b + qwen3_8b, tiebreak mistral_7b: 7.1 GPU-seconds per item** (third call on
26 percent of items), against 14.4 for four judges.

| Plan | GPU-hours for 10,000 items | Wall hours on 2 T4s |
|---|---|---|
| Four judges (the registered definition) | 40 | 20 |
| Two judges + tiebreaker (cheapest pair above) | 20 | 10 |

**Caveats that matter.**
1. The "agreement with the 4-panel consensus" column is NOT an independent accuracy: the two-judge labels are subsets of the same four
   votes, so agreement is inflated by construction. It shows the cheaper rule rarely contradicts the full panel, not that it is right.
2. The two-judge rule changes the label definition (Amendment 2 defines the label as 3-of-4 consensus). It needs a new dated
   amendment, and a small independent audit (a human check of about 200 disputed or agreed-by-two items) before its labels are a
   test set. Training labels tolerate this more than sealed-test labels do; I would keep the sealed test on the 4-judge panel.
3. Kaggle free quota is 30 GPU-hours per week per account (BELIEVED, from memory of Kaggle's published limit, not re-fetched). Two
   sessions in parallel use it twice as fast, so 10,000 items on the two-judge plan is feasible in roughly one to two weeks of Kaggle
   alone. The local GPU speed has NOT been measured for these models and other jobs share it (rule 46a): measure before relying on it.
   Colab Pro overflow is GG's to run: the kernel is `engine/kaggle/kernel_ollama.py`, which needs an Ollama install and the four model pulls.
4. Data supply: the English strata (food, beauty, apparel) hold hundreds of thousands of rows. The Flipkart Hinglish subset holds 393
   rows, 225 already used in pilots, so Hinglish cannot reach 10,000 and no per-language claim is possible below 100 consensus items per
   language without more data.

## 5. What this means for the tasks

| Task | State | Next step proposed (needs approval) |
|---|---|---|
| C1 needs_action | USABLE (alpha 0.789, consensus 0.960); about 4,732 labelled items give 138 test positives | Label at scale (English strata) under Amendment 5; sealed test on the 4-judge panel |
| C2 broad intent | USABLE, but only `product_issue`, `praise`, `other` are measurable | Label at scale, report three measurable classes; `delivery` and `service` wait for customer data |
| T2 sentiment, T3 urgency | USABLE; `neutral`, `mixed`, `high` too rare to score | Label at scale for `positive` / `negative` and `low` / `medium` |
| T6 aspect sentiment (beauty, food) | USABLE | Label at scale with quoted spans |
| Rare intents (delivery, service, refund, question, competitor) | Not measurable from public data | Judge.me real-store ingestion |

Not done and not claimed: any human audit of the consensus labels, any model trained on these labels, any per-language slice.
