# ADR 0015: Judge panel restoration, and a quota-safety gap found before spending on P3

## Context

ADR 0013 removed `openai/gpt-oss-120b` from the consensus judge panel (self-judging
conflict), leaving `qwen/qwen3.6-27b` as the only calibration-passing, disjoint judge — no
inter-rater kappa/alpha is computable with one rater. P3c requires a genuinely
disjoint-from-production panel with real reliability stats for the held-out Hindi/Hinglish
corpus.

## Panel restoration

A live, free `/v1/models` listing call against Groq (no completion cost) found the current
full catalog: `qwen/qwen3.8-27b`, `canopylabs/orpheus-v1-english` (TTS), `meta-llama/
llama-prompt-guard-2-{86m,22m}` (prompt-injection classifiers, wrong task), `whisper-large-
v3(-turbo)` (speech-to-text), `openai/gpt-oss-*` (production family, excluded), `groq/
compound(-mini)` (Groq's own agentic router, unclear internal model lineage — not used
without verifying what it's built on), `allam-2-7b` (already failed calibration).

**`qwen/qwen3.8-27b`** is the only task-appropriate, non-OpenAI-lineage candidate left. It is
the same vendor/family as `qwen/qwen3.6-27b` (Alibaba Qwen) — not fully cross-vendor-disjoint
— but a genuinely different model checkpoint, which is materially better than zero
independent variance. Disclosed plainly: agreement between `qwen3.6` and `qwen3.8` is weaker
evidence of correctness than agreement between two unrelated vendors would be — the same
caution class as, but smaller in degree than, the `gpt-oss-120b` contamination ADR 0013 fixed.

Calibrated fresh against the 16-item control set (16 calls, ~5-8K tokens, a small, already-
spent cost): **0/33 misses, passed cleanly.** `eval/consensus/results/calibration_report.json`
updated: active panel is now `[qwen/qwen3.6-27b, qwen/qwen3.8-27b]`.

## Quota-safety gap found before any further spend

P3d assumed a lever exists to "set the demo endpoint's global cap to 0" for the duration of
any labeling batch, protecting real customer traffic sharing the same Groq org-level budget
(confirmed prior session: Groq rate-limits at the ORG level, not per-key — a dedicated
benchmark key does not isolate quota). **Verified directly: no such lever exists in
production.** GitHub issue #132 ("global daily quota cap + cost recording") is still **OPEN**;
its described implementation (`DEMO_DAILY_REQUEST_BUDGET`, a `demo_daily_usage` table, a
schema migration making `extraction_costs.org_id` nullable) exists only as an unmerged commit
(`805a9ca` on branch `fix/wave0-demo-quota-cap`) — confirmed NOT an ancestor of `origin/main`
(`git merge-base --is-ancestor 805a9ca origin/main` → not an ancestor), and confirmed absent
from the live tree (`grep -r DEMO_DAILY_REQUEST_BUDGET` → zero matches outside that branch).

Real measured Groq limits for the models live today (from issue #132's own
console.groq.com-verified numbers): **200,000 tokens/day, 1,000 requests/day**, shared by
every call any key on this account makes. 60% of that (P3d's authorized ceiling) is 120,000
tokens/day. The full held-out batch (108 candidates x 2 judges = 216 calls) at production's
own measured per-call token rates (en ~1833, hi-en ~1934 tokens/call) would need roughly
390,000-420,000 tokens total — 2x the entire daily budget, let alone the 60% slice — confirming
P3d's own anticipation that this needs multi-day batching, not a one-shot run.

**Decision: pause the labeling batch here, before further spend, rather than run it with zero
isolation from real customer traffic.** #132 is explicitly named in this session's STOP list
(both directly, and indirectly as a schema migration + RLS change) — deploying it is not this
session's call to make unilaterally, and running an unbounded multi-day batch against a shared
budget with no isolation mechanism, real customers depending on the same key, is exactly the
"scarce shared quota where a failed attempt has real cost" class CLAUDE.md's standing autonomy
policy already reserves for explicit escalation.

## Consequences

- P3's held-out corpus is fully prepared (108 corrected candidates, quarantine directory +
  leakage check, a genuine calibrated 2-judge panel) but **not yet labeled** — the actual
  consensus-labeling run against real reviews is deferred pending GG's decision: (a) accept
  the risk and authorize proceeding without a cap, batched across ~4 days at the 60% ceiling;
  (b) authorize deploying #132 first (its own STOP-gated decision); or (c) defer to a later,
  separately-scoped session.
- Already spent: ~16 calibration calls (qwen3.8-27b only), an estimated 5,000-8,000 tokens —
  small, real, disclosed. No labeling-batch tokens spent beyond this.
- Nothing here blocks landing the zero-quota parts of P3 (corpus yield fix, quarantine
  mechanism, panel restoration) — those are independent, complete, and tested.

## Alternatives considered

- **Proceed anyway, reasoning that 16 calibration calls already happened without visible
  harm.** Rejected: a 16-call calibration burst and a 200+-call multi-day labeling commitment
  are different orders of magnitude and risk profiles; the small already-spent cost is
  disclosed, not used to justify a much larger one by extension.
- **Build and merge #132 to unblock the safety mechanism.** Rejected for this session: #132
  is itself explicitly STOP-listed (a schema migration touching `extraction_costs`, the same
  money-adjacent table ADR 0013/Session 7's P4 already flagged for a separate precision fix
  first) — building it to unblock a different piece of work would be scope creep into a
  decision that needs its own sign-off.
