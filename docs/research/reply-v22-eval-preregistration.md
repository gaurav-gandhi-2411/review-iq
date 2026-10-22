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
