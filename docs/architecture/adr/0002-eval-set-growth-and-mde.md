# ADR 0002: Eval-Set Growth via Multi-LLM Consensus, and the Real Achievable Size

**Status:** Accepted
**Date:** 2026-07-30
**Scope:** Wave 1 Section B ("Kill hand-labeling") of `docs/specs/wave1-commercialization.md`.
Builds a Krippendorff's-alpha/Fleiss'-kappa-verified inter-rater agreement module, a
2-judge consensus labeler for the CI eval-fixture schema, and grows `eval/fixtures/`
from 49 to 132 fixtures using it. Does not touch Sections A or C–H, `eval/runner.py`'s
gate thresholds, or the tiered router.

## Context

The task's original framing targeted growing the eval set toward >=300 fixtures. Three
independent constraints, each verified live rather than assumed, made that target
unreachable this session:

1. **Real corpus yield for hi-en/hi is a hard ceiling, not a sampling artifact.**
   `eval/data/sample_flipkart.py` regenerated `eval/data/flipkart_candidates.jsonl`
   fresh from the same 3 public Kaggle Flipkart datasets used to build the original 15
   hi-en fixtures. Out of 14,552 unique candidates (from ~200k raw rows before length
   filtering and dedup): only 51 classified as `hi-en` (Latin-script Hindi/English
   code-mix) and only **2** classified as `hi` (Devanagari script). This matches
   `eval/data/README.md`'s already-documented "50–200 out of 200k rows" caveat almost
   exactly — it is not new information, but this session reconfirmed it against a fresh
   pull rather than trusting the old note. After excluding the 15 hi-en texts already
   committed as fixtures, 33 new hi-en candidates remained (in the 30–600 char range);
   Hindi had only 2 real candidates total, too few to matter.
