# ADR 0010: Verifier Review Reliability Record + Scope of the Eval-JSON Gate-3 Carve-out

**Status:** Accepted — process record, not a code change in this repo.
**Date:** 2026-08-01
**Scope:** (1) An honest reliability record for the 6 dispatched verifier reviews posted to
PRs #16, #24, #25, #31, #40, #49 during the merge-gate incident follow-up, so anyone reading
those reviews later knows their measured error rate rather than assuming they're infallible.
(2) The exact, current scope of rule 70a gate 3's generated-artifact carve-out for review-iq's
eval-results JSON — what's verified, what's merely trusted, and what would close that gap.

## Context

After the 2026-08-01 merge-gate incident (5 PRs landed ungated via a shell-variable bug in the
merge hook, see `claude-config` PR #12), 6 oversized-but-legitimate PRs (#16, #24, #25, #31, #40,
#49) needed human-quality review without a human reading 16,736 lines by hand. Six independent
verifier subagents were dispatched in parallel, each producing a diff-level review artifact
posted directly to its PR as a GitHub comment, on the explicit understanding that the project
owner would merge on the strength of that artifact.

## Decision — reliability record, stated plainly

Of 6 reviews dispatched in that one batch, **3 contained a real defect**, found only because
each was independently spot-checked against the actual diff after the fact:

1. **PR #25's review** claimed 400+ lines of undisclosed web/ UI content (`ApiKeys.tsx`,
   `FlaggedReviews.tsx`, a probe script) were bundled into the PR without being described in the
   PR body — recommending "DO NOT MERGE AS-IS." Verified false: `gh pr diff 25 --name-only`
   listed exactly 20 files, all under `benchmark/vernacular_v2/corpus_pipeline/` and two ADRs —
   nothing under `web/`. A direct `git diff origin/main...HEAD -- web/src/pages/ApiKeys.tsx`
   returned empty; those files are byte-identical to `main` (merged via a separate PR) and were
   never part of this PR's diff. Root cause: the review treated the branch's accumulated file
   tree (which includes everything already merged from `main`) as if it were this PR's own diff.

2. **PR #31's review** listed `app/core/llm.py` under a "## What Changed" heading, formatted
   identically to genuinely-diffed files (`app/core/providers/secondary.py`,
   `.github/workflows/failover-probe.yml`). Verified false: `app/core/llm.py` is not in
   `gh pr diff 31 --name-only`'s output, and a direct `git diff` against it on this branch is
   empty. The file's *existing* behavior (calling `assert_privacy_safe()` before `complete()`)
   was legitimately read for context — understanding how the new component gets wired in is a
   normal and correct thing to check — but presenting that read as part of "what changed" is a
   false claim about the diff, buried inside an otherwise-correct security analysis.

3. **PR #49's review** asserted "~150 reviewable lines + ~40 generated, well under 400" for
   gate 3. The PR's real size, per `merge_gate.py`'s own `evaluate_size_gate()` (the actual
   mechanical check that decides eligibility) is **444 reviewable lines — over the ceiling, not
   under it**. This is a different failure mode from (1) and (2): the file list discussed was
   correct, so the diff-manifest self-check added afterward (see below) would not have caught
   it. The number was an impression, not a measurement — no command was run that would have
   produced "150," and the real command (`evaluate_size_gate()`) was never invoked before the
   claim was written.

