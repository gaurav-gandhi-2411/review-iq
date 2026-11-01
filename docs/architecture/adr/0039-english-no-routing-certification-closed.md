# ADR 0039: English no-routing certification is closed without a verdict

Status: accepted 2026-10-10 (S21). Closes the open item in `docs/specs/s15c-language-routing.md`
(option ii, unified prompt, no routing).

## Context

The pre-registered non-inferiority rule for adopting a unified prompt needs a paired comparison with
a lower 95% bound above -3 percentage points. The spec already records that n=30 cannot certify that
margin, and that the English fixture set (143 to 283 items) needed fresh, independently labelled items.
Labelling them on free OpenRouter models was tried in S20 (14 fresh items, 44 requests, USD 0):
only 6 of 14 items received two or more valid labels, one of the three panel models returned 429 on
every request, and the daily cap makes the full set take roughly 9 to 50 days. The result was
inconclusive, not negative. Paid labelling was ruled out by the no-spend constraint.

## Decision

1. Stop pursuing the English no-routing certification as a separate project. Language routing stays
   exactly as deployed. No claim that a unified prompt is equivalent is made anywhere.
2. The labelled English evidence it needed will now come, if at all, from the review-intent labelling
   panel (S21 Part 3, A4: three local models from different families on the RTX 3070, silver labels,
   agreement-gated). That panel produces its own sealed test set, so the certification is not needed
   as a dedicated spend of quota.
3. Reopen trigger: a sealed English set of at least the size the design file specifies exists with
   an A2c consensus fraction published beside it. Until then the stage-1 cassette files stay
   uncommitted and the result is "not measured", not "no difference".

## Consequences

- No wasted quota on a question the data cannot yet answer.
- The routing complexity (two prompts) stays, with its known maintenance cost.
- The S20 exploratory free-panel run remains documented as inconclusive in the labelling plan.

## Alternatives

- Pay for a cheap labelling panel (about USD 0.06 to 0.12): rejected for now by the no-spend rule.
- Certify on n=30: rejected, the spec already shows it cannot separate the margin.
