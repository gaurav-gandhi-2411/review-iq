# Reply prompt v2.2 vs v2.1: pre-registered evaluation (S31 B1, PR #298)

Status: **PRE-REGISTRATION.** Committed before any drafting or judge call for this evaluation. Results go in a
separate section added afterwards; nothing above "Results" changes once a call has run (a needed change is
logged as a deviation instead).

## 0. Question

Does reply prompt v2.2 (PR #298: critical rules 6-7, the S31 tone-example fix, the invented-detail
redaction and the Hindi/Hinglish commitment patterns) stop invented contact details without making the
drafts worse than v2.1 (main)? Reply drafting stays OFF in production whatever the answer; a pass only
allows #298 to merge.

## 1. Design

### 1.1 Sample

The same 20 held-out fixtures as N4c (`docs/research/reply-drafting-measurement.md` 1.1): the
`random.Random(42)` draw over the sorted non-exposed ids of `eval/fixtures/_held_out_hindi_hinglish/`,
first 20. The runner recomputes the draw and asserts it equals the 20 ids recorded in
`eval/results/reply_drafting_n20.json`. Extraction fed to the drafter, tone rule, no brand, no signature:
exactly as N4c 1.1.

### 1.2 Arms (2 prompt versions x 2 models = 4 arms, 80 drafts)

| Arm | Code | Model |
|---|---|---|
| v21-L | `origin/main` at run time (prompt v2.1, main's guardrails) | `openai/gpt-oss-120b` |
| v21-S | same | `openai/gpt-oss-20b` |
| v22-L | PR #298 head at run time, rebased on the same main | `openai/gpt-oss-120b` |
| v22-S | same | `openai/gpt-oss-20b` |

- Drafts come from the committed `draft_reply` path of each checkout, with `groq_model_large` set to the
  arm's model, so the served text includes that version's post-processing. The raw model text
  (before redaction) is captured too, for the secondary metric.
- Route: Groq **failover org only** (`groq-api-key`); the dedicated production org carries zero eval
  traffic. Groq's no-train configuration (`assert_privacy_safe`) is unchanged.
- Budget: a rolling-24h ledger per model (`eval/experiments/no_routing_stage1.DayLedger` semantics),
  ceiling 100,000 tokens/model, the runner stops before a call that would cross 90,000. Estimate: 40 drafts
  per model x ~1,400 tokens (N4c measured ~1,000 in / ~250-390 out) = ~56K per model.
- One attempt per draft; a 429 waits for the stated Retry-After (max 3 waits) and is logged; a draft that
  still fails is reported missing, not replaced.

### 1.3 Judges

Three judges, none from the gpt-oss family, run on Kaggle Ollama pinned 0.40.6 (sha256 c164ab8b...c0e5e0,
the Amendment 16 build), temperature 0, digests recorded in the run status (the run is rejected if the pin
or a digest is missing):

| Judge | Ollama tag |
|---|---|
| Qwen3 8B | `qwen3:8b` |
| Gemma 2 9B | `gemma2:9b` |
| Llama 3.1 8B | `llama3.1:8b` |

All 80 drafts (both versions) are judged by the same panel, blind to arm and version, in a shuffled order
(seed 42). The N4c v2.1 judgments used different judges and are NOT reused.

Rubric: N4c 1.4 verbatim (`invented_details` list -> `invented_flag`, `coherent`, `grounded` 1-5,
`tone_fit` 1-5), one JSON object per call. Unparseable JSON is retried once; still unparseable counts as a
missing verdict and is reported.

### 1.4 Metrics (per arm, n = 20, Wilson 95% CIs)

- **Hard failure** = `invented_flag` by >= 2 of 3 judges. Also reported: flagged by any judge.
- **Invented contact detail** (deterministic, no judge): an email address, phone number or URL in the
  SERVED draft that is not a substring of the review text, the allowed facts, or a bracketed placeholder.
  Same check on the raw model text is reported as the secondary measure.
- Not coherent (>= 2 of 3), usable (N4c definition), pairwise judge agreement on `invented_flag`.

### 1.5 Success rule (fixed now)

v2.2 **passes** only if BOTH hold:
1. Invented contact details = **0/20 in v22-L and 0/20 in v22-S** (served drafts).
2. Hard failures: **v22-L <= v21-L and v22-S <= v21-S** (counts on the same 20 reviews).

Pass -> #298 may merge through the gate (reply drafting stays OFF). Fail -> reported as a negative result;
#298 stays open, parked. n = 20 cannot show equivalence; a pass means "no evidence of harm at this n".

### 1.6 Privacy and artifacts

Held-out review text goes only to the Groq failover org and to a PRIVATE Kaggle dataset for judging.
Committed artifacts hold ids, arm, model, tokens, caveat kinds, deterministic counts and judge verdicts
only, never review or draft text.

### 1.7 Cost

Groq free tier on the failover org: $0. Kaggle GPU: est. 240 judge calls x ~6 s = ~0.5 GPU-h. No paid spend.

---

## 2. Results (added after the run; nothing above changed)

Pre-registration provenance: sections 0-1 were pushed as commit `0cc5d9aa86169c33655dda8ef4ed4194f57afb26`
(branch `docs/s31-reply-v22-prereg`) before the first drafting call; they are unchanged here.

Artifact: `reports/research/reply_v22_eval.json` (ids, arm, model, tokens, caveat kinds, deterministic counts, judge
verdict counts; no review or draft text). Code under test: v21 = `origin/main` `b3cabc3`; v22 = PR #298 rebased on
`b3cabc3` (head `24da616`). Drafted 2026-10-22 ~21:59-22:03 UTC; judged by Kaggle kernel
`review-iq-reply-v22-judge-run` v1 (1,003 s; Ollama sha256 and all three model digests recorded; 0 null verdicts
of 240). Groq failover org: 120b 59,294 tokens, 20b 61,091 tokens; primary org untouched.

| Arm | Invented contact (served) | Invented contact (raw) | Hard failure (>= 2 of 3) | Any judge | Not coherent | Usable |
|---|---|---|---|---|---|---|
| v21-L | **3/20** [0.05, 0.36] | 3/20 | 20/20 | 20/20 | 0/20 | 0/20 |
| v21-S | 0/20 [0, 0.16] | 0/20 | 19/20 | 20/20 | 0/20 | 1/20 |
| v22-L | **0/20** [0, 0.16] | 0/20 | 19/20 | 20/20 | 0/20 | 1/20 |
| v22-S | **0/20** [0, 0.16] | 0/20 | 18/20 | 20/20 | 0/20 | 1/20 |

Detected languages: 15 `en`, 5 `hi-en` per arm (as in N4c). The redaction step never fired in either v22 arm (the
raw v2.2 text already had no invented contact), so criterion 1 is met by the PROMPT, not by the post-processing.

**Success rule as registered:** c1 met (0/20 and 0/20); c2 met (19 <= 20, 18 <= 19) -> formally PASS.

### 2.1 The judge panel did not apply the rubric (read before using c2)

The hard-failure measure is saturated in every arm (18-20/20, versus 5/20 and 8/20 for the larger N4c judges on the
same reviews). Inspected locally (not committed, licensed text): all three 8B judges list generic thanks, apologies
and "please contact our support team" as invented details, which the rubric explicitly excludes. Mean flagged items per
draft: qwen3 1.35-2.30, gemma2 1.70-2.55, llama3.1 2.05-2.95; llama3.1 flagged at least one item in all 80 drafts.
Pairwise agreement on `invented_flag` 0.70-0.94, mostly agreement on "flagged". A measure that flags nearly every draft
cannot show that v2.2 is "not worse", so **c2 is uninformative**, not evidence. (Mean flags are lower for v2.2 than
v2.1 for qwen3 and gemma2, a direction only.)

### 2.2 Decision

- c1 is VERIFIED (deterministic) and is the change #298 exists for: invented contact details 3/20 -> 0/20 on the large
  model, from the prompt itself.
- c2 is NOT established. #298 is **not merged** on this run; it stays open and parked for GG (reply drafting is OFF
  either way). Treating a saturated judge as a pass would be exactly the broken-measurement trap.
