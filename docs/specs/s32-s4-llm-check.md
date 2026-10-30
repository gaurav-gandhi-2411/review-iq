# S32 exploratory check: how does the LLM do on complaints inside 4-5 star reviews?

**Status: EXPLORATORY. Pre-registered 2026-10-29, before any number below is computed.** It cannot change the recorded Amendment 16
and 17 verdicts (A16 FAILED E2 and E5; A17 FAILED F2). It answers one question those amendments left open, and its outcome only
decides which branch of the fixed decision tree (section 7) S32 takes. Labels are SILVER (four-judge panel consensus).

## 1. Question

Both router experiments failed only on recall for complaints inside 4-5 star reviews ("S4"): A16 run 4 114/126 = 0.905 (lower bound
0.841), A17 R1+ 119/133 = 0.895 (lower bound 0.831), against an absolute 0.85 bar. The LLM comparator was only ever run on Block A. Is
the router actually worse than the all-LLM path on S4, or is S4 hard for both?

## 2. Use of spent sealed sets (deviation from the S31 spec section 1 rule 6, authorised by the S32 brief)

fresh16 and fresh17 were each used once, by their registered evaluations. This check re-reads their S4 items under GG's S32 brief.
Constraints: nothing is tuned, selected, retrained or re-thresholded on them; the router configurations are frozen as registered
(section 4); neither set may ever serve as a confirmatory or selection set again. Any confirmatory follow-up (section 7, D1) uses a
fresh draw from untouched pools.

## 3. Items

Every S4 item (block A or B, stars >= 4) of fresh16 and fresh17, positives AND negatives, from the committed manifests
(`reports/labelling/fresh16/manifest.json`, `reports/labelling/fresh17/manifest.json`); gold = `needs_action` panel consensus
(`reports/intent/labels_fresh16/fresh16_labels.json`, `reports/intent/labels_fresh17/fresh17_labels.json`). Items without a
consensus label are excluded from every metric and counted. Counts, read from those files before registration (ids and labels only):

| Set | S4 items | gold yes | gold no | no consensus |
|---|---|---|---|---|
| fresh16 | 3,128 (A 1,428, B 1,700) | 126 | 2,959 | 43 |
| fresh17 | 3,819 (A 1,419, B 2,400) | 133 | 3,622 | 64 |
| pooled | 6,947 | 259 | 6,581 | 107 |

**Correction to the S32 brief:** the observed positive rate on S4 is 259 / 6,840 = **3.8 percent**, not about 7.5 percent. Reaching
>= 300 S4 positives therefore needs about 8,000 S4 items, not about 4,000 (used in section 7, D1/D3).

## 4. Router decisions (frozen; computed from stored logits, no new model inference)

- **Primary: the A17 design, R1+** = R1 OR p(needs_action = yes) >= tau, tau = 0.07 (`reports/intent/a17/tau.json`), served run 2
  (static-int8, x64 thresholds `app/core/classify/thresholds_run2_onnx_static_int8_x64.json`), via `engine.intent.fresh17_eval.routes`.
  Logits: run 2 on fresh16 and on fresh17 from the pinned Kaggle CPU scoring of S31 (private, `D:/ml-cache/s31/p7/`).
- **Alongside: A16 run 4, R1** with its own thresholds, from the stored fresh16 scoring (private `D:/ml-cache/s31/run4_out/out/final_H128/`).
  **fresh16 only:** run 4 was never scored on fresh17 and is not scored now (reported, not a deviation of this check).
- Also reported: R1 alone on run 2 (both sets).

## 5. LLM comparators

Prompt for both: `engine.intent.llm_moat.PROMPT` (zero-shot, the four tasks as JSON), review truncated to 1,500 characters, rendered
by `engine.intent.llm_moat_local.render_prompt`. Request body = `llm_moat_local.build_body`: temperature 0, seed 42, JSON mode,
`reasoning_effort` low, `max_tokens` 1,500 (Amendment 15 settings). The LLM "routes" an item when its `needs_action` label is "yes".

- **gpt-oss-20b, all S4 items.** Kaggle, Ollama 0.40.6 pinned (sha256 `c164ab8b...c0e5e0` verified in-kernel), `gpt-oss:20b`.
  Block A S4 items: the stored rows of `review-intent-fresh16-llm-run` and `review-intent-fresh17-llm-run` (same build, digest
  `f38aa0c5...`, same settings; these rows already entered E6/F4 on Block A). Block B items (fresh16 1,700 + fresh17 2,400 = 4,100):
  one new kernel, same kernel code (`engine/kaggle/kernel_moat20b.py`, mode `items`). **Empty completions must be 0**: any empty is
  retried once in a second pass; any still empty is reported, scored as LLM "no" in the primary analysis and excluded in a sensitivity
  analysis.
