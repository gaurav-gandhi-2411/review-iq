"""S32 exploratory check: router vs LLM recall on complaints inside 4-5 star reviews (S4). Pre-registered in
`docs/specs/s32-s4-llm-check.md` (commit 3959ac6) before any number was computed. EXPLORATORY: cannot change the A16/A17 verdicts.

    # private Kaggle items for the 20b arm (Block B S4 items of fresh16 + fresh17; Block A rows already exist)
    PYTHONPATH=. python -m engine.intent.s4_llm_check items-b --out D:/ml-cache/s32/ds_llm_b
    # 120b on the Groq FAILOVER org, S4 positives only, resumable (run under the key wrapper; the value is never printed)
    PYTHONPATH=. python -m engine.intent.s4_llm_check groq120 --out D:/ml-cache/s32/groq120
    # analysis (ids and numbers only)
    PYTHONPATH=. python -m engine.intent.s4_llm_check analyze --llm20-dirs <shards dirs...> --groq120 D:/ml-cache/s32/groq120 \
        --out reports/intent/s4_llm_check/result.json

Review text is read from private files under D:/ml-cache and never written to any output.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import numpy as np
from app.core.classify import encoder_first as EF
from app.core.classify import runtime

from engine.intent import llm_moat as M
from engine.intent import llm_moat_local as L
from engine.intent.fresh15_eval import load_llm, rate
from engine.intent.fresh16_eval import decide_all
from engine.intent.fresh17_eval import THRESHOLDS, routes

ROOT = Path(__file__).resolve().parents[2]
SETS = ("fresh16", "fresh17")
MANIFEST = {s: ROOT / f"reports/labelling/{s}/manifest.json" for s in SETS}
LABELS = {s: ROOT / f"reports/intent/labels_{s}/{s}_labels.json" for s in SETS}
TAU_FILE = ROOT / "reports/intent/a17/tau.json"
PRIVATE = Path(os.environ.get("S31_CACHE", "D:/ml-cache/s31"))
TEXTS = {
    "fresh16": PRIVATE / "ds_run2_f16/items.jsonl",
    "fresh17": PRIVATE / "ds_fresh17/fresh17_all.jsonl",
}
RUN2_LOGITS = {s: PRIVATE / f"p7/{s}_run2_pinned.npz" for s in SETS}
RUN4_LOGITS = PRIVATE / "run4_out/out/final_H128/fresh16_run4.npz"
RUN4_THRESHOLDS = PRIVATE / "run4_out/out/final_H128/thresholds_run4.json"
MODEL_120B = "openai/gpt-oss-120b"
CAP_120B = 100_000  # tokens per trailing 24 h, this harness's own cap (S31 spec rule 12); the shared ledger also gates
N_BOOT, SEED = 10_000, 42


def s4_items(s: str) -> list[dict]:
    """S4 = block A or B with stars >= 4 (the A16 E2 / A17 F2 definition), manifest order."""
    items = json.loads(MANIFEST[s].read_text(encoding="utf-8"))["items"]
    return [
        r for r in items if r["block"] in ("A", "B") and r["stars"] is not None and r["stars"] >= 4
    ]


def gold_needs_action(s: str) -> dict[str, str]:
    return json.loads(LABELS[s].read_text(encoding="utf-8"))["labels"]["needs_action"]


def positives_order() -> list[str]:
    """Pooled S4 gold-yes ids in the registered order: Random(42) shuffle of (fresh16 ids, then fresh17 ids, manifest order)."""
    ids = []
    for s in SETS:
        gold = gold_needs_action(s)
        ids += [r["id"] for r in s4_items(s) if gold.get(r["id"]) == "yes"]
    random.Random(SEED).shuffle(ids)
    return ids


def read_texts() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in TEXTS.values():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                d = json.loads(ln)
                out[d["id"]] = d["text"]
    return out


def cmd_items_b(out: Path) -> None:
    texts = read_texts()
    rows = [
        {"id": r["id"], "text": texts[r["id"]]}
        for s in SETS
        for r in s4_items(s)
        if r["block"] == "B"
    ]
    out.mkdir(parents=True, exist_ok=True)
    with (out / "items.jsonl").open("w", encoding="utf-8") as w:
        for r in rows:
            w.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {
        "title": "review-intent-s4b-llm",
        "id": "gauravgandhi2411/review-intent-s4b-llm",
        "licenses": [{"name": "other"}],
        "isPrivate": True,
    }
    (out / "dataset-metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    print(
        json.dumps(
            {
                "n_items": len(rows),
                "by_set": {s: sum(r["id"].startswith(s) for r in rows) for s in SETS},
            }
        )
    )


def ask_groq(key: str, text: str) -> tuple[dict | None, int, str]:
    """One 120b call with the 20b arm's exact body (Amendment 15 settings). -> (answer or None, tokens, finish_reason)."""
    import httpx

    body = L.build_body(MODEL_120B, text)
    for attempt in range(6):
        try:
            r = httpx.post(
                M.URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120
            )
        except httpx.TransportError:
            time.sleep(5 * 2**attempt)
            continue
        if r.status_code == 429:
            time.sleep(min(300, 15 * 2**attempt))
            continue
        if (
            r.status_code == 400 and attempt >= 1
        ):  # e.g. json_validate_failed twice: an unparseable answer
            return None, 0, "json_validate_failed"
        if r.status_code == 400 or r.status_code >= 500:
            time.sleep(5)
            continue
        r.raise_for_status()
        j = r.json()
        ch = j["choices"][0]
        return (
            L.parse_content(ch["message"].get("content")),
            int(j.get("usage", {}).get("total_tokens", 0)),
            str(ch.get("finish_reason")),
        )
    raise M.InfraError("rate limit, 5xx or transport failure after 6 attempts")


