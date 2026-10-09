"""S20 S4: blind multi-model silver labelling of NEW, disjoint English reviews (pilot).

Why: stage 2 of the "no routing" experiment (docs/specs/s15c-language-routing.md, S17/S20) needs
LABELLED HELD-OUT English reviews; the held-out corpus has only 5 by ground truth. Plan, thresholds,
cost and results: docs/research/s20-english-fixture-labelling-plan.md.

Panel: three OpenRouter models from three families, none of them OpenAI (gpt-oss) or Meta (Llama),
the production extraction models. Blind: the prompt is `eval/consensus/panel.py`'s judge prompt
(independent wording, no extractor output, no few-shots). Zero-data-retention: every request pins
`provider.zdr=true` and `data_collection=deny`.

Modes (all but `power` and `select` spend money; every spend is metered from `usage.cost`):
    power      Zero-cost sample-size table + seeded paired-bootstrap sanity check.
    select     Zero-cost: seeded, exclusion-checked sample of NEW English reviews.
    calibrate  Panel on the 5 English held-out fixtures + the 16-item control set.
    label      Panel on the selected reviews (stops early if the pre-registered gate fails).
    report     Zero-cost: consensus, alpha/kappa, silver fixtures, provenance JSON.

The API key never reaches argv or output: it is read from OPENROUTER_API_KEY or, if unset, fetched
from Secret Manager into this process only.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.agreement import fleiss_kappa, krippendorff_alpha  # noqa: E402
from eval.consensus import calibration, panel  # noqa: E402
from eval.consensus.build_report import fleiss_table, raw_votes_matrix  # noqa: E402
from eval.consensus.candidates import FLIPKART_CANDIDATES_PATH, load_jsonl  # noqa: E402
from eval.consensus.voting import consensus_for_item  # noqa: E402
from eval.heldout_exposure import (  # noqa: E402
    RESOLVED_AGREEMENT,
    benchmark_texts,
    dev_fixture_texts,
    load_held_out_fixtures,
    normalize_review_text,
)
from eval.provenance import get_git_sha, now_iso  # noqa: E402

SEED = 42  # repo convention: seed 42 everywhere stochastic
API = "https://openrouter.ai/api/v1"
SECRET = ("secondary-provider-api-key", "reviewiq-prod-260813", "gaurav.gandhi1129@gmail.com")

# Three families, none OpenAI / Meta (production extractors: gpt-oss-20b/120b, failover
# llama-3.3-70b). Prices (USD per 1M tokens) are the cheapest ZDR endpoint seen in
# GET /endpoints/zdr on 2026-10-09; the metered `usage.cost` is what is actually charged.
PANEL: tuple[dict[str, Any], ...] = (
    {
        "id": "mistralai/mistral-small-3.2-24b-instruct",
        "family": "Mistral",
        "in": 0.075,
        "out": 0.20,
    },
    {"id": "qwen/qwen3-235b-a22b-2507", "family": "Alibaba Qwen", "in": 0.09, "out": 0.55},
    {"id": "deepseek/deepseek-v3.2", "family": "DeepSeek", "in": 0.26, "out": 0.38},
)
# Dollar guard. The user approved USD 1.60 TOTAL on a key shared with other work.
SPEND_CAP_USD = 1.60
SPEND_ABORT_USD = 1.50
MAX_COMPLETION_TOKENS = 700
CHAR_RANGE = (100, 600)  # dev `en` fixtures have median 283 chars; <100 chars is a non-test
N_TARGET = 71

OUT_DIR = Path(
    r"C:\Users\gaura\AppData\Local\Temp\claude\C--Users-gaura-ml-projects-review-iq"
    r"\7d19aef9-518d-470e-aed1-48d8ff5d6700\scratchpad\s20\labelling"
)
SELECTION = OUT_DIR / "selection.json"
RAW_CALIB = OUT_DIR / "raw_calibration.jsonl"
RAW_LABEL = OUT_DIR / "raw_label.jsonl"
SILVER = OUT_DIR / "silver_en_fixtures.json"
CALIB_OUT = OUT_DIR / "calibration.json"
PROVENANCE = ROOT / "eval" / "results" / "s20_english_labelling_pilot.json"

# ---- pre-registered acceptance thresholds (written before any paid call; see the plan doc) ----
MAX_CONTROL_MISSES = calibration.MAX_ALLOWED_MISSES  # per judge, of 33 checks (existing rule)
MIN_ALPHA = 0.67  # Krippendorff's own floor for tentative conclusions
MIN_ALPHA_FIELDS = ("sentiment", "urgency")
MIN_RESOLVED_FRACTION = 0.85  # items with sentiment AND urgency resolved (unanimous/majority)
EARLY_CHECK_N = 20  # label this many, then re-check the gate before spending on the rest
NOMINAL = ("sentiment", "buy_again", "language")
ORDINAL = {"urgency": ["low", "medium", "high"], "stars_inferred": [1, 2, 3, 4, 5]}

# ----------------------------------------------------------------------------------------
# Power / sample size
# ----------------------------------------------------------------------------------------
Z975, Z80 = 1.959964, 0.841621  # two-sided 95% CI lower bound (as no_routing_stage1.py); 80% power
MARGIN = 0.03


def n_required(mu: float, sd: float, power: float) -> int | None:
    """Smallest n with mu - 1.96*sd/sqrt(n) >= -margin at the given power (normal approx).

    power 0.5: the expected lower bound clears the margin. power 0.8: P(lower bound >= -margin)
    is 80%. Undefined (None) when mu <= -margin (non-inferiority cannot hold).
    """
    gap = mu + MARGIN
    if gap <= 0:
        return None
    z = Z975 + (Z80 if power == 0.8 else 0.0)
    return math.ceil((z * sd / gap) ** 2)


def bootstrap_power(mu: float, sd: float, n: int, *, sims: int = 2000, boots: int = 1000) -> float:
    """Fraction of simulated studies whose paired-bootstrap 95% lower bound clears -3pp."""
    import numpy as np

    rng = np.random.default_rng(SEED)
    wins = 0
    for _ in range(sims):
        d = rng.normal(mu, sd, n)
        idx = rng.integers(0, n, (boots, n))
        means = d[idx].mean(axis=1)
        wins += np.percentile(means, 2.5) >= -MARGIN
    return wins / sims


def cmd_power() -> None:
    mus = (-0.01, 0.0, 0.01, 0.02, 0.04)
    print("n for lower 95% bound >= -3pp.  cell = n(50% power) / n(80% power)")
    for sd in (0.129, 0.181):
        print(f"SD {sd * 100:.1f}pp:", end=" ")
        for mu in mus:
            print(
                f"mu={mu * 100:+.0f}pp: {n_required(mu, sd, 0.5)}/{n_required(mu, sd, 0.8)}",
                end="  ",
            )
        print()
    for mu, sd, n in ((0.0, 0.129, 72), (0.0, 0.129, 146), (0.0, 0.181, 286), (0.0, 0.129, 71)):
        print(
            f"bootstrap (seed {SEED}, 2000 sims x 1000 resamples) mu={mu} sd={sd} n={n}: "
            f"P(lower>=-3pp) = {bootstrap_power(mu, sd, n):.3f}"
        )


# ----------------------------------------------------------------------------------------
# OpenRouter client + spend meter
# ----------------------------------------------------------------------------------------


def _key() -> str:
    import os

    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    name, project, account = SECRET
    out = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={name}",
            f"--project={project}",
            f"--account={account}",
        ],
        capture_output=True,
        text=True,
        check=True,
        shell=(sys.platform == "win32"),
    )
    return out.stdout.strip()


class Spend:
    """Persistent metered spend (USD) across invocations; refuses to start a call past the abort."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        )

    @property
    def total(self) -> float:
        return sum(e["cost"] for e in self.entries)

    def add(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)
        self.path.write_text(json.dumps(self.entries), encoding="utf-8")


