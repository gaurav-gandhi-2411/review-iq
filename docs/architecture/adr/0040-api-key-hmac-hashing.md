# ADR 0040: Hash API keys with HMAC-SHA256 and a pepper instead of argon2id

Status: PROPOSED 2026-10-10 (S21). Not to be implemented or deployed without GG: it touches auth and a
schema migration (gate 4), and it changes a security control.

## Context

API keys are `riq_live_` plus 128 bits of `secrets` randomness (`app/auth/keygen.py`). Verification is a
prefix lookup (17 characters, unique index) followed by `argon2id.verify` with the library defaults
(time 3, memory 64 MB, parallelism 4; `app/auth/api_key.py`, `keygen.py`).

Measured (S20 load test, `docs/ops/load-test-s20.md`, one local core): an argon2id verify costs about
60 ms, so API-key traffic is BELIEVED to top out near 17 requests/s per vCPU (arithmetic from the
measured cost, not a measured ceiling). The Groq free tier caps extraction far below that, so today
the LLM quota binds first; Argon2 binds first for any cheap endpoint behind an API key. Each verify also allocates 64 MB; at the configured concurrency of 80 the
worst case is far above a small Cloud Run instance's memory (arithmetic, not measured).

Argon2 exists to slow brute force of low-entropy secrets. A 128-bit random key is not brute-forceable
at any hash speed, so the 60 ms buys no security for this input. It does cost latency, CPU and memory
on the authenticated path, and an attacker can use it as an amplification vector (every invalid key
with a valid prefix still costs a full verify).

## Decision (proposed)

1. Store `key_hash_v2 = HMAC-SHA256(pepper, raw_key)` and a `hash_version` column. Compare with
   `hmac.compare_digest`. The pepper is a 32-byte secret in Secret Manager (`api-key-pepper`), bound
   to the runtime service account only, never in the database.
2. Migration is expand, migrate, contract. Expand: add nullable `key_hash_v2` and `hash_version`
   (default 1). Migrate: on each successful argon2 verify, write the HMAC and set version 2 (lazy
   upgrade, no big-bang rehash, since raw keys are not stored). Contract: after every active key has
   upgraded, or after a stated sunset date, drop the argon2 path and column in a separate PR.
3. Pepper rotation: keep `api-key-pepper` versions; store the pepper version id beside the hash and
   verify against the stored version; rotate by re-verifying on use.
4. Prefix lookup and the unique index are unchanged. Rate limiting by prefix is added in the same
   change to cap invalid-key probing.

## Consequences

- Positive: authenticated-path CPU drops from about 60 ms to well under 1 ms (to be measured with the
  same harness before and after); memory per request drops by 64 MB; the amplification vector closes.
- Negative: a database leak alone is not enough to attack a key under both schemes, but with the
  pepper leaked as well the HMAC is a fast hash. For 128-bit random keys that is still infeasible; the
  scheme must never be reused for human-chosen secrets (admin passwords stay on argon2id).
- Risk: a bug in the lazy upgrade could lock out a key; mitigated by keeping the argon2 path until the
  contract step and by an integration test that verifies both versions.

## Alternatives

- Keep argon2id and tune it down (time 1, memory 8 MB): smaller win, keeps the same wrong-tool shape.
- Plain SHA-256 without a pepper: adequate for 128-bit keys, but the pepper makes a database-only
  leak useless at nearly no cost.
- Short-lived signed tokens (JWT) for the API: a larger change with its own revocation problem.