def cmd_groq120(out: Path, limit: int | None) -> int:
    key = os.environ.get("GROQ_EVAL_KEY", "")
    if not key:
        raise SystemExit("GROQ_EVAL_KEY is not set (the FAILOVER org key, via the wrapper)")
    # The ledger hook blocks a call until the shared pool allows it (default up to 6 h); a short wait keeps every invocation a
    # bounded foreground command (no long-lived watcher): a blocked call raises BudgetWaitTimeout and the run resumes later.
    os.environ.setdefault("GROQ_BUDGET_WAIT_S", "60")
    from engine.groq_budget_hook import BudgetWaitTimeout, install

    install()  # machine-wide ledger; fails closed if the ledger module is missing
    L.GEN.update({"max_tokens": 1500, "reasoning_effort": "low"})
    out.mkdir(parents=True, exist_ok=True)
    f, upath = out / "gpt-oss-120b.jsonl", M.usage_path(out, MODEL_120B)
    done = (
        {json.loads(ln)["id"] for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()}
        if f.exists()
        else set()
    )
    usage = M.read_usage(upath)
    texts = read_texts()
    n_new = 0
    with f.open("a", encoding="utf-8") as w:
        for i in positives_order():
            if i in done:
                continue
            if limit is not None and n_new >= limit:
                break
            recent = [n for _, n in usage[-20:] if n > 0]
            need = max(800, int(sum(recent) / len(recent))) if recent else 1500
            wait = M.wait_seconds(usage, need, time.time(), cap=CAP_120B)
            if wait > 0:
                print(
                    json.dumps(
                        {
                            "stopped": "own 24h cap",
                            "wait_h": round(wait / 3600, 2),
                            "done": len(done),
                        }
                    )
                )
                return 4
            try:
                ans, used, finish = ask_groq(key, texts[i])
            except M.InfraError as e:
                print(json.dumps({"stopped": str(e), "done": len(done)}))
                return 3
            except BudgetWaitTimeout:
                print(json.dumps({"stopped": "shared ledger pool", "done": len(done)}))
                return 4
            usage.append((time.time(), used))
            with upath.open("a", encoding="utf-8") as uw:
                uw.write(json.dumps({"ts": usage[-1][0], "tokens": used}) + "\n")
            w.write(
                json.dumps({"id": i, "ans": ans, "tokens": used, "finish_reason": finish}) + "\n"
            )
            w.flush()
            done.add(i)
            n_new += 1
    print(json.dumps({"done": len(done), "new": n_new, "of": len(positives_order())}))
    return 0