SPEND = Spend(OUT_DIR / "spend_ledger.json")


def call_model(model: dict[str, Any], text: str, key: str, tag: str) -> dict[str, Any]:
    """One blind labelling call. Returns a raw record (never raises on provider errors)."""
    if SPEND.total >= SPEND_ABORT_USD:
        raise SystemExit(f"ABORT: metered spend {SPEND.total:.4f} >= {SPEND_ABORT_USD}")
    body = {
        "model": model["id"],
        "messages": [
            {"role": "system", "content": panel.JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": panel.build_user_prompt(text)},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_COMPLETION_TOKENS,
        "response_format": {"type": "json_object"},
        # zdr: only zero-data-retention endpoints; deny: no training/logging; reasoning off so the
        # budget is not spent on a hidden trace (cost and latency, and the output is the label).
        "provider": {"zdr": True, "data_collection": "deny", "sort": "price"},
        "reasoning": {"enabled": False},
        "usage": {"include": True},
    }
    req = urllib.request.Request(  # noqa: S310 -- fixed https URL
        f"{API}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    rec: dict[str, Any] = {"model": model["id"], "tag": tag, "raw": None, "error": None}
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:  # noqa: S310
                resp = json.loads(r.read())
            msg = resp["choices"][0]["message"]
            rec["raw"] = msg.get("content") or ""
            rec["finish"] = resp["choices"][0].get("finish_reason")
            rec["provider"] = resp.get("provider")
            u = resp.get("usage") or {}
            rec["prompt_tokens"] = u.get("prompt_tokens", 0)
            rec["completion_tokens"] = u.get("completion_tokens", 0)
            rec["cost"] = float(u.get("cost") or 0.0)
            rec["error"] = None
            break
        except urllib.error.HTTPError as exc:
            rec["error"] = f"HTTP {exc.code}"
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            rec["error"] = type(exc).__name__
        time.sleep(2 * (attempt + 1))
    rec["latency_s"] = round(time.time() - t0, 2)
    rec.setdefault("cost", 0.0)
    SPEND.add(
        {
            "ts": now_iso(),
            "model": model["id"],
            "tag": tag,
            "cost": rec["cost"],
            "prompt_tokens": rec.get("prompt_tokens", 0),
            "completion_tokens": rec.get("completion_tokens", 0),
        }
    )
    return rec


def run_items(items: list[dict[str, Any]], raw_path: Path, tag: str) -> None:
    """Call every panel model on every item not yet in `raw_path` (resumable, 6 workers)."""
    done = {(r["item_id"], r["model"]) for r in _read_jsonl(raw_path)}
    jobs = [(it, m) for it in items for m in PANEL if (it["id"], m["id"]) not in done]
    key = _key()

    def work(job: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        it, m = job
        rec = call_model(m, it["text"], key, tag)
        rec["item_id"] = it["id"]
        return rec

    with ThreadPoolExecutor(max_workers=6) as ex, raw_path.open("a", encoding="utf-8") as fh:
        for rec in ex.map(work, jobs):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
    print(f"{tag}: {len(jobs)} calls, metered spend so far USD {SPEND.total:.4f}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ----------------------------------------------------------------------------------------
# Selection: new English reviews, disjoint from everything the repo has seen
# ----------------------------------------------------------------------------------------

TEXT_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".html"}
SCAN_ROOTS = ("app", "docs", "tests", "eval", "benchmark", "scripts", "web")


def _tracked_text_blob() -> str:
    """Normalized concatenation of every tracked text file that could have shown a model a review.

    Covers prompts, few-shots (app/), cassettes and fixtures (eval/, benchmark/), tests and docs.
    Substring containment of a candidate's whole normalized text in this blob == "seen".
    """
    files = subprocess.run(  # noqa: S603, S607 -- fixed argv
        ["git", "ls-files", *SCAN_ROOTS], capture_output=True, text=True, check=True, cwd=ROOT
    ).stdout.splitlines()
    parts: list[str] = []
    for f in files:
        p = ROOT / f
        if p.suffix in TEXT_SUFFIXES and p.is_file() and p.stat().st_size < 30_000_000:
            parts.append(normalize_review_text(p.read_text(encoding="utf-8", errors="ignore")))
    return "\n".join(parts)


def cmd_select(n: int = N_TARGET, extra: int = 30) -> None:
    cands = load_jsonl(FLIPKART_CANDIDATES_PATH)
    held = load_held_out_fixtures()
    exact_seen = set(dev_fixture_texts()) | set(benchmark_texts())
    exact_seen |= {normalize_review_text(f["review_text"]) for f in held.values()}
    lo, hi = CHAR_RANGE
    stages = Counter()
    pool = []
    seen_keys: set[str] = set()
    for c in cands:
        stages["pool_total"] += 1
        if c.get("language") != "en":
            continue
        stages["detected_en"] += 1
        if not lo <= c["char_len"] <= hi:
            continue
        stages["in_length_window"] += 1
        k = normalize_review_text(c["text"])
        if k in exact_seen:
            stages["dropped_exact_seen"] += 1
            continue
        if k in seen_keys:
            stages["dropped_duplicate"] += 1
            continue
        seen_keys.add(k)
        pool.append(c)
    pool.sort(key=lambda c: normalize_review_text(c["text"]))  # stable before the seeded shuffle
    random.Random(SEED).shuffle(pool)
    stages["eligible_pool"] = len(pool)
    blob = _tracked_text_blob()
    picked, blob_hits = [], 0
    for c in pool:
        if normalize_review_text(c["text"]) in blob:
            blob_hits += 1
            continue
        picked.append(
            {
                "id": f"s20en-{len(picked) + 1:04d}",
                "text": c["text"],
                "source": c["source"],
                "rating_hint_unused": None,
                "char_len": c["char_len"],
            }
        )
        if len(picked) == n + extra:
            break
    stages["dropped_substring_in_tracked_text"] = blob_hits
    out = {
        "seed": SEED,
        "git_sha": get_git_sha(),
        "char_range": list(CHAR_RANGE),
        "stages": dict(stages),
        "n_selected": len(picked),
        "items": picked,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SELECTION.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["stages"]), "selected", len(picked))


# ----------------------------------------------------------------------------------------
# Consensus + statistics
# ----------------------------------------------------------------------------------------


def records_from_raw(raw: list[dict[str, Any]], texts: dict[str, str]) -> list[dict[str, Any]]:
    by_item: dict[str, dict[str, dict[str, Any] | None]] = {}
    for r in raw:
        parsed = panel.parse_judge_response(r["raw"]) if r.get("raw") else None
        by_item.setdefault(r["item_id"], {})[r["model"]] = parsed.model_dump() if parsed else None
    ids = [m["id"] for m in PANEL]
    recs = []
    for iid, outs in by_item.items():
        outs = {m: outs.get(m) for m in ids}
        recs.append(
            {
                "id": iid,
                "text": texts.get(iid, ""),
                "judge_outputs": outs,
                "consensus": consensus_for_item(outs),
            }
        )
    return sorted(recs, key=lambda r: r["id"])


def cohen_kappa(a: list[Any], b: list[Any]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb.get(k, 0) for k in ca) / n**2
    return None if pe == 1 else (po - pe) / (1 - pe)


def agreement_stats(recs: list[dict[str, Any]], *, boot: bool = False) -> dict[str, Any]:
    ids = [m["id"] for m in PANEL]
    out: dict[str, Any] = {"n_items": len(recs), "alpha": {}, "fleiss_kappa": {}, "cohen_kappa": {}}
    fields = [*NOMINAL, *ORDINAL]
    for f in fields:
        mat = raw_votes_matrix(recs, f, ids)
        level = "nominal" if f in NOMINAL else "ordinal"
        cats = None if f in NOMINAL else ORDINAL[f]
        a = krippendorff_alpha(mat, level, categories=cats)
        entry: dict[str, Any] = {"level": level, "alpha": a}
        if boot and a is not None and len(recs) >= 20:
            rng = random.Random(SEED)
            vals = []
            for _ in range(500):
                idx = [rng.randrange(len(recs)) for _ in recs]
                m2 = [[row[i] for i in idx] for row in mat]
                try:
                    v = krippendorff_alpha(m2, level, categories=cats)
                except KeyError:  # eval/agreement.py ordinal delta needs every category present
                    continue  # a resample missing a category is skipped, not imputed
                if v is not None:
                    vals.append(v)
            vals.sort()
            if vals:
                entry["ci95"] = [vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]]
        out["alpha"][f] = entry
        if f in NOMINAL:
            tbl = fleiss_table(recs, f, ids)
            out["fleiss_kappa"][f] = {
                "n_items": len(tbl),
                "kappa": fleiss_kappa(tbl) if tbl else None,
            }
        for (i, ja), (j, jb) in itertools.combinations(enumerate(ids), 2):
            xs, ys = [], []
            for k, v in enumerate(mat[i]):
                if v is not None and mat[j][k] is not None:
                    xs.append(v)
                    ys.append(mat[j][k])
            out["cohen_kappa"].setdefault(f, {})[f"{ja.split('/')[0]}|{jb.split('/')[0]}"] = (
                cohen_kappa(xs, ys)
            )
    resolved = [
        all(r["consensus"][f]["agreement"] in RESOLVED_AGREEMENT for f in MIN_ALPHA_FIELDS)
        for r in recs
    ]
    out["resolved_fraction_sentiment_urgency"] = mean(resolved) if resolved else None
    out["agreement_levels"] = {
        f: dict(Counter(r["consensus"][f]["agreement"] for r in recs))
        for f in (*NOMINAL, *ORDINAL, "pros", "cons", "topics", "product")
    }
    return out


def gate(stats: dict[str, Any]) -> dict[str, Any]:
    a = {f: stats["alpha"][f]["alpha"] for f in MIN_ALPHA_FIELDS}
    ok_alpha = all(v is not None and v >= MIN_ALPHA for v in a.values())
    rf = stats["resolved_fraction_sentiment_urgency"]
    ok_res = rf is not None and rf >= MIN_RESOLVED_FRACTION
    return {
        "alpha": a,
        "alpha_ok": ok_alpha,
        "resolved_fraction": rf,
        "resolved_ok": ok_res,
        "passed": ok_alpha and ok_res,
    }


# ----------------------------------------------------------------------------------------
# Calibration
# ----------------------------------------------------------------------------------------


def calib_items() -> list[dict[str, Any]]:
    held = load_held_out_fixtures()
    en = [f for f in held.values() if f["ground_truth"].get("language") == "en"]
    items = [
        {"id": f["id"], "text": f["review_text"], "gold": f, "kind": "heldout_en"}
        for f in sorted(en, key=lambda f: f["id"])
    ]
    items += [
        {"id": c["id"], "text": c["text"], "control": c, "kind": "control"}
        for c in calibration.load_control_set()
    ]
    return items


def cmd_calibrate() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    items = calib_items()
    run_items(items, RAW_CALIB, "calibration")
    raw = _read_jsonl(RAW_CALIB)
    texts = {i["id"]: i["text"] for i in items}
    recs = {r["id"]: r for r in records_from_raw(raw, texts)}
    # (a) per-judge misses on the unambiguous control set (existing rule: <= 2 of 33 checks)
    per_judge: dict[str, Any] = {}
    for m in PANEL:
        misses, checks = [], 0
        for it in (i for i in items if i["kind"] == "control"):
            c = it["control"]
            checks += len(c.get("expected", {})) + len(c.get("expected_list_contains", {}))
            out = recs[it["id"]]["judge_outputs"][m["id"]]
            ms = calibration.check_item_against_expected(c, out)
            if ms:
                misses.append({"item": it["id"], "fields": ms})
        n_miss = sum(len(x["fields"]) for x in misses)
        per_judge[m["id"]] = {
            "misses": n_miss,
            "checks": checks,
            "detail": misses,
            "passed": n_miss <= MAX_CONTROL_MISSES,
        }
    # (b) panel silver vs the existing consensus gold on the 5 English held-out fixtures
    vs_gold: list[dict[str, Any]] = []
    for it in (i for i in items if i["kind"] == "heldout_en"):
        g = it["gold"]
        unresolved = set(g["labeling_meta"].get("unresolved_fields", []))
        row: dict[str, Any] = {"id": it["id"]}
        for f in ("sentiment", "urgency", "buy_again", "language"):
            if f in unresolved:
                row[f] = "gold_unresolved"
                continue
            s = recs[it["id"]]["consensus"][f]
            row[f] = {
                "gold": g["ground_truth"][f],
                "panel": s["silver"],
                "level": s["agreement"],
                "match": s["silver"] == g["ground_truth"][f],
            }
        vs_gold.append(row)
    stats_all = agreement_stats(list(recs.values()))
    stats_en = agreement_stats([recs[i["id"]] for i in items if i["kind"] == "heldout_en"])
    res = {
        "git_sha": get_git_sha(),
        "generated_at": now_iso(),
        "panel": [m["id"] for m in PANEL],
        "control_per_judge": per_judge,
        "vs_existing_gold_heldout_en": vs_gold,
        "stats_all_21": stats_all,
        "stats_heldout_en_5": stats_en,
        "gate_all_21": gate(stats_all),
        "spend_usd": SPEND.total,
    }
    CALIB_OUT.write_text(
        json.dumps(res, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(json.dumps({k: res[k] for k in ("gate_all_21", "spend_usd")}, default=str))
    print({m: (v["misses"], v["checks"]) for m, v in per_judge.items()})


# ----------------------------------------------------------------------------------------
# Labelling + report
# ----------------------------------------------------------------------------------------


def cmd_label(n: int) -> None:
    sel = json.loads(SELECTION.read_text(encoding="utf-8"))["items"]
    items = sel[:n]
    texts = {i["id"]: i["text"] for i in sel}
    first = items[:EARLY_CHECK_N]
    run_items(first, RAW_LABEL, "label")
    g = gate(agreement_stats(records_from_raw(_read_jsonl(RAW_LABEL), texts)))
    print("early gate on first", len(first), json.dumps(g, default=str))
    if not g["passed"]:
        print("STOP: pre-registered gate failed on the early batch; no further spend.")
        return
    run_items(items[EARLY_CHECK_N:], RAW_LABEL, "label")


def cmd_report() -> None:
    sel = json.loads(SELECTION.read_text(encoding="utf-8"))
    texts = {i["id"]: i["text"] for i in sel["items"]}
    raw = _read_jsonl(RAW_LABEL)
    recs = records_from_raw(raw, texts)
    stats = agreement_stats(recs, boot=True)
    g = gate(stats)
    fixtures, dropped = [], Counter()
    for r in recs:
        lang = r["consensus"]["language"]
        if lang["agreement"] not in RESOLVED_AGREEMENT or lang["silver"] != "en":
            dropped["language_not_resolved_en"] += 1
            continue
        c = r["consensus"]

        def silver(f: str, c: dict[str, Any] = c) -> Any:
            return c[f]["silver"] if c[f]["agreement"] in RESOLVED_AGREEMENT else None

        gt = {
            "product": silver("product") or "unknown",
            "stars": silver("stars"),
            "stars_inferred": silver("stars_inferred"),
            "pros": silver("pros") or [],
            "cons": silver("cons") or [],
            "buy_again": silver("buy_again"),
            "sentiment": silver("sentiment"),
            "topics": silver("topics") or [],
            "competitor_mentions": silver("competitor_mentions") or [],
            "urgency": silver("urgency"),
            "feature_requests": silver("feature_requests") or [],
            "language": "en",
        }
        unresolved = sorted(
            f
            for f in gt
            if c.get(f, {}).get("agreement") not in RESOLVED_AGREEMENT and f != "language"
        )
        fixtures.append(
            {
                "id": r["id"],
                "review_text": r["text"],
                "ground_truth": gt,
                "labeling_meta": {
                    "labeled_by": "multi-llm-consensus-openrouter-zdr",
                    "tier": "silver",
                    "quarantined": True,
                    "blind": True,
                    "panel": [m["id"] for m in PANEL],
                    "agreement_per_field": {f: x["agreement"] for f, x in c.items()},
                    "unresolved_fields": unresolved,
                },
            }
        )
    SILVER.write_text(json.dumps(fixtures, indent=1, ensure_ascii=False), encoding="utf-8")
    calib = json.loads(CALIB_OUT.read_text(encoding="utf-8")) if CALIB_OUT.exists() else None
    per_model: dict[str, Any] = {}
    for m in PANEL:
        rs = [x for x in _read_jsonl(RAW_LABEL) + _read_jsonl(RAW_CALIB) if x["model"] == m["id"]]
        lab = [x for x in rs if x["tag"] == "label"]
        per_model[m["id"]] = {
            "calls": len(rs),
            "errors": sum(1 for x in rs if x.get("error")),
            "usd": round(sum(x["cost"] for x in rs), 6),
            "mean_prompt_tokens": mean(x["prompt_tokens"] for x in lab) if lab else None,
            "mean_completion_tokens": mean(x["completion_tokens"] for x in lab) if lab else None,
            "providers": dict(Counter(x.get("provider") for x in rs)),
            "unparseable": sum(
                1 for x in rs if x.get("raw") and not panel.parse_judge_response(x["raw"])
            ),
        }
    prov = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "script": "eval/experiments/english_labelling_pilot.py",
        "seed": SEED,
        "plan": "docs/research/s20-english-fixture-labelling-plan.md",
        "panel": [{"id": m["id"], "family": m["family"]} for m in PANEL],
        "thresholds": {
            "max_control_misses": MAX_CONTROL_MISSES,
            "min_alpha": MIN_ALPHA,
            "alpha_fields": list(MIN_ALPHA_FIELDS),
            "min_resolved_fraction": MIN_RESOLVED_FRACTION,
        },
        "selection": {
            "stages": sel["stages"],
            "n_selected": sel["n_selected"],
            "char_range": sel["char_range"],
        },
        "n_labelled": len(recs),
        "n_silver_fixtures_en": len(fixtures),
        "dropped": dict(dropped),
        "stats": stats,
        "gate": g,
        "per_model": per_model,
        "calibration": calib,
        "spend_usd_metered_total": round(SPEND.total, 6),
        "spend_cap_usd": SPEND_CAP_USD,
        "silver_fixtures_location": "scratchpad s20/labelling/silver_en_fixtures.json (NOT committed)",
    }
    PROVENANCE.write_text(
        json.dumps(prov, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "n": len(recs),
                "silver": len(fixtures),
                "gate": g,
                "spend": prov["spend_usd_metered_total"],
            },
            default=str,
        )
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["power", "select", "calibrate", "label", "report"])
    ap.add_argument("--n", type=int, default=N_TARGET)
    a = ap.parse_args()
    if a.mode == "power":
        cmd_power()
    elif a.mode == "select":
        cmd_select(a.n)
    elif a.mode == "calibrate":
        cmd_calibrate()
    elif a.mode == "label":
        cmd_label(a.n)
    else:
        cmd_report()


if __name__ == "__main__":
    main()