**A false "safe to merge" from any of these failure modes is strictly worse than a false alarm**:
a false alarm (like #25's) invites scrutiny and gets caught, as it did here. A false negative
gets no equivalent scrutiny by default — which is exactly why this ADR exists: the two false
alarms in this batch got checked because a blocking verdict is inherently suspicious; nothing
forced an equivalent check on the 5 SAFE verdicts until this pass was done deliberately.

The other 3 reviews (PR #16, #40, and PR #24, modulo one minor completeness gap — `test_admin_
api.py` was never named despite its corresponding endpoint being discussed) held up under the
same scrutiny. PR #49's review's content assessment (the react-router migration, the CVE gate,
the deploy-guard script) was independently re-verified and found accurate — only its size claim
was wrong.

## Fixes applied

- `claude-config` PR #16: added a **DIFF-LEVEL PR REVIEW SCOPE DISCIPLINE** section to
  `agents/verifier.md`. The only source of truth for "what changed" is now `gh pr diff <n>
  --name-only` — never a branch/repo file listing. A mandatory self-check
  (`DIFF-MANIFEST SELF-CHECK`) must be reported on every diff-level review: every file discussed
  must be cross-checked against the real manifest in both directions.
- `claude-config` PR #18: added a **QUANTITATIVE CLAIMS MUST BE MEASURED, NOT ESTIMATED**
  section to both `agents/verifier.md` and `agents/executor.md`. Every quantitative claim (line
  counts, test counts, coverage, timings) must carry the exact command that produced it and its
  actual output, or be explicitly marked "NOT MEASURED THIS RUN" — this is what would have
  caught PR #49's line-count error, since the self-check above only verifies *which files* are
  discussed, not the *numbers* attached to them.

## Decision — eval-JSON gate-3 carve-out scope (rule 39b follow-up)

PR #16 (Wave 1 Section A) makes `eval/results.json` and `eval/results/latest.json`
machine-owned, and per CLAUDE.md rule 39b a generated-artifact carve-out from gate 3's line
count is conditional on a real regeneration proof, not just intent. `scripts/check_eval_
results_reproducible.py` (added to PR #16's own branch) provides that proof for exactly these
two files: it regenerates both via the existing $0/deterministic cassette-replay mechanism and
fails if the substantive content (everything except `generated_at`/`git_sha`/`mode`/per-fixture
`latency_ms`, all confirmed non-substantive by testing against the real committed cassettes)
differs from what's committed. `claude-config` PR #17 adds the matching `DESIGNATED_PATTERNS`
entries so gate 3 actually applies the carve-out.

**This carve-out is deliberately partial, and that partiality is a decision, not an oversight.**
`eval/results/authenticity_latest.json` and `eval/results/agreement_latest.json` are **NOT**
covered — they remain fully reviewable under gate 3, and their content is **trusted, not
independently verified as reproducible**, for a real reason in each case:

- **`authenticity_latest.json`**: `eval/authenticity/runner.py` has a `--dry-run` mode, but it
  is explicitly documented as "skip Groq calls, use heuristics-only scoring" — a categorically
  different, cheaper scoring path, not a byte-for-byte replay of the real committed run. There
  is no cassette-backed replay mode for this scorer today, unlike the main extraction path.
- **`agreement_latest.json`**: written by `eval/consensus/run_consensus.py`, a 2-judge-panel
  LLM-labeling pipeline that included a human-reviewed calibration step (dropping
  `allam-2-7b`, excluding `llama-3.3-70b-versatile` as a self-judging conflict). This is a
  one-time labeling process whose output reflects a judgment call, not an idempotent
  computation — "regenerating" it doesn't mean "reproduce the same bytes," it means "re-run an
  expensive judging pipeline and hope the same calibration decisions get made again."

**What would close this gap, if it's ever worth the cost:**
- For `authenticity_latest.json`: build a cassette-replay mode for `app.core.authenticity.
  engine.score_single` (parallel to the one `eval/runner.py` already has for `extract_with_llm`)
  and a check script following the same shape as `check_eval_results_reproducible.py`.
- For `agreement_latest.json`: this one may not be closeable the same way at all — a labeling
  pipeline with a human-calibration step is not naturally idempotent. The realistic path is
  either (a) freezing the calibration decision as code (no re-judging, just re-applying the
  frozen panel/weights to the same fixture set deterministically) and checking reproducibility
  of *that* narrower step, or (b) accepting that this file is provenance-tracked
  (`generated_at`/`git_sha` already present) but never mechanically gated, and relying on human
  review whenever it changes.

Until either exists, these two files stay outside the carve-out on purpose — trusted by human
review each time they change, not by a standing mechanical check.

## Consequences

- Future diff-level verifier dispatches should be measurably more reliable against this exact
  failure class, but this ADR's own reliability count (3/6) is the baseline to compare against,
  not a promise that the fixed process catches everything. Re-audit the next batch of dispatched
  reviews the same way (spot-check SAFE verdicts, not just blocked ones) before trusting the
  fixed process fully.
- `eval/results.json` / `eval/results/latest.json` diff size is now correctly discounted from
  gate 3 for review-iq PRs. `authenticity_latest.json` / `agreement_latest.json` are not, and PR
  bodies touching them should not claim they're carved out.
