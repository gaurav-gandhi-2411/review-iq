# Amendment 18 results: R1+ on complaints inside 4-5 star reviews (S4), fresh18

**Verdict: P1 PASS.** On fresh18 S4 gold-yes, R1+ recall minus gpt-oss-20b recall is **+0.083**, with a paired bootstrap 95 percent CI of
**[+0.040, +0.126]**. The lower bound is above the -0.05 margin. Scored once, 2026-11-01 07:32 UTC. An independent verifier recomputed
every number with its own code: **31 of 31 match**, and its own R1/R1+ implementation agrees with the scoring code item for item.

Rule: `docs/specs/review-intent.md` Amendment 18 (registered in #638 `86675ad` before the draw). Result file:
`reports/intent/fresh18/result.json`. Labels: `reports/intent/labels_fresh18/` (committed `69fe727`, before any scoring).
Labels are SILVER (four-judge LLM panel consensus).

## Numbers

| Quantity | Value |
|---|---|
| fresh18 S4 items | 8,100 (2,700 per category, seed 51) |
| needs_action gold-yes / gold-no / no consensus | **301** / 7,673 / 126 |
| Size rule (>= 300 gold-yes) | met, by one item; no top-up drawn |
| **R1+ recall** | 284 / 301 = **0.944**, Wilson [0.911, 0.964] |
| **gpt-oss-20b recall** | 259 / 301 = **0.861**, Wilson [0.817, 0.895] |
| **Paired delta R1+ minus 20b** | **+0.0831, CI [0.0399, 0.1262]** (10,000 resamples, seed 42) |
| McNemar exact | router only 35, LLM only 10, p = 0.000247 |
| Missed by both | 7 |
| R1 alone | 276 / 301 = 0.917, Wilson [0.880, 0.943] |
| R1+ recall lower bound against A17's 0.85 bar (continuity only) | 0.911 >= 0.85 |
| R1+ routed share, all S4 items | 1,206 / 8,100 = 0.149 |
| R1+ routed share, gold-no | 818 / 7,673 = 0.107 |
| 20b yes-rate, gold-no | 166 / 7,673 = 0.022 |
| 20b empty or missing rows | 0 and 0 (so the sensitivity analysis is identical) |
| **120b secondary (200-item subsample)** | **NOT RUN.** The shared Groq pool is held by triage-iq's recorder. Deadline 2026-11-15 |

By category, gold-yes counts are apparel 170, beauty 49 and food 82.

## What this does and does not show

- It shows that R1+ is **no worse than the LLM path the product can run on its own hardware** (gpt-oss-20b on a T4). Here R1+ is clearly
  better: 25 more complaints caught out of 301.
- It does **not** show that R1+ matches the strongest LLM. The registered 120b comparison is secondary and has not run yet. In S32's
  exploratory data, R1+ and 120b tied on S4 (138/147 each, CI [-0.048, +0.048]).
- **A17's F2 failure stands and is not changed.** That was S4 recall against an absolute 0.85 bar on fresh17, lower bound 0.831. On fresh18,
  R1+ S4 recall has a lower bound of 0.911. That is reported here for continuity only.
- Expectation, written before the draw: "P1 is expected to pass with room to spare; 20b is the weaker comparator." That is what happened.

## Panel and process

The panel ran in three Kaggle sessions (stages P, P, P2) on Ollama 0.40.6 (sha256 `c164ab8b...`). The judge digests and the prompt hash
(`ri-judge-v3`, `2b171bb2b8d0`) are identical to fresh17's. Status files: `panel_status_session{1,2,3}.json`, plus the failed first push
`panel_status_session1_v1_failed.json`.

needs_action agreement: Krippendorff alpha 0.680 (USABLE-WITH-CAVEAT; fresh17 had 0.740), Gwet AC1 0.967, clear consensus 0.984. The
lower alpha is expected on a stratum with 3.7 percent positives, because alpha penalises chance-corrected agreement on rare classes.
The other tasks (broad intent, sentiment, urgency) are not used by A18.

## Deviations and incidents (none touch the labels or the decision)

1. **Panel v1 failed after 395 s** with `KeyError: 'stratum'`. The panel dataset had been built without the `stratum` field. It was rebuilt
   as fresh17's had been (`stratum = category`, texts unchanged). No judge record was written.
2. **In session 1, GPU1's model pull got a registry 503.** qwen3 and mistral idled for the whole 8 h session. Fixed with a pull retry
   with backoff (#645).
3. **Session 3 used stage P2** (#647). P2 places GPU1's two unfinished judges one per GPU. It changes placement only; the models,
   prompts and output files are unchanged.
4. **The resume checks happened at session end, not "within minutes" as registered.** Kaggle does not expose kernel output while a
   kernel runs. The #609 guard fails closed if no prior file is found, and each session's `prior_files` shows the expected counts:
   session 2 had llama31/gemma2 at 5,000 each; session 3 had llama31/gemma2 at 8,100 and qwen3/mistral at 4,400/4,200.
5. **About 9 h of idle time between sessions 1 and 2.** The S33 process ended mid-wait, and S34 picked the work up.

Kaggle GPU time (BELIEVED, from kernel elapsed times):

| Run | Hours |
|---|---|
| 20b | 3.5 |
| Panel v1 | 0.1 |
| Panel session 1 | 8.2 |
| Panel session 2 | 8.1 |
| Panel session 3 | 5.0 |
| **Total** | **about 25** |

Groq: 0 tokens on either org for this amendment so far.

## Next

`docs/plans/router-enable-plan.md` (parked for GG). Enabling stays GG's decision. The served code implements R1 only today, so the plan's
step 2 adds R1+ with a parity test before anything can be switched on.
