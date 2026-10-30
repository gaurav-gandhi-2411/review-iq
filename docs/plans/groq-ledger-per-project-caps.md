# Proposal: per-project caps on the machine-wide Groq budget ledger (PARKED for GG)

Status: PROPOSAL, S33 (2026-10-30). Nothing here is implemented. The ledger is triage-iq's code
(`triage-iq/tools/groq_budget/groq_budget.py`) and its live config is machine-wide (`~/.groq_budget/config.json`); changing either
changes other projects' behaviour, so under GG's S33 instruction this is written up and parked, not applied.

## What is true now (VERIFIED 2026-10-30 ~14:10 UTC by reading the code, the config and `groq_budget.py status`)

- **Allocation for `openai/gpt-oss-120b`** (`BUILTIN_CONFIG`, then `config.json` layered on top): daily cap 200,000, production
  reserve 40,000 (never allocated), so 160,000 allocatable over a rolling 24 h. Per-project caps exist only for projects named in
  `projects`: built-in `benchmark` 100,000, **overridden to 20,000** in `~/.groq_budget/config.json` (written 2026-10-30 10:37 UTC;
  triage-iq decision D95: "lowered to 20K/day ... for the recording; the original value is recorded and restored when the recording
  finishes"). **`triageiq-recorder` has no cap**: it may take all 160,000.
- **The recording-active flag is an exclusive lock.** While `recording.json` names a live pid and an unexpired TTL, `check()` answers
  `WAIT [recording_active]` to every other project, whatever the headroom. triage-iq #375 (`8e61ed4`) renews the flag for the whole
  unattended run.
- **Now:** `triageiq-recorder` 156,811 / 160,000 (25 entries, 10:38 to 11:24 UTC 10-30); flag held by pid 25380. The recording is
  ADR-0075 plan A, about 919K tokens (estimate 918,124); at no more than 160K a day it ends about 11-04 to 11-05 (BELIEVED, arithmetic).
- **What the reserve does not cover** (from #629): production serving and every non-ledger caller (gg-portfolio chatbot, DealHunter
  demo profile, others listed in `docs/STATUS.md`) spend from the same org without asking the ledger. The 40K reserve is only a
  promise among ledger-wired projects.

## Effect

- Samidha's production failover reads the shared org directly. When the recorder holds 160K, Groq-side headroom is at most 40K minus
  every non-ledger caller's spend, i.e. unknown and possibly below one extraction (~2,169 tokens).
- review-iq's S32 S4 check (120b arm, 147 / 259) cannot resume while the flag is held, and cannot finish before its pre-registered
  deadline (2026-11-03 06:00 UTC) at the recording's pace; it will be analysed INCOMPLETE on n = 147 as registered.

## Proposal (per model, per trailing 24 h)

| Bucket | Today | Proposed |
|---|---|---|
| Production reserve (never allocated) | 40,000 | 40,000 |
| Unallocated slack for non-ledger callers | 0 | 40,000 |
| `triageiq-recorder` | uncapped (up to 160,000) | 60,000 |
| `benchmark` (review-iq evals) | 100,000 (20,000 during the recording) | 60,000 |
| Any other ledger project | uncapped | 20,000 default |

Mechanics, all in `groq_budget.py`:

1. Allocatable becomes `daily_cap - production_reserve - unallocated_slack` (new key, default 0 so old configs behave exactly as today).
2. `projects` gets `triageiq-recorder: 60_000` and `benchmark: 60_000`; a new `default_project_cap` key (default `None` = today's
   behaviour) caps unnamed projects.
3. **The recording flag stops being exclusive beyond the recorder's own cap**: while a recording is active, other projects still get
   `WAIT` unless their request fits in what the recorder cannot use (allocatable minus the recorder's cap). With the numbers above,
   `benchmark` keeps 60K even during a recording, and a recording can never starve another project or the reserve.
4. Tests: the existing suite plus (a) recorder capped at 60K under a full pool; (b) `benchmark` allowed during an active recording when
   the recorder is at its cap; (c) old `config.json` with none of the new keys gives identical decisions to today (backward compat).

## Cost of the proposal

- The ADR-0075 recording at 60K a day takes about 15 days instead of about 6. If that is too slow, recorder 100K / benchmark 20K /
  slack 40K keeps the guarantee (no single project can take the reserve or the slack) at the recording's preferred pace.
- None of this measures or protects Groq-side headroom against non-ledger callers; only moving production to its own paid
  organisation does (`ops/runbooks/groq-paid-tier.md` section 6).

## GG decisions needed

1. Approve the caps (or the 100K/20K/40K variant) and the non-exclusive recording flag; then a triage-iq session implements it with the
   tests above.
2. Separately, for S33: let the S4 check finish INCOMPLETE at n = 147 on 11-03 (the registered fallback, the default), or pause the
   triage-iq recording for about 46K tokens of 120b so the remaining 112 items run (also needs the `benchmark` cap restored above 46K).
