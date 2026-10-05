# ADR 0034: panel-2 silver adjudication of the 60 unscored held-out pairs stopped at the calibration gate

Status: accepted as a negative result (Session 17, G6). Pre-registration: `docs/specs/s17-panel2-adjudication.md`
(committed before any completion call). Every number below is from `eval/consensus/results/panel2_calibration.json`
and `panel2_votes.jsonl`, reproducible at zero cost with `python -m eval.consensus.panel2 --mode replay --phase calibration`
against `eval/cassettes/panel2_cassettes.json` (code at the commit that adds this file).

## Context

ADR 0033: the published held-out headline (79.6% [76.2, 82.8], n=70 unseen reviews) is accuracy where panel 1 agreed;
60 of 560 field-pairs (product 16, topics 15, pros 17, cons 12) were unscored and the assumption-free Manski bounds
are [70.9%, 81.6%]. A point estimate needs an independent adjudication of those pairs, and panel 1 (Alibaba Qwen x2,
Google Gemini) already split on them. Panel 2 had to be vendor-disjoint from panel 1 and from production (OpenAI via
Groq, Meta Llama via the OpenRouter failover, Google Gemini via the dormant fallback), ZDR-routed, and had to pass a
calibration gate before judging anything.

G6a, shipped first and separately: `assert_no_self_judging` now compares vendor family against every production
model read from Settings (Groq small/large, secondary provider model, Gemini fallback, plus the documented Llama
failover), fails closed on an unmapped family, and takes extra forbidden families (the panel-1 vendors). Disclosed
side effect: panel 1's own `gemini-3.5-flash-lite` trips it (Google is the dormant fallback's vendor), so a fresh
panel-1 labeling run now fails loud; existing panel-1 labels are unchanged.

## Decision

Run the pre-registered calibration (28 control items, 69 field checks per candidate: the 16 existing items + 12 new
synthetic Hinglish/Hindi items in `eval/consensus/control_set_hinglish.json`; pass = at most 2 misses) on five ZDR
candidates, and run the target sets only if 3 candidates from 3 vendors pass. The gate was not relaxed.

## Result

Calibration (140 live calls, 88,234 tokens in, 15,077 out, total 0.0160 USD from OpenRouter `usage.cost`; all 140
responses were HTTP 200 and parsed, so no miss is a transport or format failure):

| candidate (vendor) | misses / 69 | pass | missed items (field: expected -> got) |
|---|---|---|---|
| deepseek/deepseek-v4-flash (DeepSeek) | 0 | yes | none |
| nvidia/nemotron-3-super-120b-a12b (NVIDIA) | 2 | yes | calh-004, calh-010 (language: hi -> hi-en) |
| mistralai/mistral-small-2603 (Mistral) | 8 | no | cal-010, cal-011 (stars: 5, 1 -> null); calh-002/003/007/008/009/012 (language: hi-en -> hi) |
| z-ai/glm-4.7-flash (Zhipu) | 4 | no | cal-004, calh-010 (language: hi -> hi-en); cal-010, cal-011 (stars -> null) |
| thinkingmachines/inkling-small (Thinking Machines) | 3 | no | cal-004, calh-004, calh-010 (language: hi -> hi-en) |

Per-candidate cost (USD, tokens in/out): deepseek 0.001692 (17,453/2,721); mistral 0.002836 (17,477/2,709); nemotron
0.002823 (17,785/3,221); inkling 0.006858 (17,203/3,296); glm 0.001826 (18,316/3,130).

**Two candidates pass; the rule needs three from three vendors. Panel 2 was not assembled, T and V were not run, and
there is no r, alpha, kappa, concordance, narrowed interval or silver point estimate.** The published numbers are
unchanged.

What the misses are: 13 of the 17 misses are the `language` field (Devanagari text called `hi-en`, or Latin Hinglish
called `hi`) and 4 are two explicit-star-rating items answered `null` by mistral and glm. These are real judgement misses, not call
configuration: the prompt defines `hi` as Devanagari and every call parsed cleanly. A reader should still note that
`language` is outside the headline fields (it is dropped for label noise, alpha 0.380, ADR 0023) and that none of the
60 unscored pairs is a `language` pair. Counting the table above without `language`, all five candidates would pass
(mistral and glm at exactly 2 misses on `stars`, the other three at 0); that is arithmetic on the recorded misses, not
a rerun. It is a post-hoc change to the gate after seeing results and is NOT applied here.

## Consequences

- The question "what is the model's accuracy on the 60 unscored pairs" stays open; the Manski interval [70.9%,
  81.6%] stands as the honest statement and the point estimate stays unidentified.
- Spend: 0.0160 USD of the authorised 1.00 USD (cap 2.00). The main phase (66 reviews x 3 judges + 30 repeats) was
  estimated at about 0.05 USD from calibration unit costs, BELIEVED, not measured.
- A fresh panel-1 labeling run fails the hardened guard until the Gemini judge is replaced or the Gemini fallback is
  formally retired; this is the intended loud failure, not a regression.
- Recommended next step, needing GG's call because it changes the pre-registered gate: register a v2 gate scoped to
  the fields the 60 pairs actually involve (headline fields) with `language` reported separately, then run T and V
  with the three best candidates from distinct vendors (on current data: deepseek, nemotron, inkling, all 0 misses
  outside `language`). This is exploratory relative to the v1 pre-registration and must be labelled so.

## Alternatives

- Relax the gate to 2 judges or raise the miss tolerance: rejected (the pre-registration forbids it; two judges give
  no majority rule and no Fleiss kappa).
- Substitute other candidates from other vendors until three pass: possible, but each swap after seeing results is a
  forking path; if done it needs a v2 pre-registration listing the roster first.
- A human adjudication of the 60 pairs: out of scope (GG labels nothing).
