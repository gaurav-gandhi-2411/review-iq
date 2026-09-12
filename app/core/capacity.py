"""Real production capacity ceiling -- Session 13 P3b.

The async job endpoints (POST /v2/extract/batch, POST /v2/ingest/csv) used to return an
unbounded "processing" status with no indication of how long that might take. Groq's free
tier for this project's two models (openai/gpt-oss-20b, openai/gpt-oss-120b) each carry an
INDEPENDENT 8,000 tokens/minute (TPM) cap -- at this project's real measured token cost per
extraction (eval/results/token_cost_measurement_n106.json, n=106, PR #169), the sustainable
combined throughput across both models is far below what a naive "1,000 requests/day per
model" reading would suggest, because TPM binds long before RPM/RPD/TPD do at these token
sizes. See docs/architecture/adr/0015-panel-restoration-and-quota-safety-gap.md's Session 13
correction section for the full methodology and eval/capacity_model.py for the reproducible,
zero-quota computation this constant is derived from.

This constant is NOT read live from eval/results/capacity_model.json at runtime: eval/ is not
copied into the production Docker image (see Dockerfile -- only app/ ships). If
eval/capacity_model.py's output changes (e.g. the tier-routing mix shifts, or Groq's published
limits change), re-run it and update REAL_EXTRACTIONS_PER_MINUTE_CEILING below to match --
there is a regression test (tests/unit/test_capacity.py) that fails loudly if this constant and
a fresh run of eval/capacity_model.py ever diverge by more than 1%, so drift here is caught in
CI, not silently shipped.
"""

from __future__ import annotations

# eval/results/capacity_model.json's real_extractions_per_minute_ceiling, computed 2026-09-12
# at the observed 40.6% small / 59.4% large tier-routing mix -- binding constraint is the
# large tier's (openai/gpt-oss-120b) TPM pool. This is the WHOLE BUSINESS's combined ceiling
# (shared across every customer, the demo endpoint, and any live eval call against these two
# models) -- not a per-customer or per-job number.
REAL_EXTRACTIONS_PER_MINUTE_CEILING: float = 5.622857445976143


def estimate_seconds_remaining(rows_remaining: int) -> float:
    """Best-case ETA in seconds for `rows_remaining` more extractions to complete.

    "Best case" because this assumes the job has EXCLUSIVE access to the full measured
    throughput ceiling -- real elapsed time will be longer whenever real customer traffic,
    demo traffic, or another job's rows are competing for the same shared Groq quota at the
    same time. Callers must present this as an estimate, not a guarantee (see
    app/api/v2/ingest.py's `estimated_seconds_remaining` field naming and docstring).
    """
    if rows_remaining <= 0:
        return 0.0
    return (rows_remaining / REAL_EXTRACTIONS_PER_MINUTE_CEILING) * 60.0