- Next step, to be pre-registered BEFORE it runs: a calibration gate for judges (synthetic drafts with known invented /
  clean labels; a judge is admitted only above a fixed accuracy) and a larger non-gpt-oss panel, re-judging the SAME 80
  drafts (no new drafting, no new Groq spend).

---

## 3. Re-judge with a calibrated panel: PRE-REGISTRATION (added 2026-10-24, S31, before any re-judge call)

Why: section 2.1 showed the 8B panel saturated, so c2 ("v2.2 hard failures not worse than v2.1") was not established. This section
fixes, before it runs, a calibration gate and a larger panel. The 80 drafts, the rubric (1.3), the hard-failure definition (>= 2 of 3) and
the success rule (1.5) are unchanged; c1 (deterministic, met) is not re-measured.

- **Panel:** `qwen3:14b`, `phi4:14b`, `gemma3:12b` (no gpt-oss family), Ollama 0.40.6 pinned and sha256-checked, temperature 0, the
  same JSON rubric and one retry; model digests recorded; a run without them is rejected.
- **Calibration set:** `eval/fixtures/reply_judge_calibration.json`, 20 SYNTHETIC cases written for this (10 drafts with only what the rubric
  allows, 10 with one planted invented detail: email, phone, URL, order number, discount code, a named person, a refund/replacement
  promise, a timeline, a policy claim, an invented product cause). The calibration items are shuffled in with the 80 drafts (seed 42),
  blind; the judge cannot tell them apart.
- **Gate:** a judge is ADMITTED if its `invented_flag` matches `expected_invented` on at least 18 of 20 calibration cases. The re-judge is
  VALID only if all three are admitted. If fewer than three are admitted the result is INCONCLUSIVE: no judge is swapped, #298 stays parked.
- **If valid:** the success rule 1.5 is applied with this panel's hard failures (c2) together with the already-measured c1. Pass -> #298 may
  merge through the gate (reply drafting stays OFF). Fail -> negative result, #298 stays parked.
- **Report only:** each judge's calibration confusion (clean flagged / planted missed), flagged-by-any-judge and mean flags per draft
  (the saturation check that failed in 2.1), pairwise agreement.
- **Cost:** Kaggle GPU about 1 h; no Groq or paid spend.