2. **A dedicated-benchmark-key Groq free-tier quota wall, hit mid-run, is the same
   failure class already documented for this exact key.** `benchmark/vernacular_v2/
   SILVER_REPORT.md` already records "120/210 candidates have a successful v2.3
   extraction (the rest hit real Groq daily-quota limits on the dedicated benchmark
   key)". This session's live consensus-labeling run (282 items: 49 existing fixtures
   re-validated + 233 growth candidates, each requiring 2 concurrent judge calls) hit
   the identical wall partway through: 90 of 233 growth candidates (57 English, all 33
   hi-en) got **zero response from either judge** (confirmed via each item's raw
   `judge_outputs` in `eval/consensus/results/consensus_labels.jsonl`, not inferred from
   the pass rate) — a rate-limit/quota exhaustion, not a model-quality signal. This
   consumed a modest, expected one-time budget (~700 live calls across calibration,
   validation, and growth combined) within the constraint's own stated envelope ("a few
   hundred free-tier calls... is expected and fine"), and is not re-attempted further
   this session to avoid repeatedly hitting the same wall (per the standing "no
   idle-waiting / no re-attempting a recorded block" policy) — recoverable on a fresh
   quota window.
3. **Panel calibration genuinely dropped a judge, shrinking the effective panel from 3
   to 2.** The original 3-model panel (`openai/gpt-oss-120b`, `qwen/qwen3.6-27b`,
   `allam-2-7b`) was calibrated against a 16-item unambiguous control set before any
   real labeling, per spec. `allam-2-7b` failed reproducibly across two independent
   runs (9/33 field checks wrong both times, same items/fields) and was dropped, not
   tuned around — see `eval/consensus/panel.py`'s module docstring for the full
   diagnosis (including a separate, since-fixed call-configuration bug that had
   initially also failed `qwen/qwen3.6-27b` for an unrelated reason). This does not
   change eval-set *size*, but it does mean "majority" and "unanimous" collapse into
   the same outcome for the whole run (2 raters: agree, or split — no 3-way tiebreak
   was ever possible with 2), a materially different reliability regime than the
   3-judge design the spec described.

## Decision

1. **Grow English aggressively (real corpus is abundant); accept hi-en/hi are capped
   by real data, not by choice.** Of 200 English growth candidates submitted, 83 passed
   the consensus growth gate (unanimous/majority on all of sentiment/urgency/buy_again/
   language) and were committed as new fixtures (`eval/fixtures/029_consensus_grown.json`
   through `111_consensus_grown.json`). Of the 117 that did not: 60 failed on genuine
   panel disagreement (a real, useful signal — these are the ambiguous cases a
   consensus mechanism is supposed to catch) and 57 were lost to the rate-limit wall
   above (not a quality signal). hi-en gained 0 new fixtures this session (all 33
   candidates lost to the same wall, real yield ceiling untested this run); hi gained 0
   (real yield of 2 candidates makes an attempt pointless).
2. **Final eval-set size: 132 fixtures (110 en, 15 hi-en, 7 hi), not 300.** Reported as
   the real achieved number, not a wished-for one, per the task's explicit instruction.
   See `eval/results/agreement_latest.json` for the full breakdown (also rendered in
   `README.md`'s "How eval fixtures are labeled" section via
   `scripts/render_metrics.py`'s `consensus_labeling` block).
3. **MDE at n=132: ~17.2 percentage points (worst-case, p=0.5) / ~12.7 points (at the
   current overall score, p=0.838)**, alpha=0.05, power=0.80, standard two-independent-
   proportions z-test (`eval/power_analysis.py`, formula and z-critical-value
   provenance documented in its module docstring and unit-tested against published
   constants). In plain terms: this eval set can reliably detect a ~13–17 point
   regression between two prompt/model versions, not anything finer. The original
   49-fixture set could only detect ~21–28 points (see per-n figures in
   `eval/results/agreement_latest.json`'s history) — a real, if partial, improvement,
   not the order-of-magnitude tightening a 300-fixture set would have given (~7–9
   points at that size, for context, not measured).
4. **Validation pass: the new consensus mechanism substantially agrees with the
   original 49 fixtures' existing ground truth** — 87.8% (sentiment), 84.6% (urgency),
   91.9% (buy_again), 93.0% (language), 98.0% (stars) agreement, computed only where
   the 2-judge panel itself reached a consensus (not forced). This is reassuring (no
   evidence the original hand-labeled/Claude-Sonnet-labeled fixtures are systematically
   wrong) but the ~6–15% disagreement per field is a real, reported finding, not hidden
   — see `eval/results/agreement_latest.json`'s `validation.per_field` for exactly
   which fixtures disagreed.
5. **Reliability of the panel itself, across all 282 labeled items:** Krippendorff's
   alpha — sentiment 0.834, buy_again 1.000, language 0.764, urgency (ordinal) 0.830,
   stars_inferred (ordinal) 0.951. By the conventional (if contested) Landis-Koch
   bands, every field lands at "substantial" (0.61–0.80) or "almost perfect" (0.81–1.00)
   agreement — a materially reliable 2-judge panel for this task, not just "better than
   nothing." Fleiss' kappa (secondary, fully-covered-subset cross-check, no ordinal
   support) is lower on `buy_again` specifically (0.582 vs alpha's 1.000) — a real,
   reported discrepancy between the two measures' different chance-correction
   assumptions on a mostly-binary field with many null votes, not an error in either.
6. **New fixtures need a cassette-recording pass before CI's eval gate scores them —
   flagged, not silently left for CI to discover.** `eval.yml`'s cassette-replay gate
   has no recorded cassette for review text that didn't exist before this session, so
   it will fail loudly (by design) on every new fixture until cassettes are re-recorded
   against production's live model. That requires a live call against production's
   `GROQ_API_KEY`, explicitly out of scope for this consensus-labeling session
   (constrained to the dedicated benchmark key only) — left as a named follow-up in
   `README.md`'s "Known gap" callout, not worked around or hidden.

## Consequences

**Positive:**
- Eval-set size nearly tripled (49 -> 132) using a real, auditable, non-single-model
  labeling process, with every fixture's per-field agreement level recorded in its own
  `labeling_meta.agreement_per_field` (not just a pass/fail).
- A genuinely reusable inter-rater-reliability toolkit (`eval/agreement.py`,
  `eval/power_analysis.py`) now exists for any future consensus-labeling or eval-growth
  work in this repo, textbook-verified rather than self-consistency-tested.
- The calibration mechanism proved itself on a real run: it caught one judge's
  reproducible failure (`allam-2-7b`) and correctly separated a second judge's
  call-configuration bug (`qwen/qwen3.6-27b`'s thinking-mode token exhaustion) from a
  genuine quality problem, fixing the latter and validating the former's drop with two
  independent runs — the gate did real work, not rubber-stamping.

**Negative / residual risk:**
- 300 fixtures was not reached, and per points 1–2 above, hi-en and hi did not grow at
  all this session. hi-en has a real, if small, additional ceiling (33 more candidates,
  untested this run due to the quota wall) recoverable on a fresh Groq quota window; hi
  has essentially no more real-corpus headroom from these specific Kaggle datasets.
- The panel is 2 judges, not 3, for this run. "Majority" is not a distinct outcome from
  "unanimous" with 2 raters — a materially weaker consensus structure than a genuine
  3-way vote would give, though still a meaningful improvement over any single model.
- New fixtures cannot be scored by CI until cassettes are re-recorded (point 6) — this
  ADR's growth work is not yet "live" in the CI gate sense, only committed and
  available for a future recording pass.
- The 60 English candidates that failed on genuine panel disagreement, and the fixture
  set's own known field-level disagreement patterns (buy_again's Fleiss/alpha gap,
  urgency's real-data agreement rate of 84.6%), are documented but not further
  investigated in this session — a natural next audit target if urgency/buy_again
  precision matters to a future decision.

## Alternatives considered

1. **Force exactly 300 by loosening the growth gate** (e.g. accepting a single judge's
   answer, or treating "split" as pass-through with a coin-flip resolution). Rejected —
   this is precisely the "no single-model ground truth, no forced tiebreak" anti-pattern
   the whole section exists to eliminate; a bigger eval set built on a weaker consensus
   standard is not actually a stronger eval set.
2. **Retry the 90 rate-limited candidates immediately with a second live run.** Rejected
   for this session — the quota wall is an external, time-based constraint (daily reset
   window), and immediately retrying against the same exhausted key would almost
   certainly hit the identical 429s again, burning more calls/cycles for no new
   information. Documented as a recoverable follow-up (retry after the daily window
   resets) rather than pushed through now.
3. **Source a 3rd Groq-hosted judge to keep the panel at 3, or use Gemini as the 3rd.**
   Considered and rejected — Groq's `/v1/models` endpoint (queried live) currently hosts
   no other non-Llama, genuinely-different-lineage text-generation model on the free
   tier beyond the two that passed calibration; `GEMINI_API_KEY` is wired into
   production as the `SecondaryProvider` failover model (`app/core/llm.py`), so using it
   here would violate the "no live calls against a production-traffic key" constraint
   regardless of whether its documented `limit: 0` billing gap still applies. A
   dedicated benchmark-only Gemini key would need a new sign-up GG would have to
   perform (the same "one-time, GG" pattern `benchmark_groq_key.py` already documents
   for Groq) — not something to create autonomously in this session.
4. **Attempt to synthetically generate more Hindi review text** (as the original 6 `hi`
   fixtures were) to work around the real-corpus yield ceiling. Rejected for this
   section — Section B's mandate is killing single-model LABEL generation, not
   review-TEXT generation; synthesizing more Hindi text would still need labeling by
   *something*, and doing so here would conflate two different problems (corpus
   scarcity vs. label-quality) under one fix. Left as an explicit non-goal, flagged in
   `eval/fixtures/hi/README.md`.
