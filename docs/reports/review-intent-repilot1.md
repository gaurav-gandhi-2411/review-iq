# Review intent: re-pilot 1 (S22 C3c)

Pre-registered in `docs/specs/review-intent.md` Amendment 3 (committed before any re-pilot item was shown to a
judge; commit order is in the PR). Prompt `ri-judge-v2`; four judges from four families (llama3.1:8b, qwen3:8b,
gemma2:9b, mistral:7b) run blind on Kaggle T4s at commit `6b8c9b3`; 300 FRESH random items (seed 43, zero
overlap with pilot 1) and 180 items mined with fixed regexes for the rare classes. Results:
`reports/labelling/pilot2/`. All labels are panel consensus (SILVER), not ground truth. Eight of the items were
shown to llama3.1:8b in a prompt smoke test before the run (labels discarded, nothing changed afterwards).

## 1. Verdict

1. Two tasks reach USABLE-WITH-CAVEAT again and none reaches PASS: sentiment (alpha 0.759, consensus
   0.923) and urgency (0.683, 0.897).
2. The v2 fixes did NOT repair the 10-class intent task: alpha 0.653 against 0.693 in pilot 1, now
   below the 0.667 floor (FAIL). The priority rule did not reduce disagreement; it moved it.
3. The explicit no-repurchase binary FAILS by the pre-registered rule (alpha 0.487) although pairwise agreement is
   high (0.927) and so is clear consensus (0.957): the flag is rare (52 items flagged by any
   judge, 14 by three or more), and on the items any judge flagged alpha is 0.105. Judges do not
   agree on WHICH reviews state it.
4. Aspect presence improved (alpha 0.23 to 0.33 in pilot 1, 0.41 to 0.56 now) and still FAILS. Aspect sentiment
   (given a quoted span) is USABLE for beauty and food, and borderline for apparel (0.655).
5. Mining did not fill the rare classes. After 30 mined candidates per class from corpora holding thousands of
   regex matches, the panel's consensus primary intent is `delivery` for 3, `customer_service` for
   3, `question` for 3, `competitor_comparison` for 0, `suggestion` for 1. Only `product_defect` and `praise`
   (and `return_refund_request` at 14) have meaningful support. This confirms pilot 1: these classes are not in these
   corpora at a measurable rate, or the regex candidates are mostly false positives. Either way a 0.95-precision
   claim for them needs the customers' own reviews.

## 2. Results (random 300; generated from `reports/labelling/pilot2/repilot.json`)

<!-- METRICS:START -->
| Task | Pilot 1 alpha | Re-pilot alpha (random 300) | Kappa | Pairwise | Clear consensus (ceiling) | Unanimous | Pre-registered gate | Mined 180: alpha / consensus |
|---|---|---|---|---|---|---|---|---|
| T1 primary intent (10 classes) | 0.693 | 0.653 | 0.653 | 0.851 | 0.863 | 0.767 | FAIL | 0.600 / 0.772 |
| T2 sentiment | 0.772 | 0.759 | 0.748 | 0.894 | 0.923 | 0.830 | USABLE-WITH-CAVEAT | 0.680 / 0.889 |
| T3 urgency | 0.716 | 0.683 | 0.570 | 0.865 | 0.897 | 0.763 | USABLE-WITH-CAVEAT | 0.653 / 0.828 |
| T4 explicit no-repurchase (binary) | n/a | 0.487 | 0.526 | 0.927 | 0.957 | 0.857 | FAIL | 0.573 / 0.950 |
| T6 aspect presence, apparel | 0.333 | 0.497 | 0.492 | 0.748 | 0.843 | 0.552 | FAIL | 0.385 / 0.833 |
| T6 aspect presence, beauty | 0.331 | 0.559 | 0.559 | 0.784 | 0.901 | 0.600 | FAIL | 0.437 / 0.835 |
| T6 aspect presence, food | 0.229 | 0.410 | 0.410 | 0.740 | 0.851 | 0.531 | FAIL | 0.382 / 0.837 |
| T6 aspect sentiment, apparel | 0.704 | 0.655 | 0.725 | 0.857 | 0.844 | 0.780 | FAIL | 0.756 / 0.844 |
| T6 aspect sentiment, beauty | 0.658 | 0.697 | 0.769 | 0.882 | 0.903 | 0.814 | USABLE-WITH-CAVEAT | 0.741 / 0.876 |
| T6 aspect sentiment, food | 0.711 | 0.740 | 0.784 | 0.874 | 0.867 | 0.800 | USABLE-WITH-CAVEAT | 0.787 / 0.869 |
<!-- METRICS:END -->

