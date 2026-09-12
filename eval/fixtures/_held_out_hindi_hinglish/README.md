# Held-out Hindi/Hinglish corpus — QUARANTINED, do not read while writing prompts

Session 8 P3. This directory holds fixtures built specifically to give production models
their first uncontaminated Hindi-sentiment measurement (`docs/specs/wave1-coverage-
abstention-analysis.md`'s ~102-required-n gap). Its entire value depends on nobody —
human or agent — having seen this content while developing `app/core/prompts/**`.

## The quarantine, and how it's actually enforced (not just named)

1. **Structural**: `eval/runner.py::_collect_fixture_paths` only ever walks the flat
   `eval/fixtures/*.json` files plus two explicitly-named subdirectories, `hi-en/` and
   `hi/`. This directory's name matches neither — it is invisible to every eval run
   (CI cassette-replay, local live eval, `eval.runner` imported anywhere) by
   construction, not by convention. Verified: `grep -n '"hi-en", "hi"' eval/runner.py`.
2. **Mechanical leakage check**: `scripts/check_no_heldout_leakage.py` (CI-wired,
   `.github/workflows/ci.yml`) hashes a fingerprint of every held-out fixture's review
   text and fails the build if that fingerprint (or a long enough substring) appears
   anywhere under `app/core/prompts/**` or `PROMPTS.md`. This catches copy-paste
   leakage mechanically — it does not rely on anyone remembering not to.
3. **Policy**: nobody drafting or reviewing a prompt change reads this directory's
   fixture files. If you are working on `app/core/prompts/**` and find yourself here,
   stop — you do not need this content for that work.

## What's in here

LLM-consensus **silver** labels (never human ground truth — see P3e), produced by the
disjoint-family judge panel documented in `docs/architecture/adr/0013-*.md` and
`docs/architecture/adr/0015-*.md`. Source reviews: `eval/data/flipkart_candidates.jsonl`,
`language == "hi-en" or "hi"` (106 + 2 = 108 candidates, `docs/architecture/adr/0014-*.md`).

## What this directory is NOT for

- Not a source of few-shot prompt examples, ever.
- Not scored by the CI gate (structurally excluded, see above).
- Not human-labeled ground truth — every label here is LLM-consensus silver, and must be
  reported as such wherever it's cited.
