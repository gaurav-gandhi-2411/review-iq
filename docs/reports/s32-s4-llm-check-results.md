# S32 S4 LLM check: results (EXPLORATORY, INCOMPLETE n = 147 for 120b)

Spec: `docs/specs/s32-s4-llm-check.md` (pre-registered `3959ac6`; timing deviation logged in section 8 at `f5850f1` before the run).
Code: `engine/intent/s4_llm_check.py` `analyze`, run once 2026-10-30 17:40 UTC. Result: `reports/intent/s4_llm_check/result.json`
(ids and numbers only). Labels are SILVER (four-judge panel consensus). An independent verifier recomputed every number with its own
code (85 checks, 0 mismatches).

## Decision: D1 (router non-inferior), on a thin margin

Pooled R1+ minus gpt-oss-120b on S4 gold-yes, **n = 147 of 259** (the 120b arm is INCOMPLETE; its 147 rows are the first 147 of the
registered Random(42) order, verified): delta **0.000**, paired bootstrap 95 percent CI **[-0.0476, +0.0476]** (10,000 resamples,
seed 42), McNemar 7 router-only vs 7 LLM-only, p = 1.0. The lower bound is above -0.05, so section 7 gives **D1**.

**Fragility (verifier, stated plainly):** the lower bound is exactly -7/147 = -0.0476, 0.0024 above the margin; the bootstrap lives
on a 1/147 grid, and the next grid point (-8/147 = -0.0544) would be D3. The exact percentile-bootstrap tail at -8/147 is 2.24 percent,
just under 2.5 percent, so -0.0476 is also the infinite-resample answer, and 12 alternative seeds on the real data all give it; but
about 5 percent of seeds on a synthetic replica, a Wald interval ([-0.0501, 0.0501]) or one discordant pair the other way would give D3.
The branch is applied as committed; the confirmatory test (Amendment 18) is what settles the question.

## Numbers (pooled, S4 gold-yes unless stated; Wilson 95)

| Arm | Recall | n |
|---|---|---|
| R1+ (A17 router) | 240/259 = 0.927 [0.888, 0.953] | all positives |
| R1 | 231/259 = 0.892 [0.848, 0.924] | |
| gpt-oss-20b | 211/259 = 0.815 [0.763, 0.857] | |
| gpt-oss-120b | 138/147 = 0.939 [0.888, 0.968] | 147 with 120b rows |
| R1+ on the same 147 | 138/147 = 0.939 | |

- R1+ minus 20b (supporting): +0.112, CI [0.058, 0.166], McNemar 42 vs 13, p = 0.0001; missed by both 6.
- Per set, R1+ minus 120b: fresh16 -0.015 [-0.074, 0.044] (n = 68); fresh17 +0.013 [-0.063, 0.089] (n = 79).
- Run 4 (fresh16 only, report only): minus 20b +0.056 [-0.024, 0.135]; minus 120b -0.044 [-0.118, 0.029].
- Routing cost on S4 gold-no (6,581): 20b says yes on 1.9 percent; R1+ routes 10.7 percent.
- Data checks: 20b 7,700 rows, 0 missing, 0 empty; 120b 147 rows, 0 empty, 60,732 tokens; the three registered router numbers
  (114/126, 116/126, 119/133) reproduce exactly.

## What it means

On complaints inside 4-5 star reviews, the A17 router catches as many as gpt-oss-120b on the items both saw, and clearly more than
gpt-oss-20b. A17's F2 failure (absolute 0.85 bar) stands as recorded; the open question is now whether non-inferiority to the LLM holds
on fresh data, which Amendment 18 tests.
