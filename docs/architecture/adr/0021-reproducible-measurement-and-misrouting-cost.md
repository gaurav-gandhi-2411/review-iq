# ADR 0021: The 67.9% number is reproducible, and the 12.3pp gap is two problems, not one

## Context

Session 10's 67.9% figure (ADR 0018) was measured against the live demo endpoint over HTTP,
with no cassette, in a session where the demo cap was zeroed and restored twice and one window
briefly collided with an in-flight measurement. It could not be re-derived, regression-tested,
or defended as the number that would go in front of a customer. Session 11 P1 fixes this.
Separately, ADR 0019 found production's language detector agrees with the corpus only ~48% of
the time, and P3 asks whether that's misrouting the extraction pipeline, not just under-
measuring it.

## Method

New script, `eval/score_held_out_corpus_v2.py`, supersedes ADR 0018's HTTP-based one. Calls
`extract_with_llm` **in-process** — the same function `app/api/demo.py` and `app/api/v2/
extract.py` both call — using the exact cassette mechanism (`app.core.providers.cassette`,
`EVAL_CASSETTE_MODE=record`/`replay`) the 49-fixture CI-gate set already uses, pointed at a
separate store (`eval/cassettes/held_out_cassettes.json`) so it never touches or depends on
`eval/cassettes/cassettes.json`.

**A real methodology gap found first**: neither `eval/runner.py::run_single` nor
`run_single_routed` — the functions behind every published CI-gate number — ever calls
`detect_language()`. Both force-feed the fixture's own ground-truth language into
`build_prompt`. Real traffic (`demo.py`, `v2/extract.py`) calls `detect_language()` first and
routes on whatever it returns. **Every published number this product has ever reported was
measured with language routing assumed correct, never as auto-detected** — this is a second,
independent contamination source from prompt/fixture overlap, not previously named.

Given that, the script measures two conditions per fixture:
- **as_deployed**: `detect_language()` picks the prompt, exactly like real traffic. This is
  P1's number.
- **language_forced**: the fixture's own ground-truth language is forced into `build_prompt`,
  exactly like the CI-gate set's own methodology. The delta between the two is misrouting cost,
  isolated from prompt-quality contamination (P3b).

Only fixtures where detection disagrees with ground truth need a second call (same-language
items reuse the `as_deployed` result — identical prompt, identical cassette key).

## Results

**All 106 held-out fixtures scored, cassettes recorded, then independently replayed with zero
network calls — byte-identical output on every field.** P1a/P1b answered directly: the number
reproduces.

| Measurement | Score | 95% CI | n |
|---|---|---|---|
| **as_deployed** (P1's number — real routing) | **68.3%** | [66.3%, 70.3%] | 106 |
| language_forced (routing always correct) | 72.9% | [71.1%, 74.6%] | 106 |
| Currently published hi-en (contaminated, main) | 80.6% | — | 15 |

**Session 10's 67.9% (n=23) vs. this session's 68.3% (n=106): a 0.4-point difference, well
inside the n=23 result's own CI ([63.6%, 72.3%]).** The cap-collision incident did not
meaningfully contaminate the earlier live measurement — reassuring, and worth saying plainly
rather than only reporting the new, tighter number.

**The contamination gap decomposes into two real, separately-measurable effects**, not one:

- **Language misrouting cost: 4.6pp, 95% CI [2.8pp, 6.4pp]** (paired bootstrap, same 106 items
  under both conditions) — excludes zero. This is what auto-detection getting the wrong prompt
  ~48% of the time actually costs, isolated from everything else.
- **Remaining contamination (language-controlled): 7.8pp, 95% CI [2.0pp, 12.8pp]** (published
  hi-en vs. `language_forced`) — still excludes zero, still real, but visibly weaker and noisier
  than the raw 12.3pp figure once misrouting is factored out.
- These two approximately sum to the raw gap (4.6 + 7.8 = 12.4pp ≈ the directly-measured 12.3pp,
  95% CI [6.5pp, 17.4pp] at n=106, tighter than ADR 0018's n=23 figure of [5.9pp, 19.2pp] as
  expected) — internally consistent, not two independent claims that happen to add up by luck.

## Decision

- `eval/score_held_out_corpus_v2.py` and `eval/cassettes/held_out_cassettes.json` are the
  canonical, reproducible artifacts for this measurement going forward — supersedes ADR 0018's
  live-HTTP script (kept in the repo for provenance, not deleted, per this repo's honest-
  documentation convention; no longer the source of the published number).
  `eval/results/held_out_scoring_v2.json` is the result artifact.
- Still provisional, still LLM-consensus silver, still not published to any public surface as
  of this ADR (P4 handles the actual publishing decision).
- Language misrouting is a real, quantified, separate defect from prompt/fixture contamination
  — both need addressing, and conflating them (as the raw 12.3pp gap does) understates how much
  of the gap is a detector bug with a clear fix path (ADR 0018's threshold-divergence
  discussion) versus genuine prompt-quality contamination requiring different work.

## Consequences

- Every future contamination measurement on this corpus should report both conditions, not
  just `as_deployed` — collapsing them back into one number would re-introduce exactly the
  conflation this ADR exists to resolve.
- The 49-fixture CI-gate set's own numbers should be understood as `language_forced`-equivalent
  (routing assumed correct) — they were never measuring what a real customer's language-
  detection experience looks like, a limitation not previously documented anywhere in this
  project's eval methodology writeups.
- This does not change the CI-gate set's role as a change detector (ADR 0001) — it changes what
  that role should be understood to cover.

## Alternatives considered

- **Report only `as_deployed` and skip the isolation experiment.** Rejected: P3b's explicit ask,
  and the resulting finding (misrouting is a separately fixable, separately quantified 4.6pp)
  is more actionable than one blended number — it tells you where to spend effort first.
- **Retrofit `eval/runner.py` to call `detect_language()` for the 49-fixture set too.** Out of
  scope here: that would change the CI-gate set's own long-standing methodology and every
  historical number's comparability, a bigger decision than this measurement PR should make
  unilaterally. Named as a real gap; not fixed in this PR.
