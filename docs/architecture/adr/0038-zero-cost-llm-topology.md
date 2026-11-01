# 0038. Zero-cost LLM topology: dedicated Groq primary, shared Groq failover, free OpenRouter for evals only

Status: proposed (S20). Builds on 0037 (shared Groq org capacity).

## Context

GG will not pay for LLM usage. Production extraction runs on Groq's free tier, whose organisation-level
limits are shared with GG's other products (ADR 0037). The current failover is OpenRouter
(`meta-llama/llama-3.3-70b-instruct`, ZDR-only) on an account with `total_credits` 0. Measured
2026-10-09 (`scratchpad/s20/f3b_failover.py`): three real-size extractions through
`SecondaryProvider` with the deployed model all returned 200 in 2.2-3.0 s (310 tokens in, 76-145 out, provider
Together), billed against an account whose `total_usage` already exceeds its credits. It works today and
is not guaranteed to: the same account returned 402 for larger models and `:free` is not offered for this model
(404 "This model is unavailable for free"). Nothing makes that failover durable at $0.

Terms read 2026-10-09 (verbatim quotes in the S20 report, WebFetch extraction, not legal advice):
OpenRouter Terms s7 forbids creating "multiple accounts as a single user, for purposes of bypassing or
circumventing use limits". Groq's Services Agreement has no provision on multiple accounts beyond "Groq has no
obligation to provide multiple accounts", and its limits are per organisation.

## Decision

1. Primary: a DEDICATED Groq organisation for Samidha production (its own key), so other products cannot
   starve it. GG creates it.
2. Failover: GG's existing shared Groq account, as a SECOND Groq account (`SECONDARY_PROVIDER_KIND=groq`,
   `SECONDARY_PROVIDER_API_KEY`, `SECONDARY_PROVIDER_MODEL`). Groq to Groq, both ZDR-capable. This is a
   failover used only after the primary fails. It is NOT rotation or load-spreading across accounts to
   multiply a quota, and no code does that.
3. OpenRouter is not used for multiple accounts, ever. Its `:free` models are used for EVALS ONLY, on the CC0
   held-out corpus. Customer data never goes to a non-ZDR endpoint.
4. The OpenRouter failover stays the default (`SECONDARY_PROVIDER_KIND=openrouter`) until GG switches it, so
   merging this changes nothing.

## Consequences

- No code path sends customer text to a non-ZDR provider. An unknown `SECONDARY_PROVIDER_KIND` value falls
  back to the ZDR OpenRouter path, not to anything else.
- Free-tier capacity for Samidha stays unmeasured until the dedicated org exists (ADR 0037).
- If Groq as a whole is down, both legs fail. The OpenRouter leg is then no longer a safety net.
  Residual risk, accepted for $0.
- Whether Groq would treat a second account owned by the same person as acceptable is unverified: the text read
  does not prohibit it, and s6.3(d)(iii) ("in a manner intended to avoid incurring Fees") is the nearest clause.

## Alternatives

- Several OpenRouter accounts rotated for quota: rejected, prohibited by Terms s7.
- Paid OpenRouter credits: rejected by GG's no-spend constraint.
- Gemini free tier as failover: rejected, it trains on inputs.