def llm_says_yes(row: dict | None) -> bool | None:
    """True/False for an answered row ('yes' routes); None when the row is missing. An empty/unparseable answer is False."""
    if row is None:
        return None
    a = row.get("ans")
    na = a.get("needs_action") if isinstance(a, dict) else None
    return isinstance(na, dict) and na.get("label") == "yes"


def is_empty(row: dict) -> bool:
    a = row.get("ans")
    return not (
        isinstance(a, dict)
        and isinstance(a.get("needs_action"), dict)
        and a["needs_action"].get("label") in ("yes", "no")
    )


def paired_cluster_boot(
    r: np.ndarray, lm: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> dict:
    """Recall(router) - recall(LLM) on the same positives; percentile 95 CI, resampling items (clusters of size 1)."""
    n = len(r)
    if n == 0:
        return {"n": 0}
    rng = np.random.default_rng(seed)
    d = np.empty(n_boot)
    for b in range(n_boot):  # chunk-free loop: n is a few hundred
        idx = rng.integers(0, n, n)
        d[b] = r[idx].mean() - lm[idx].mean()
    lo, hi = np.percentile(d, [2.5, 97.5])
    return {"delta": round(float(r.mean() - lm.mean()), 4), "boot95": [round(float(lo), 4), round(float(hi), 4)], "n": n,
            "resamples": n_boot, "cluster": "item"}  # fmt: skip


def mcnemar_exact(r: np.ndarray, lm: np.ndarray) -> dict:
    """Two-sided exact McNemar: binomial(b + c, 0.5) on the discordant pairs."""
    from math import comb

    b = int((r & ~lm).sum())  # router yes, LLM no
    c = int((~r & lm).sum())  # router no, LLM yes
    n = b + c
    if n == 0:
        return {"router_only": b, "llm_only": c, "p": 1.0}
    tail = sum(comb(n, k) for k in range(0, min(b, c) + 1)) / 2**n
    return {"router_only": b, "llm_only": c, "p": round(min(1.0, 2 * tail), 6)}


def compare(name: str, router: np.ndarray, llm: np.ndarray) -> dict:
    return {"comparison": name, "router_recall": rate(int(router.sum()), len(router)), "llm_recall": rate(int(llm.sum()), len(llm)),
            "paired": paired_cluster_boot(router.astype(float), llm.astype(float)), "mcnemar": mcnemar_exact(router, llm),
            "missed_by_both": int((~router & ~llm).sum())}  # fmt: skip


def router_decisions() -> dict[str, dict[str, dict[str, bool]]]:
    """set -> id -> {r1_plus, r1, run4_r1 (fresh16 only)} for every item of the set, from the stored logits (frozen configs)."""
    th2 = runtime.load_thresholds(ROOT / THRESHOLDS)
    tau = float(json.loads(TAU_FILE.read_text(encoding="utf-8"))["tau"])
    out: dict[str, dict[str, dict[str, bool]]] = {}
    for s in SETS:
        z = np.load(RUN2_LOGITS[s])
        ids = [str(x) for x in z["ids"]]
        r1, rp = routes(th2, z["logits"], tau)
        out[s] = {i: {"r1_plus": bool(rp[k]), "r1": bool(r1[k])} for k, i in enumerate(ids)}
    z4 = np.load(RUN4_LOGITS)
    dec4 = decide_all(runtime.load_thresholds(RUN4_THRESHOLDS), z4["logits"])
    for k, i in enumerate(str(x) for x in z4["ids"]):
        out["fresh16"][i]["run4_r1"] = bool(EF.route_r1(dec4[k]).route)
    return out


def cmd_analyze(llm20_dirs: list[Path], groq_dir: Path, out: Path) -> None:
    dec = router_decisions()
    llm20: dict[str, dict] = {}
    for d in llm20_dirs:
        llm20.update(load_llm(d))
    f120 = groq_dir / "gpt-oss-120b.jsonl"
    llm120 = (
        {
            json.loads(ln)["id"]: json.loads(ln)
            for ln in f120.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        }
        if f120.exists()
        else {}
    )
    pos_by_set, neg_by_set, counts = {}, {}, {}
    for s in SETS:
        gold = gold_needs_action(s)
        its = s4_items(s)
        pos_by_set[s] = [r["id"] for r in its if gold.get(r["id"]) == "yes"]
        neg_by_set[s] = [r["id"] for r in its if gold.get(r["id"]) == "no"]
        counts[s] = {"s4_items": len(its), "gold_yes": len(pos_by_set[s]), "gold_no": len(neg_by_set[s]),
                     "no_consensus": len(its) - len(pos_by_set[s]) - len(neg_by_set[s])}  # fmt: skip
    all_s4 = [i for s in SETS for i in pos_by_set[s] + neg_by_set[s]]
    missing20 = [i for i in all_s4 if i not in llm20]
    empty20 = [i for i in all_s4 if i in llm20 and is_empty(llm20[i])]

    def arr(ids: list[str], s_of: dict[str, str], key: str) -> np.ndarray:
        return np.array([dec[s_of[i]][i][key] for i in ids])

    s_of = {i: s for s in SETS for i in pos_by_set[s] + neg_by_set[s]}
    res: dict = {"spec": "docs/specs/s32-s4-llm-check.md (pre-registered at 3959ac6)", "status": "EXPLORATORY", "counts": counts,
                 "llm20": {"rows": len(llm20), "missing_s4_rows": len(missing20), "empty_s4_rows": len(empty20)},
                 "llm120": {"rows": len(llm120), "of_positives": sum(counts[s]["gold_yes"] for s in SETS),
                            "empty_rows": sum(is_empty(r) for r in llm120.values()),
                            "tokens": int(sum(r.get("tokens", 0) for r in llm120.values()))},
                 "consistency_with_registered": {}, "by_set": {}, "pooled": {}}  # fmt: skip
    # registered numbers recomputed from the same logits (A16 E2 run 4, A16 run 2 R1, A17 F2 R1+); a mismatch stops the analysis
    reg = {
        "fresh16_run4_r1": (114, 126),
        "fresh16_run2_r1": (116, 126),
        "fresh17_r1_plus": (119, 133),
    }
    got = {"fresh16_run4_r1": (int(arr(pos_by_set["fresh16"], s_of, "run4_r1").sum()), len(pos_by_set["fresh16"])),
           "fresh16_run2_r1": (int(arr(pos_by_set["fresh16"], s_of, "r1").sum()), len(pos_by_set["fresh16"])),
           "fresh17_r1_plus": (int(arr(pos_by_set["fresh17"], s_of, "r1_plus").sum()), len(pos_by_set["fresh17"]))}  # fmt: skip
    res["consistency_with_registered"] = {
        k: {"registered": list(reg[k]), "recomputed": list(got[k]), "equal": reg[k] == got[k]}
        for k in reg
    }
    if not all(v["equal"] for v in res["consistency_with_registered"].values()):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
        raise SystemExit(
            "router decisions do not reproduce the registered numbers; analysis stopped"
        )

    def block(pos: list[str], neg: list[str], with_run4: bool) -> dict:
        rp, r1 = arr(pos, s_of, "r1_plus"), arr(pos, s_of, "r1")
        y20 = np.array([bool(llm_says_yes(llm20.get(i))) for i in pos])
        b: dict = {
            "recall": {
                "r1_plus": rate(int(rp.sum()), len(pos)),
                "r1": rate(int(r1.sum()), len(pos)),
                "llm20b": rate(int(y20.sum()), len(pos)),
            },  # fmt: skip
            "r1_plus_vs_20b": compare("R1+ minus gpt-oss-20b", rp, y20),
        }
        ne = [i for i in pos if i in llm20 and not is_empty(llm20[i])]
        if len(ne) != len(pos):
            b["r1_plus_vs_20b_excluding_empty"] = compare("R1+ minus 20b, empties excluded", arr(ne, s_of, "r1_plus"),
                                                          np.array([bool(llm_says_yes(llm20[i])) for i in ne]))  # fmt: skip
        got120 = [i for i in pos if i in llm120]
        if got120:
            y120 = np.array([bool(llm_says_yes(llm120[i])) for i in got120])
            b["recall"]["llm120b"] = rate(int(y120.sum()), len(got120))
            b["r1_plus_vs_120b"] = compare(
                "R1+ minus gpt-oss-120b", arr(got120, s_of, "r1_plus"), y120
            )
            b["r1_plus_vs_120b"]["complete"] = len(got120) == len(pos)
        if with_run4:
            r4 = arr(pos, s_of, "run4_r1")
            b["recall"]["run4_r1"] = rate(int(r4.sum()), len(pos))
            b["run4_vs_20b"] = compare("run 4 R1 minus gpt-oss-20b", r4, y20)
            if got120:
                b["run4_vs_120b"] = compare(
                    "run 4 R1 minus gpt-oss-120b", arr(got120, s_of, "run4_r1"), y120
                )
        n20 = np.array([bool(llm_says_yes(llm20.get(i))) for i in neg])
        b["routing_cost_on_gold_no"] = {"llm20b_yes_rate": rate(int(n20.sum()), len(neg)),
                                        "r1_plus_routed_share": rate(int(arr(neg, s_of, "r1_plus").sum()), len(neg))}  # fmt: skip
        return b

    for s in SETS:
        res["by_set"][s] = block(pos_by_set[s], neg_by_set[s], s == "fresh16")
    res["pooled"] = block(
        pos_by_set["fresh16"] + pos_by_set["fresh17"],
        neg_by_set["fresh16"] + neg_by_set["fresh17"],
        False,
    )
    res["decision"] = decide(res["pooled"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": res["decision"],
                "consistency": {
                    k: v["equal"] for k, v in res["consistency_with_registered"].items()
                },
            }
        )
    )


def decide(pooled: dict) -> dict:
    """Section 7: the pooled R1+ minus 120b paired CI decides; 20b is used only if 120b produced no rows (a reported deviation)."""
    key, deviation = (
        ("r1_plus_vs_120b", None)
        if "r1_plus_vs_120b" in pooled
        else ("r1_plus_vs_20b", "no 120b rows: 20b used")
    )
    lo, hi = pooled[key]["paired"]["boot95"]
    branch = "D1" if lo > -0.05 else "D2" if hi < -0.05 else "D3"
    return {"branch": branch, "comparison": key, "boot95": [lo, hi], "margin": -0.05, "deviation": deviation,
            "complete_120b": pooled.get(key, {}).get("complete", key != "r1_plus_vs_120b")}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("items-b")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("groq120")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int)
    p = sub.add_parser("analyze")
    p.add_argument("--llm20-dirs", type=Path, nargs="+", required=True)
    p.add_argument("--groq120", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "items-b":
        cmd_items_b(a.out)
        return 0
    if a.cmd == "groq120":
        return cmd_groq120(a.out, a.limit)
    cmd_analyze(a.llm20_dirs, a.groq120, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
