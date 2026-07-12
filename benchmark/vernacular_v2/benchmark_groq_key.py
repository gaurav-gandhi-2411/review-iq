"""Dedicated Groq API key loader for benchmark/eval work — ISOLATED from prod's key.

Why this exists: on 2026-07-07, an unpaced benchmark run against review-iq's real
production Groq key exhausted its rate-limit budget and caused live `/v2/extract`
calls to fail (503 "upstream LLM unavailable") for real traffic. A second, paced
retry against the SAME key degraded prod again — pacing the outer call loop doesn't
bound the burst, because a single extraction can internally fire 2-4 real Groq
requests (retry + escalation) almost instantly. The only fix that actually removes
prod from the blast radius is a separate quota bucket: a dedicated API key used
ONLY for benchmark/eval load, never prod's key.

Setup (one-time, GG):
    1. Create a new key at https://console.groq.com/keys (name it e.g. "review-iq-benchmark").
    2. Create benchmark/vernacular_v2/.env.benchmark.local (gitignored — matches the
       existing .env.*.local pattern, never committed) with one line:
           GROQ_API_KEY_BENCHMARK=gsk_...
    3. Every benchmark script imports `load_benchmark_groq_key()` from this module and
       uses ITS return value — never `get_settings().groq_api_key` (that's prod's key).

Safety: refuses to run if the benchmark key is missing, OR if it's identical to
whatever's in the main .env's GROQ_API_KEY (catches "GG pasted the same key by
mistake" before any request goes out).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ENV_PATH = Path(__file__).resolve().parent / ".env.benchmark.local"
MAIN_ENV_PATH = ROOT / ".env"


def _read_env_var(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(rf"^{re.escape(key)}\s*=\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def load_benchmark_groq_key() -> str:
    """Return the dedicated benchmark Groq API key, or exit with a clear error.

    Never touches app.core.config.get_settings() (prod's cached Settings singleton) —
    reads the raw .env files directly, so there is no code path by which a benchmark
    run can end up using prod's key even by accident.
    """
    benchmark_key = _read_env_var(BENCHMARK_ENV_PATH, "GROQ_API_KEY_BENCHMARK")
    if not benchmark_key:
        print(
            f"ERROR: no dedicated benchmark Groq key found.\n"
            f"Create {BENCHMARK_ENV_PATH.relative_to(ROOT)} with:\n"
            f"  GROQ_API_KEY_BENCHMARK=gsk_...\n"
            f"(get a key from https://console.groq.com/keys — do NOT reuse prod's key)",
            file=sys.stderr,
        )
        sys.exit(1)

    prod_key = _read_env_var(MAIN_ENV_PATH, "GROQ_API_KEY")
    if prod_key and benchmark_key == prod_key:
        print(
            "ERROR: GROQ_API_KEY_BENCHMARK is identical to the main .env's GROQ_API_KEY.\n"
            "This is the exact sharing that degraded production twice on 2026-07-07.\n"
            "Create a genuinely separate key at https://console.groq.com/keys.",
            file=sys.stderr,
        )
        sys.exit(1)

    return benchmark_key


if __name__ == "__main__":
    key = load_benchmark_groq_key()
    print(
        f"OK — dedicated benchmark key loaded ({key[:8]}...{key[-4:]}), verified distinct from prod's."
    )
