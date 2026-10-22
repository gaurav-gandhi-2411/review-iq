"""Reply prompt v2.2 vs v2.1 (S31 B1, PR #298): drafting, judge-input assembly and analysis.

Pre-registration: docs/research/reply-v22-eval-preregistration.md (committed before any call).

    # once per version, from a checkout of that version (main for v21, #298 for v22); the key is read from the
    # environment and never printed; the ledger and private outputs live outside the repository:
    GROQ_EVAL_KEY=... PYTHONPATH=<checkout> python eval/experiments/reply_v22_eval.py draft --version v21 \
        --out D:/ml-cache/s31/reply --ledger D:/ml-cache/s31/reply/ledger.json
    python eval/experiments/reply_v22_eval.py judge-items --out D:/ml-cache/s31/reply     # -> judge_items.jsonl
    python eval/experiments/reply_v22_eval.py analyze --out D:/ml-cache/s31/reply --verdicts <judge jsonl dir> \
        --result eval/results/reply_v22_eval.json

Committed result: ids, arm, model, tokens, caveat kinds, deterministic counts, judge verdicts. No review or draft text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
N4C_RESULT = ROOT / "eval" / "results" / "reply_drafting_n20.json"
MODELS = {"L": "openai/gpt-oss-120b", "S": "openai/gpt-oss-20b"}
CEILING = 100_000  # tokens per model per trailing 24 h on the failover org (S31 spec rule 12)
STOP_AT = 90_000  # pre-registered: stop before a call that would cross this
EST_PER_DRAFT = 1_500
JUDGES = ("qwen3_8b", "gemma2_9b", "llama31_8b")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(
    r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|in|co|net|org|shop|store)(?:/\S*)?\b", re.I
)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s-]{8,}\d)(?!\w)")


def sample_ids() -> list[str]:
    """N4c 1.1: random.Random(42).shuffle over the sorted non-exposed held-out ids, first 20."""
    ack = json.loads((ROOT / "eval" / "heldout_exposure_ack.json").read_text(encoding="utf-8"))
    exposed = set(ack["acknowledged"])
    ids = sorted(p.stem for p in FIXTURES.glob("hien-*.json") if p.stem not in exposed)
    random.Random(42).shuffle(ids)
    return ids[:20]


def recorded_ids() -> list[str]:
    drafts = json.loads(N4C_RESULT.read_text(encoding="utf-8"))["drafts"]
    return sorted({d["id"] for d in drafts})


def load_fixture(fid: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{fid}.json").read_text(encoding="utf-8"))


def tone_for(gt: dict[str, Any]) -> str:
    return {"negative": "apologetic", "positive": "appreciative"}.get(
        gt.get("sentiment") or "", "professional"
    )


def invented_contacts(text: str, sources: list[str]) -> list[str]:
    """Emails, URLs and phone numbers in `text` that appear in none of `sources` (placeholders are bracketed, so never match)."""
    src = " ".join(sources).lower()
    found = [m.group(0) for rx in (EMAIL, URL, PHONE) for m in rx.finditer(text)]
    return sorted({f for f in found if f.lower().strip(".,)") not in src})


async def draft(version: str, out: Path, ledger_path: Path) -> None:
    from app.core.config import get_settings
    from app.core.reply import engine
    from app.core.reply.schema import ReplyRequest, ReplyTone
    from app.core.schemas import ReviewExtraction

    from eval.experiments.no_routing_stage1 import DayLedger

    ids = sample_ids()
    if sorted(ids) != recorded_ids():
        raise SystemExit("sample differs from the N4c ids: refusing to run")
    key = os.environ.get("GROQ_EVAL_KEY", "")
    if not key:
        raise SystemExit("GROQ_EVAL_KEY is not set (the FAILOVER org key)")
    raw_seen: list[str] = []
    real_call = engine._call_groq

    async def capture(*a: Any, **k: Any) -> tuple[str, int, int]:
        res = await real_call(*a, **k)
        raw_seen.append(res[0])
        return res

    engine._call_groq = capture  # record the model's raw text before the engine's post-processing
    ledger = DayLedger(ledger_path)
    rows_path = out / f"drafts_{version}.jsonl"
    done = set()
    if rows_path.exists():
        done = {
            (r["arm"], r["id"])
            for r in map(json.loads, rows_path.read_text(encoding="utf-8").splitlines())
        }
    for size, model in MODELS.items():
        arm = f"{version}-{size}"
        settings = get_settings().model_copy(
            update={"groq_api_key": key, "groq_model_large": model, "groq_model_small": model}
        )
        for fid in ids:
            if (arm, fid) in done:
                continue
            if ledger.used(model, time.time()) + EST_PER_DRAFT > STOP_AT:
                raise SystemExit(
                    f"ledger: {model} would cross {STOP_AT} tokens in 24 h; stopping (resume later)"
                )
            fx = load_fixture(fid)
            gt = fx["ground_truth"]
            ext = ReviewExtraction(product=gt.get("product") or "product", cons=gt.get("cons") or [],
                                   topics=gt.get("topics") or [], pros=gt.get("pros") or [])  # fmt: skip
            req = ReplyRequest(text=fx["review_text"], tone=ReplyTone(tone_for(gt)), extraction=ext)
            raw_seen.clear()
            row: dict[str, Any] = {"arm": arm, "id": fid, "model": model, "version": version}
            for attempt in range(4):
                try:
                    d, tin, tout = await engine.draft_reply(req, settings=settings)
                    sources = [fx["review_text"], *ext.cons, *ext.topics, *ext.pros]
                    row |= {"ok": True, "language": d.language, "tone": d.tone.value, "tokens_in": tin,
                            "tokens_out": tout, "caveats": d.caveats, "served": d.reply_text,
                            "raw": raw_seen[-1] if raw_seen else None,
                            "invented_contacts_served": invented_contacts(d.reply_text, sources),
                            "invented_contacts_raw": invented_contacts(raw_seen[-1], sources) if raw_seen else None}  # fmt: skip
                    ledger.add(model, tin + tout, time.time(), f"{arm}:{fid}")
                    break
                except Exception as e:  # noqa: BLE001 (recorded; a 429 waits, anything else is reported missing)
                    msg = str(e)[:300]
                    if "429" in msg and attempt < 3:
                        time.sleep(60)
                        continue
                    row |= {"ok": False, "error": msg}
                    break
            with rows_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(arm, fid, "ok" if row.get("ok") else "FAILED", flush=True)


RUBRIC = """You are auditing a customer-service reply drafted for a product review.