Class support after mining (the rare-class criterion of Amendment 3):

<!-- METRICS:START -->
| Intent class | Consensus primary, random 300 | Consensus primary, mined 180 | Total | Flagged by 3+ judges (primary or secondary) | Measurable (30+ consensus items) |
|---|---|---|---|---|---|
| product_defect | 44 | 36 | 80 | 128 | yes |
| delivery | 0 | 3 | 3 | 4 | no |
| customer_service | 0 | 3 | 3 | 15 | no |
| pricing | 2 | 3 | 5 | 21 | no |
| praise | 207 | 81 | 288 | 329 | yes |
| question | 0 | 3 | 3 | 4 | no |
| return_refund_request | 5 | 9 | 14 | 29 | no |
| suggestion | 0 | 1 | 1 | 10 | no |
| competitor_comparison | 0 | 0 | 0 | 0 | no |
| spam_irrelevant | 1 | 0 | 1 | 1 | no |
<!-- METRICS:END -->

Panel health: parse failures (after one retry) llama 0, gemma 0, qwen 0, mistral 10
of 480 (mistral 0.021, was 0.067); aspects dropped because their quote was not in the review: llama
73, gemma 86, qwen 57, mistral 178. The verifiable-quote rule removed a lot of
judge invention, which is the intended effect.

## 3. Exploratory checks (NOT pre-registered; random 300 only; from `reports/labelling/pilot2/exploratory.json`)

- Intent collapsed to the four reporting groups ACTION / INSIGHT / SIGNAL / NOISE: alpha 0.706, consensus 0.887.
- Binary needs-action (any ACTION-group primary intent): alpha 0.767, consensus 0.940.
  Praise against anything else: alpha 0.764, consensus 0.947.
  The product-relevant coarse split is as reliable as sentiment; the 10-way split is where reliability breaks.
- Dropping qwen3 raises T1 alpha to 0.687; dropping llama3.1 lowers it to 0.626. No single judge explains the failure.
- Derived rating-text mismatch (stars 4 or 5 with negative consensus sentiment, or stars 1 or 2 with positive): 5 of
  277 random items. It is rare, and as reliable as sentiment by construction.
- Because these were looked at after seeing the data, they are hypotheses for re-pilot 2, not results.

## 4. What the pre-registered decision rule says, and what I propose

Amendment 3: tasks at USABLE or better enter labelling-at-scale PLANNING; tasks still failing get one more
definition fix and a re-pilot 2 on fresh items, then are merged or dropped. No labelling at scale starts before GG
has seen this report.

| Task | Result | Proposal for re-pilot 2 (needs a dated amendment BEFORE it runs) |
|---|---|---|
| T2 sentiment | USABLE | Plan labelling at scale. Keep as defined. |
| T3 urgency | USABLE | Plan labelling at scale for `low` / `medium`; `high` stays unsupported (only 16 items at consensus across all 480) and needs targeted data |
| T1 intent | FAIL (10 classes) | Make the coarse task the headline: needs-action vs praise (alpha 0.77 exploratory) with the 10-way label kept as a secondary, reported-not-claimed output; test on fresh items |
| T4 explicit no-repurchase | FAIL | Drop from the model. Keep as a keyword feature only if a rare-flag gate is pre-registered: the alpha gate is the wrong tool at about 7 percent prevalence, but the right replacement (positive-class agreement) must be registered before data, not after |
| T5 mismatch | derived | Ship as the derived rule once sentiment ships (it inherits sentiment's reliability) |
| T6 aspects | presence FAIL, sentiment USABLE | Keep aspect SENTIMENT only where a quoted span exists; do not claim aspect presence |
| Rare intent classes | not measurable | Defer to real customer reviews (Judge.me ingestion); do not spend more public-corpus mining on them |

Not done and not claimed: any human audit of the consensus labels, any per-language slice (the Hinglish stratum
is 75 items per task here), and any model trained on these labels.