- **gpt-oss-120b, S4 positives only (259).** Groq FAILOVER org (Secret Manager `groq-api-key`, value never printed), through the
  machine-wide budget ledger (`engine.groq_budget_hook`) and this harness's own cap of 100,000 tokens per trailing 24 h; zero traffic on
  the primary org. Same body with model `openai/gpt-oss-120b`. Items in a fixed random order (Random(42) shuffle of the pooled positive
  ids), so a partial run is a random subsample. Transport/quota failures are retried and never recorded as answers; an unparseable or
  empty completion is an answer (LLM "no") and is counted.
- **120b deadline:** if not all 259 rows exist by **2026-11-03 06:00 UTC**, the analysis runs on the completed rows (a random subsample by
  construction), labelled INCOMPLETE with n stated, and the decision uses it unchanged.

## 6. Metrics

Per set and pooled, on S4 gold-yes items:

1. Recall (Wilson 95 percent) of: R1+ (primary), R1, run 4 R1 (fresh16), 20b, 120b.
2. **Paired difference router minus LLM** (R1+ minus 120b: deciding; R1+ minus 20b: supporting; run 4 minus each: fresh16, report
   only): point estimate, percentile 95 percent CI from a paired bootstrap, 10,000 resamples, seed 42, **clustered by item** (the
   fresh16/fresh17 manifests carry no product ids). Pooled = items of both sets resampled together.
3. **McNemar** exact two-sided p (binomial on discordant pairs) for each paired comparison.
4. Routing cost on S4 gold-no items: the 20b yes-rate (Wilson), and R1+'s routed share on the same items for comparison.
5. Overlap: positives missed by both, by the router only, by the LLM only.

Code: `engine/intent/s4_llm_check.py` (written after this commit), result `reports/intent/s4_llm_check/result.json` (ids and numbers
only, no text). The verifier recomputes every headline number from the stored rows before the results PR merges.

## 7. Decision tree (fixed in advance; the 120b comparison decides, 20b supports) — verbatim from the S32 brief

D1 — Router NON-INFERIOR (pooled paired CI lower bound > -0.05):
    The 0.85 absolute bar was the wrong criterion, but A17's recorded failure stands. Pre-register
    Amendment 18, a CONFIRMATORY test, before any draw:
    - The A17 router unchanged (no retuning).
    - A fresh S4-enriched set from untouched pools via the leakage ledger, sized for >=300 S4 positives
      (~4,000 S4 items at the observed ~7.5% positive rate), four-judge panel on Kaggle, pinned build,
      resume step guarded as fixed in #609.
    - PRIMARY: router recall non-inferior to the LLM comparator on S4 (margin 0.05, paired).
    - Also: overall routing recall lower bound >= 0.90; routed share <= 0.30; non-inferior overall.
    - Comparator: 120b if it fits the cap within ~5 days; otherwise 20b on all items plus 120b on a
      pre-registered random subsample.
    Run it to completion and report. Enabling the router stays GG's decision: write the enable plan
    (run-2 + tau thresholds, monitoring, rollback, the parked encoder-first PR chain #396/#576/#578/#579)
    and park it.
D2 — LLM CLEARLY BETTER (pooled paired CI upper bound < -0.05):
    A genuine gap. Stop router research. Write a costed options note (stratum-specific rule, targeted
    silver data, or accept the gap — the router is worth ~$1.74 per Starter customer per month on paid
    Groq) recommending the router be deferred until a paying customer exists. Park for GG.
D3 — INCONCLUSIVE (CI spans -0.05):
    Power calculation for a confirmatory test. If >=300 S4 positives are reachable within ~1 week of
    Kaggle GPU, proceed as D1. Otherwise report and park as D2.

**Applying the tree.** "Pooled paired CI" = the 95 percent CI of R1+ recall minus 120b recall on pooled S4 gold-yes (section 6, item
2). The tree text is GG's and is kept verbatim; its "~4,000 S4 items at ~7.5%" is superseded by the measured 3.8 percent (section 3):
an Amendment 18 draw is sized for >= 300 S4 positives at the observed rate (about 8,000 S4 items). A CI that is both above -0.05 at its
lower bound and below -0.05 at its upper bound is impossible, so exactly one branch applies. If the 120b arm yields no rows at all, the
20b comparison is used in its place and that substitution is reported as a deviation.

## 8. Deviation log (appended after registration; no result existed when each entry was written)

- **2026-10-30 17:40 UTC, TIMING DEVIATION (GG's advisor, S33).** The analysis runs now on the 147 completed 120b rows, labelled
  INCOMPLETE with n stated, exactly as the section 5 fallback specifies; only the timing changes (registered: at 2026-11-03 06:00 UTC).
  Reason: triage-iq's ADR-0075 recording holds the shared ledger's exclusive recording flag (renewed for the whole run) and, at no
  more than 160K tokens a day for about 919K tokens, holds it past the deadline, while `benchmark` is capped at 20K a day for the
  recording (triage-iq decision D95). No further 120b row can complete before the deadline, so the data are identical either way. The
  decision to run early was taken before any analysis output existed and does not depend on any result. The 147 rows are the first 147
  of the registered Random(42) order, i.e. a random subsample of the 259 pooled S4 positives by construction. Nothing else changes:
  metrics, the decision function and the tree are as committed.