REVIEW (customer text):
{review}

ALLOWED FACTS (the only specifics the drafter was given): product: {product}; cons: {cons}; topics: {topics}; pros: {pros}
REQUESTED TONE: {tone}

DRAFT REPLY:
{draft}

Return ONE JSON object with exactly these keys:
- "invented_details": list of every specific in the draft that is NOT in the review text or the allowed facts. Counts as invented: any
  person/company/brand name; order, ticket or tracking numbers; a promise or offer of refund, replacement, discount, pickup, or compensation;
  any timeline or deadline ("within 24 hours", "by Monday"); a specific phone number, email, URL or contact channel; a policy claim; a product
  fact or cause not stated in the review. NOT counted: generic apology/thanks, a generic "please contact our support team" with no channel or
  timeline, a bracketed placeholder such as [your support contact], restating what the review says.
- "coherent": true if fluent, in the review's language/script mix, with no nonsensical composition (e.g. thanking the customer for a defect).
- "grounded": integer 1-5, how specifically the draft addresses the complaint or praise in the review.
- "tone_fit": integer 1-5, how well it matches the requested tone and suits a public brand reply.
Output only the JSON object."""


def judge_items(out: Path) -> None:
    rows = []
    for v in ("v21", "v22"):
        rows += [
            r
            for r in map(
                json.loads, (out / f"drafts_{v}.jsonl").read_text(encoding="utf-8").splitlines()
            )
            if r.get("ok")
        ]
    random.Random(42).shuffle(rows)  # blind and order-balanced: the judge never sees the arm
    with (out / "judge_items.jsonl").open("w", encoding="utf-8") as f:
        for k, r in enumerate(rows):
            gt = load_fixture(r["id"])["ground_truth"]
            prompt = RUBRIC.format(review=load_fixture(r["id"])["review_text"], product=gt.get("product"), cons=gt.get("cons"),
                                   topics=gt.get("topics"), pros=gt.get("pros"), tone=r["tone"], draft=r["served"])  # fmt: skip
            f.write(json.dumps({"item": f"j{k:03d}", "prompt": prompt}, ensure_ascii=False) + "\n")
    key = {f"j{k:03d}": {"arm": r["arm"], "id": r["id"]} for k, r in enumerate(rows)}
    (out / "judge_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"{len(rows)} judge items")


def wilson(k: int, n: int, z: float = 1.959964) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def analyze(out: Path, verdict_dir: Path, result: Path) -> None:
    key = json.loads((out / "judge_key.json").read_text(encoding="utf-8"))
    verdicts: dict[tuple[str, str], dict[str, Any]] = {}
    for j in JUDGES:
        for r in map(
            json.loads, (verdict_dir / f"{j}.jsonl").read_text(encoding="utf-8").splitlines()
        ):
            k = key[r["item"]]
            verdicts.setdefault((k["arm"], k["id"]), {})[j] = r.get("verdict")
    drafts = {}
    for v in ("v21", "v22"):
        for r in map(
            json.loads, (out / f"drafts_{v}.jsonl").read_text(encoding="utf-8").splitlines()
        ):
            drafts[(r["arm"], r["id"])] = r
    per_arm: dict[str, Any] = {}
    rows_out = []
    for arm in ("v21-L", "v21-S", "v22-L", "v22-S"):
        n = hard = anyf = incoh = usable = inv_served = inv_raw = missing = 0
        for (a, fid), d in sorted(drafts.items()):
            if a != arm:
                continue
            n += 1
            if not d.get("ok"):
                missing += 1
                rows_out.append({"arm": arm, "id": fid, "ok": False})
                continue
            vs = verdicts.get((arm, fid), {})
            got = [vs.get(j) for j in JUDGES]
            flags = [bool(g and g.get("invented_details")) for g in got]
            coh = [bool(g and g.get("coherent")) for g in got if g]
            gr = sorted(int(g.get("grounded", 0)) for g in got if g)
            tf = sorted(int(g.get("tone_fit", 0)) for g in got if g)
            is_hard = sum(flags) >= 2
            not_coh = sum(1 for g in got if g and g.get("coherent") is False) >= 2
            ok_use = (
                (not is_hard)
                and (not not_coh)
                and gr
                and tf
                and gr[len(gr) // 2] >= 3
                and tf[len(tf) // 2] >= 3
            )
            hard += is_hard
            anyf += any(flags)
            incoh += not_coh
            usable += bool(ok_use)
            inv_served += bool(d["invented_contacts_served"])
            inv_raw += bool(d.get("invented_contacts_raw"))
            rows_out.append({"arm": arm, "id": fid, "language": d["language"], "tokens_in": d["tokens_in"], "tokens_out": d["tokens_out"],
                             "caveat_kinds": sorted({c.split(":")[0].split("(")[0].strip() for c in d["caveats"]}),
                             "invented_contact_served": bool(d["invented_contacts_served"]),
                             "invented_contact_raw": bool(d.get("invented_contacts_raw")),
                             "judges": {j: (None if g is None else {"n_invented": len(g.get("invented_details") or []), "coherent": g.get("coherent"),
                                            "grounded": g.get("grounded"), "tone_fit": g.get("tone_fit")}) for j, g in zip(JUDGES, got, strict=True)},
                             "missing_verdicts": sum(g is None for g in got), "hard_failure": is_hard, "usable": bool(ok_use)})  # fmt: skip
        ok = n - missing
        per_arm[arm] = {"n": n, "missing_drafts": missing, "hard_failure": hard, "hard_failure_ci95": wilson(hard, ok),
                        "flagged_by_any_judge": anyf, "not_coherent": incoh, "usable": usable, "usable_ci95": wilson(usable, ok),
                        "invented_contact_served": inv_served, "invented_contact_served_ci95": wilson(inv_served, ok),
                        "invented_contact_raw": inv_raw}  # fmt: skip
    c1 = (
        per_arm["v22-L"]["invented_contact_served"] == 0
        and per_arm["v22-S"]["invented_contact_served"] == 0
    )
    c2 = (
        per_arm["v22-L"]["hard_failure"] <= per_arm["v21-L"]["hard_failure"]
        and per_arm["v22-S"]["hard_failure"] <= per_arm["v21-S"]["hard_failure"]
    )
    res = {"preregistration": "docs/research/reply-v22-eval-preregistration.md", "judges": list(JUDGES), "arms": per_arm,
           "success_rule": {"c1_zero_invented_contacts_v22": c1, "c2_hard_failures_not_worse": c2, "pass": c1 and c2},
           "drafts": rows_out, "no_review_or_draft_text_included": True}  # fmt: skip
    result.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"arms": per_arm, "success_rule": res["success_rule"]}, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft")
    d.add_argument("--version", choices=["v21", "v22"], required=True)
    d.add_argument("--ledger", type=Path, required=True)
    j = sub.add_parser("judge-items")
    a = sub.add_parser("analyze")
    a.add_argument("--verdicts", type=Path, required=True)
    a.add_argument("--result", type=Path, required=True)
    for p in (d, j, a):
        p.add_argument("--out", type=Path, required=True)
    x = ap.parse_args()
    x.out.mkdir(parents=True, exist_ok=True)
    if x.cmd == "draft":
        asyncio.run(draft(x.version, x.out, x.ledger))
    elif x.cmd == "judge-items":
        judge_items(x.out)
    else:
        analyze(x.out, x.verdicts, x.result)


if __name__ == "__main__":
    main()
