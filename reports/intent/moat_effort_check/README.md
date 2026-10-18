# Is `reasoning_effort` honoured by Ollama 0.40.6? (spec Amendment 13, S26)

Registered in `docs/specs/review-intent.md` Amendment 13 before any row existed. Kernel `gauravgandhi2411/review-intent-moat-effort` (private), code commit `11d2c2d2b6f2879dc0d3e7b5a3ea5b0336c03374`, Tesla T4, `ollama version is 0.40.6` from the pinned GitHub release (archive sha256 matched the pin: `c164ab8b1b28e2a247c29719b8e404c423c5b83ae64c3ab4e59d50c7ff7fc5e0`). Zero-shot prompt, first 20 sealed items, temperature 0, seed 42, `max_tokens` 2900, same 20 items at all four settings. Counts only: no review text. Reproduce: `python -m engine.intent.llm_effort_check --status run_status.json --out effort_check.json`.

Completion tokens per call:

<!-- METRICS:START:effort_per_setting -->
| setting | calls | mean | median | max | empty | ended at cap |
|---|---|---|---|---|---|---|
| none | 20 | 557.8 | 556.0 | 785 | 0 | 0 |
| low | 20 | 379.1 | 377.5 | 444 | 0 | 0 |
| medium | 20 | 548.8 | 540.5 | 832 | 0 | 0 |
| high | 20 | 821.9 | 739.5 | 1842 | 0 | 0 |
<!-- METRICS:END -->

Pre-registered test, high versus low over the same 20 items:

<!-- METRICS:START:effort_high_vs_low -->
high longer on 20 of 20 items, low longer on 0, ties 0; exact two-sided sign test p 2e-06; median 377.5 (low) against 739.5 (high), ratio 1.959. Criterion: p below 0.05 AND ratio at least 1.25. HONOURED: True
<!-- METRICS:END -->

Reading (VERIFIED for the numbers above, on 20 items of one prompt): the field changes how much the model reasons, monotonically (low below medium below high), and the server default is indistinguishable from medium (mean 557.8 against 548.8). So "reasoning effort low" in Amendment 12 was a real setting, not a requested one. What this does not show: that answer quality is unchanged across settings (not measured here), or the same effect on the few-shot prompt (not run).
