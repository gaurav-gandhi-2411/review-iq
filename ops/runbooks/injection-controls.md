# Runbook: field-targeted injection controls (S15d)

Two opt-in tripwires for prompt injection that targets extraction fields (`buy_again`,
`stars_inferred`, `topics`). Code: `app/core/injection_controls.py`. Measured coverage and limits:
`SECURITY.md` section 2 and `docs/specs/s15c-injection-control-options.md`.

**Both are OFF by default. Enabling them is an operator (GG) decision.** They raise the cost of a
naive attack; they do not close the class (9 of 10 hand-written rephrasings evade the input
detector, and the output check sees only forgeries that contradict another field).

## Flags

| Env var | Default | Effect when `true` |
|---|---|---|
| `ENABLE_FIELD_INJECTION_INPUT_CONTROL` | `false` | Sentences that match the input rules are removed from the text sent to the extraction model. The stored review is the original. Language detection and the Layer 4 grounding check use the stripped text. |
| `ENABLE_FIELD_INJECTION_OUTPUT_CHECK` | `false` | `buy_again` is set to null when true against a negative picture; `stars_inferred` is set to null when an extreme value contradicts sentiment or `buy_again`. Also re-checks cache hits on a copy (the stored row is never rewritten). |

Either flag alone is valid. Flag state is read from the process environment at startup
(`get_settings()` is cached): changing a value needs a new Cloud Run revision, never a code change.

## Paths covered

`POST /v2/extract`, `POST /v2/extract/batch` and CSV ingest (via the ingest worker), the Shopify and
Google webhooks (all through `_run_extraction_v2`), `POST /demo/extract`, the v1 `POST /extract`
(non-Cloud-Run only), and the reply engine's grounding extraction (input step only). A static test
(`tests/unit/test_injection_controls_wiring.py`) fails if a new extraction call site skips the
controls. Not covered, by design: authenticity scoring and reply drafting (different tasks, no
extraction schema fields), and the injection-guard classifier itself.

## What callers see

With a flag on, every extraction response gains an optional `injection_controls` object:
`input_stripped`, `input_rules`, `output_nulled`, `output_soft_flags`, `needs_review`. With both
flags off the key is absent (responses are unchanged). A review with `needs_review` is also stored
with `is_suspicious = true`, so it appears in the existing flagged-reviews view.
A nulled `buy_again`/`stars_inferred` is `null` in the response, never a corrected value.

## What to watch after enabling

Structured logs (no review text is ever logged):

- `injection_controls.input_stripped`: `rules`, `sentences_removed`, `chars_before`, `chars_after`, `emptied`.
- `injection_controls.output_check`: `rules`, `nulled`, `cached`.

Baseline expectation from the measurements (Flipkart product reviews): input flags on about 1 in
245,757 real reviews and 0 of 106 held-out; output nulls on 0 of 106 recorded predictions. A
sustained rate well above that means either an attack campaign or a false-positive class the
corpora did not contain (SaaS/app-store text is unmeasured). Investigate before trusting either
reading, and use the flag off to compare.

If every sentence of a review is flagged, the model receives the placeholder
`[review text removed by injection control]` instead of an empty string (`emptied=true` in the log).

## Rollback

Set the flag(s) back to `false` and deploy a new revision. Nothing is persisted that needs
undoing: `injection_controls` is never stored, and `is_suspicious` rows already written stay as
they are.

## Before enabling in production

The end-to-end effect with the controls on has NOT been measured. Run
`eval/run_injection_e2e.py --controls on` and the twin-control experiment
(`eval/run_injection_twin_control.py`) first; both are quota-gated (see their module docstrings).
