"""Re-run predictions ONLY for candidates whose prompt routing changed under the fixed
language router (2026-07-08 detector fix), carrying over all other existing successful
predictions verbatim, to isolate the routing variable for the before/after silver re-score.

This is deliberately NOT a full re-run of all 210 candidates: the vast majority of
candidates route identically under the old (pre-fix) and new (post-fix) hint logic, so
re-running them would burn Groq quota and LLM nondeterminism noise for zero signal. Only
candidates whose hint actually flips get a fresh extraction; everything else is carried
over from predictions.jsonl unchanged, with a "routing" field recording old vs. new hint
and whether a rerun happened.

Cassette path and output path are both dedicated to this script — never mixed with the
existing v0.1 internal benchmark cassette or the vernacular_v2 predictions.jsonl.

Usage:
    uv run python benchmark/vernacular_v2/rerun_fixed_router.py
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Must happen before ANY import that could transitively trigger app.core.config.get_settings()
# (which is @lru_cache'd — whatever GROQ_API_KEY is in the env on its first call sticks for
# the rest of this process). This env var is scoped to THIS local script process only; it has
# no effect on prod's separately-configured Cloud Run container.
from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key  # noqa: E402

_benchmark_key = load_benchmark_groq_key()
os.environ["GROQ_API_KEY"] = _benchmark_key
print(f"Using dedicated benchmark Groq key ({_benchmark_key[:8]}...{_benchmark_key[-4:]}) — isolated from prod.")

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

CANDIDATES_PATH = ROOT / "benchmark" / "vernacular_v2" / "candidates.jsonl"
EXISTING_PREDICTIONS_PATH = ROOT / "benchmark" / "vernacular_v2" / "predictions.jsonl"
PREDICTIONS_PATH = ROOT / "benchmark" / "vernacular_v2" / "predictions_fixed_router.jsonl"

# Courtesy pacing on the benchmark key's own budget, matching run_predictions.py.
DELAY_SECONDS = 5

_PRE_FIX_HINGLISH = re.compile(
    r"\b(nahi|nhi|accha|bahut|paisa|vasool|bakwaas|ekdum|mast|yaar|bhai|"
    r"gajab|bilkul|zabardast|bekar|khrab|boleto|jada|bhi|toh)\b",
    re.IGNORECASE,
)


def _pre_fix_lang_hint(text: str) -> str:
    """Frozen copy of the pre-fix (drifted) benchmark shim's routing heuristic, kept
    verbatim here ONLY for computing the "old" side of the before/after routing
    comparison. Do NOT "fix" this — it must stay exactly as it behaved before the
    2026-07-08 detector fix, or the before/after comparison is meaningless.
    """
    chars = [c for c in text if not unicodedata.category(c).startswith("Z")]
    if not chars:
        return "en"
    deva = sum(1 for c in chars if re.match(r"[ऀ-ॿ]", c))
    frac = deva / len(chars)
    if frac > 0.10:
        return "hi"
    if frac > 0.0:
        return "hi-en"
    if _PRE_FIX_HINGLISH.search(text):
        return "hi-en"
    return "en"


async def main() -> None:
    import app.core.providers.cassette as cassette_module

    from app.core.language import detect_language
    from benchmark.systems.review_iq import predict

    # Redirect the cassette path AFTER importing the predict() wrapper: importing
    # benchmark.systems.review_iq re-assigns CASSETTES_PATH to the v0.1 internal
    # benchmark's cassette at module level, so a redirect done before that import is
    # silently clobbered (real bug found 2026-07-09 — the 07-07/07-08 vernacular runs
    # leaked recordings into benchmark/cassettes/review_iq_cassettes.json this way).
    cassette_module.CASSETTES_PATH = ROOT / "benchmark" / "vernacular_v2" / "cassettes_fixed_router.json"

    def _new_lang_hint(text: str) -> str:
        lang = detect_language(text)
        return "en" if lang == "other" else lang

    candidates = [
        json.loads(line) for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    # Only a genuinely SUCCESSFUL prior prediction (no _error) is eligible to be carried
    # over or used as the "old" baseline for routing comparison.
    existing_preds: dict[str, dict] = {}
    if EXISTING_PREDICTIONS_PATH.exists():
        for line in EXISTING_PREDICTIONS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not rec.get("pred", {}).get("_error"):
                    existing_preds[rec["id"]] = rec

    # Resumable: anything already present with a successful pred in this script's own
    # output file counts as done and is kept verbatim.
    done: dict[str, dict] = {}
    if PREDICTIONS_PATH.exists():
        for line in PREDICTIONS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if not rec.get("pred", {}).get("_error"):
                    done[rec["id"]] = rec

    # Compute routing for every candidate that has an existing successful prediction to
    # carry forward or re-run against.
    carry_over_ids: list[str] = []
    rerun_ids: list[str] = []
    for cand in candidates:
        cid = cand["id"]
        if cid not in existing_preds or cid in done:
            continue
        old_hint = _pre_fix_lang_hint(cand["text"])
        new_hint = _new_lang_hint(cand["text"])
        if old_hint == new_hint:
            carry_over_ids.append(cid)
        else:
            rerun_ids.append(cid)

    n_rerun = len(rerun_ids)
    n_carry = len(carry_over_ids)
    print(f"Already done (resumed): {len(done)}")
    print(f"Carry-over (routing unchanged): {n_carry}")
    print(f"Rerun (routing changed): {n_rerun}")
    if n_rerun != 38:
        print(f"WARNING: expected exactly 38 reruns, computed {n_rerun}. Proceeding anyway.")

    results = list(done.values())
    with PREDICTIONS_PATH.open("w", encoding="utf-8") as fh:
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()

        n_errored = 0
        cand_by_id = {c["id"]: c for c in candidates}

        for cid in carry_over_ids:
            cand = cand_by_id[cid]
            old_hint = _pre_fix_lang_hint(cand["text"])
            new_hint = _new_lang_hint(cand["text"])
            rec = dict(existing_preds[cid])
            rec["routing"] = {"old": old_hint, "new": new_hint, "rerun": False}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)

        for i, cid in enumerate(rerun_ids, 1):
            cand = cand_by_id[cid]
            old_hint = _pre_fix_lang_hint(cand["text"])
            new_hint = _new_lang_hint(cand["text"])
            t0 = time.monotonic()
            pred = await predict(cand["text"], replay_mode=False)
            latency_ms = int((time.monotonic() - t0) * 1000)
            rec = {
                "id": cid,
                "slice": cand["slice"],
                "language_detected": cand["language_detected"],
                "pred": pred,
                "latency_ms": latency_ms,
                "routing": {"old": old_hint, "new": new_hint, "rerun": True},
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(rec)
            if pred.get("_error"):
                n_errored += 1
            err = pred.get("_error", "")
            status = f"SENT={pred.get('SENT')} URG={pred.get('URG')} LANG={pred.get('LANG')}"
            if err:
                status = f"ERROR: {err[:80]}"
            print(f"  [{i}/{n_rerun}] {cid} ({old_hint}->{new_hint}): {status}  {latency_ms}ms")
            await asyncio.sleep(DELAY_SECONDS)

    print("\nDone.")
    print(f"Carried over: {n_carry}  Re-run: {n_rerun}  Errored: {n_errored}")
    print(f"Written: {PREDICTIONS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
