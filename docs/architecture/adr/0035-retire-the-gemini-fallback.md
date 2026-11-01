# ADR 0035: retire the dormant Gemini fallback; the Google judge leaves panel 1

Status: accepted (Session 17, X3; GG accepted the ADR 0034 recommendation, 2026-10-05).

## Context

`app/core/llm.py` carried a Gemini failover (`_call_gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `ENABLE_GEMINI_FALLBACK`)
that the v2/org-key path never used and that was dormant in production (`ENABLE_GEMINI_FALLBACK` unset, verified
2026-09-11). It was a standing liability: the Gemini free tier trains on inputs, its default model had silently been shut
down for two months with nothing detecting it (Section F), a nightly probe and a model-availability step existed only to
keep it alive, and it made Google a "production vendor" for the self-judging guard. The hardened guard (ADR 0034, G6a)
then flagged panel 1's own Google judge `gemini-3.5-flash-lite` as sharing a vendor with the fallback.

Before removal, every consumer was grepped across code, workflows, scripts, tests, docs and runbooks (rule 85b: `gemini`,
`GEMINI`, `genai`, `allow_gemini_fallback`, `_call_gemini`, `generativelanguage`, `gemini-api-key`).

## Decision

Remove the Gemini path; OpenRouter (ZDR-only `SecondaryProvider`) remains the only failover.

Application (`app/`): `_call_gemini`, the Gemini branch and `model_hint="gemini"` in `extract_with_llm`; the
`allow_gemini_fallback` parameter of `extract_with_llm` and `route_extraction` (it also gated `assert_privacy_safe` on the
Groq providers; that assertion now runs unconditionally, which is behaviour-preserving because Groq's
`trains_on_input` is False); the `gemini_api_key`, `gemini_model` and `enable_gemini_fallback` settings; the
`gemini-2.0-flash` pricing row (no row for the production default `gemini-2.5-flash` ever existed, so no recorded cost
can be affected); stale prose in `metrics.py`/`router.py`/`pricing.py`.

Per file, tooling and automation:

| file | decision |
|---|---|
| `scripts/probe_failover.py`, `tests/test_probe_failover.py` | removed the Gemini probe; the probe now covers the secondary path only; the generic three-state/acknowledgement logic and its tests stay (a stand-in path label replaces "gemini" in the main-level tests) |
| `.github/workflows/failover-probe.yml` | removed the `GEMINI_API_KEY` env and the `gemini_model` dispatch input / override |
| `.github/workflows/model-availability-check.yml` | no longer extracts `gemini_model`, reads the `gemini-api-key` secret or checks a Gemini model; Groq models only |
| `scripts/check_model_generation.py` (+ test) | Gemini provider removed (Groq only) |
| `scripts/record_cassettes_via_fallback.py` | RETARGETED to OpenRouter only (the Gemini leg is removed); kept because recording cassettes without touching the production Groq key is still useful (its Llama model map is the one recorded in ADR 0003) |
| `eval/consensus/panel.py` | Google judge removed from `JUDGE_MODELS`, recorded in `RETIRED_JUDGE_MODELS` (data only), Gemini call code removed |
| `eval/runner.py` and the eval/benchmark callers | only the removed `allow_gemini_fallback=False` argument and a now-closed "known gap" comment |
| `.github/workflows/eval.yml`, `web-surface-probe.yml`, `benchmark/vernacular_v2/multi_llm_labeler.py`, historical ADRs, `PROMPTS.md`, `eval/README.md`, `legal/*`, `ops/runbooks/clean-break-migration.md`, `service-role-key-rotation.md` | left as history or for GG (see below); no behaviour depends on them |

Docs updated for current state: `.env.example`, `README.md` (stack row, secrets list), `SECURITY.md` (section 3 and 8),
and the cost-check, deploy, cold-start and secret-rotation runbooks.

Panel 1: a fresh run now uses qwen3.6-27b and qwen3.8-27b (plus allam, which fails calibration) and passes
`assert_no_self_judging`. The 106 held-out fixtures, `consensus_labels.jsonl` and `calibration_report.json` were produced
with the old three-judge roster and are untouched; `panel.py`'s docstring says so. The guard's production-model list is now
derived from every model-named Settings field (plus the documented Llama failover), so a Google rule returns automatically
if a Google provider is re-added; panel 2 still forbids Google and Alibaba as panel-1 vendors.

## Consequences

- One failover provider instead of two; no provider that trains on inputs is reachable from the app; one less nightly
  probe leg and one less scheduled secret read.
- A Groq outage with no `SECONDARY_PROVIDER_*` configured now raises "All LLM providers failed" on the legacy `/extract` and
  `/demo` paths too. The probe already reports the unconfigured secondary every night (acknowledged, not silent). Operating
  without any failover is a conscious state, not a new one for the org-key path.
- A fresh panel-1 run has 2 passing judges of one vendor (Alibaba), so its agreement overstates independence; use panel 2's
  roster (ADR 0034) when independent adjudication matters.
- `google-genai` stays in `pyproject.toml`/`uv.lock` unused by the app (BELIEVED removable; dropping it needs `uv lock`
  and a CI run, left out of this change to keep it reviewable).
- Not changed, for GG review: `legal/privacy-policy.md` and `legal/sub-processors.md` still describe Gemini as excluded
  from the customer-data path and reachable on `/v1` behind `ENABLE_GEMINI_FALLBACK`; both statements are now stale in the
  safe direction (Gemini is not reachable at all) but they are legal copy.

## GG actions (nothing here touched production env, secrets or gcloud)

Unused after this change is deployed (do these only AFTER the deploy, so the running revision cannot read a missing
binding):
1. Cloud Run `review-iq` (and `review-iq-admin` if it carries them): unbind the secret-backed env var `GEMINI_API_KEY`
   (Secret Manager `gemini-api-key`), and remove `ENABLE_GEMINI_FALLBACK` and `GEMINI_MODEL` if set (the app ignores
   unknown env vars, so a stale binding is harmless but misleading).
2. GitHub Actions secret `GEMINI_API_KEY` (no workflow reads it any more).
3. Secret Manager `gemini-api-key`: disable then delete once step 1 is verified (note `ops/runbooks/secret-rotation.md`
   and `clean-break-migration.md` still list it; `service-role-key-rotation.md` lists the GitHub secret).
4. Your local `.env` `GEMINI_API_KEY` line, and any other machine's copy.
5. Decide whether to configure `SECONDARY_PROVIDER_API_KEY` / `SECONDARY_PROVIDER_MODEL` in production so a failover
   exists at all; the failover probe stays acknowledged-unconfigured until you do.

## Alternatives

- Keep the fallback and swap the Google judge for another vendor: leaves a train-on-input provider in the code and a
  probe and secret to maintain for a path no customer path uses; rejected.
- Allowlist the Google judge against the guard: the weakest option, a hole in the control that exists to prevent the
  gpt-oss-120b self-judging incident; rejected.
- Keep `allow_gemini_fallback` as a renamed privacy flag: with Gemini gone it only toggled an assertion that always
  passes for Groq; an unconditional assertion is simpler and stricter; rejected.
