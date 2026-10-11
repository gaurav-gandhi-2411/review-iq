"""Pre-registered campaign-detection evaluation (docs/specs/campaign-detection.md).

Stages (run from the repo root with PYTHONPATH=.; engine venv has numpy/pandas):
  prepare   load + qualify + split the clean streams; write reports/campaign_eval/split.json
  tune      tuning + validation products: grid evaluation, threshold selection under the budgets,
            write tuning_results.json and frozen_params.json (the sealed set is never opened)
  sealed    run ONCE on the sealed products x sealed configurations with the frozen parameters
  pairs     pairwise template-signal evaluation (see pairs.py)

Everything injected is SYNTHETIC. Clean streams are ASSUMED ORGANIC.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from benchmark.campaign_eval import analysis as an
from benchmark.campaign_eval.data import Stream, load_amazon, load_sephora, split_keys
from benchmark.campaign_eval.evaluate import (
    Selection,
    evaluate_stream,
    full_selection,
    load_pickle,
    sample_tuning_configs,
    save_pickle,
)
from benchmark.campaign_eval.generator import (
    all_configs,
    generate_campaign,
    split_configs,
)
from benchmark.campaign_eval.grid import grid_from_params

OUT = Path("reports/campaign_eval")
WORKERS = 8
_STREAMS: dict[str, Stream] = {}


def _init() -> None:
    for corpus in ("amazon", "sephora"):
        _STREAMS.update(load_pickle(f"streams_{corpus}.pkl"))  # type: ignore[arg-type]


def _work(args: tuple) -> dict:
    key, cfgs, sel, do_clean = args
    return evaluate_stream(_STREAMS[key], cfgs, sel, do_clean)


def _run_pool(tasks: list[tuple]) -> list[dict]:
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=WORKERS, initializer=_init) as ex:
        for i, r in enumerate(ex.map(_work, tasks, chunksize=1)):
            results.append(r)
            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(tasks)} streams, {time.time() - t0:.0f}s", flush=True)
    return results


def _write(name: str, obj: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _split() -> dict[str, list[str]]:
    return json.loads((OUT / "split.json").read_text(encoding="utf-8"))["products"]


def stage_prepare() -> None:
    amazon, sephora = load_amazon(), load_sephora()
    save_pickle(amazon, "streams_amazon.pkl")
    save_pickle(sephora, "streams_sephora.pkl")
    products = split_keys({"amazon": list(amazon), "sephora": list(sephora)})
    streams = {**amazon, **sephora}
    cfgs = split_configs()
    # verbatim-copy check: no generated text may equal a corpus text (normalised)
    from app.core.detectors.campaign_signals import normalize_cluster_text

    corpus_texts = {normalize_cluster_text(t) for s in streams.values() for t in s.text}
    from datetime import UTC, datetime

    gen, hits = 0, 0
    for i, cfg in enumerate(all_configs()):
        for r in generate_campaign(cfg, datetime(2024, 1, 1, tzinfo=UTC), 1000 + i):
            gen += 1
            hits += normalize_cluster_text(r.text) in corpus_texts
    _write(
        "split.json",
        {
            "seed": 42,
            "note": "products split per corpus (50/20/30, seed 42); configs split per "
            "(size, similarity) cell (5 sealed / 3 validation / 7 tuning)",
            "n_streams": {"amazon": len(amazon), "sephora": len(sephora)},
            "n_reviews": {
                c: int(sum(len(s) for s in d.values()))
                for c, d in (("amazon", amazon), ("sephora", sephora))
            },
            "products": products,
            "n_products": {k: len(v) for k, v in products.items()},
            "configs": {k: [c.config_id for c in v] for k, v in cfgs.items()},
            "generated_texts_checked": gen,
            "generated_texts_equal_to_a_corpus_text": hits,
            "attribution": "Sephora Skincare Reviews, CC BY 4.0, Melissa Monfared (Kaggle). "
            "Amazon Fine Food Reviews, CC0-1.0.",
        },
    )
    print("prepared", {k: len(v) for k, v in products.items()}, "verbatim hits", hits, "/", gen)


def stage_tune(version: str) -> None:
    """Grid search on tuning + validation products only; the sealed set is never opened."""
    products, cfg_split = _split(), split_configs()
    sel = full_selection(version)
    grid, b1_th, b2_th = sel.grid, sel.b1, sel.b2
    t0 = time.time()
    tasks = [
        (k, sample_tuning_configs(k, cfg_split["tuning"]), sel, True) for k in products["tuning"]
    ]
    tasks += [(k, cfg_split["validation"], sel, True) for k in products["validation"]]
    results = _run_pool(tasks)
    n_t = len(products["tuning"])
    tune, val = results[:n_t], results[n_t:]
    save_pickle({"tuning": tune, "validation": val}, f"tune_results_{version}.pkl")
    print(f"[{version}] evaluated {len(results)} streams in {time.time() - t0:.0f}s")

    cols = {"detector": ("det", len(grid)), "b1": ("b1", len(b1_th)), "b2": ("b2", len(b2_th))}
    stats = {
        m: (an.column_stats(tune, k, n), an.column_stats(val, k, n)) for m, (k, n) in cols.items()
    }
    frozen: dict = {"seed": 42, "grid_version": version, "budgets": {}}
    report: dict = {
        "grid_version": version,
        "n_tuning_streams": n_t,
        "n_validation_streams": len(val),
        "n_tuning_injections": int(stats["detector"][0]["n_inj"][0]),
        "n_validation_injections": int(stats["detector"][1]["n_inj"][0]),
        "tuning_product_months": float(stats["detector"][0]["months"][0]),
        "grid_size": len(grid),
        "tuning_false_alert_rate_range_over_grid": {
            m: [float(stats[m][0]["far"].min()), float(stats[m][0]["far"].max())] for m in stats
        },
        "budgets": {},
    }
    for budget in an.BUDGETS:
        entry: dict = {}
        # signal (d) ablation: enabled only if it adds >= 1.0 pp tuning recall at this budget
        t, v = stats["detector"]
        within = t["far"] <= budget
        off, on = within & ~grid.use_m, within & grid.use_m
        best_off = t["rec"][off].max() if off.any() else 0.0
        best_on = t["rec"][on].max() if on.any() else 0.0
        mismatch_on = (best_on - best_off) >= 0.01
        entry["mismatch_ablation"] = {
            "best_tuning_recall_without": float(best_off),
            "best_tuning_recall_with": float(best_on),
            "enabled": bool(mismatch_on),
        }
        allowed = None if mismatch_on else ~grid.use_m
        picks = {
            "detector": an.select_column(t, v, budget, allowed),
            "b1": an.select_column(*stats["b1"], budget),
            "b2": an.select_column(*stats["b2"], budget),
            "b1_half": an.select_column(*stats["b1"], budget / 2),
            "b2_half": an.select_column(*stats["b2"], budget / 2),
        }
        for name, col in picks.items():
            m = name.split("_")[0]
            tt, vv = stats[m]
            params = None
            if col is not None:
                params = (
                    grid.params(col)
                    if m == "detector"
                    else {"theta": float((b1_th if m == "b1" else b2_th)[col])}
                )
            entry[name] = {
                "column": col,
                "params": params,
                "tuning": None
                if col is None
                else {"far": float(tt["far"][col]), "recall": float(tt["rec"][col])},
                "validation": None
                if col is None
                else {"far": float(vv["far"][col]), "recall": float(vv["rec"][col])},
            }
        b1v = entry["b1"]["validation"]["recall"] if entry["b1"]["column"] is not None else -1
        b2v = entry["b2"]["validation"]["recall"] if entry["b2"]["column"] is not None else -1
        entry["best_baseline_by_validation_recall"] = "b1" if b1v >= b2v else "b2"
        report["budgets"][str(budget)] = entry
        frozen["budgets"][str(budget)] = {
            k: v
            for k, v in entry.items()
            if k in picks or k in ("best_baseline_by_validation_recall", "mismatch_ablation")
        }
    _write(f"tuning_results_grid_{version}.json", report)
    _write(f"frozen_params_grid_{version}.json", frozen)
    print(json.dumps(report["budgets"], indent=1)[:2500])


def _frozen_selection(frozen_by_version: dict[str, dict]) -> tuple[Selection, dict]:
    """Sub-grid of ONLY the frozen picks (all versions and budgets); returns position lookups."""
    det: list[dict] = []
    b1: list[float] = []
    b2: list[float] = []
    for fz in frozen_by_version.values():
        for b in fz["budgets"].values():
            for name in ("detector", "b1", "b2", "b1_half", "b2_half"):
                p = b[name]["params"]
                if p is None:
                    continue
                if name == "detector" and p not in det:
                    det.append(p)
                elif name.startswith("b1") and p["theta"] not in b1:
                    b1.append(p["theta"])
                elif name.startswith("b2") and p["theta"] not in b2:
                    b2.append(p["theta"])
    sel = Selection(grid_from_params(det), np.array(b1), np.array(b2))
    return sel, {"det": det, "b1": b1, "b2": b2}


def stage_sealed() -> None:
    """The one sealed look: sealed products x sealed configurations, frozen picks of grid v1
    (as pre-registered) and grid v2 (amendment 7; the primary), evaluated in the same run."""
    if (OUT / "sealed_results.json").exists():
        sys.exit("sealed_results.json exists: the sealed set is run once (spec section 7)")
    frozen_by_version = {
        v: json.loads((OUT / f"frozen_params_grid_{v}.json").read_text(encoding="utf-8"))
        for v in ("v1", "v2")
    }
    products, cfg_split = _split(), split_configs()
    sel, pos = _frozen_selection(frozen_by_version)
    results = _run_pool([(k, cfg_split["sealed"], sel, True) for k in products["sealed"]])
    save_pickle(results, "sealed_results.pkl")
    out: dict = {
        "label": "SYNTHETIC injected campaigns on ASSUMED-ORGANIC clean streams",
        "primary_grid": "v2",
        "n_sealed_products": len(products["sealed"]),
        "grids": {},
    }
    for version, frozen in frozen_by_version.items():
        out["grids"][version] = {}
        for budget, fz in frozen["budgets"].items():
            res: dict = {}
            frames = {}
            for name, key in (
                ("detector", "det"),
                ("b1", "b1"),
                ("b2", "b2"),
                ("b1_half", "b1"),
                ("b2_half", "b2"),
            ):
                p = fz[name]["params"]
                if p is None:
                    continue
                col = pos["det"].index(p) if key == "det" else pos[key].index(p["theta"])
                frames[name] = an.Frame(results, key, col)
                res[name] = an.summarize(frames[name])
                res[name]["params"] = p
            best = fz["best_baseline_by_validation_recall"]
            if "detector" in frames and best in frames:
                res["delta_recall_detector_minus_best_baseline"] = {
                    "baseline": best,
                    **an.paired_delta(frames["detector"], frames[best]),
                }
                for other in ("b1", "b2"):
                    if other in frames:
                        res[f"delta_recall_detector_minus_{other}"] = an.paired_delta(
                            frames["detector"], frames[other]
                        )
            if "b1_half" in frames and "b2_half" in frames:
                # informational only: B1 or B2, each tuned to half the budget
                res["union_b1_b2_half_budget_each"] = an.summarize(
                    an.union_frame(frames["b1_half"], frames["b2_half"])
                )
            out["grids"][version][budget] = res
    _write("sealed_results.json", out)
    print("sealed done")


def stage_pairs() -> None:
    """Template-signal pair evaluation on the SEALED products and sealed configurations; run once,
    after the sealed alert evaluation, at every grid similarity (the frozen choices are listed)."""
    from benchmark.campaign_eval.pairs import evaluate_pairs
    from benchmark.campaign_eval.trace import SIMS

    if (OUT / "pairs_results.json").exists():
        sys.exit("pairs_results.json exists: run once")
    _init()
    streams = [_STREAMS[k] for k in _split()["sealed"]]
    out = evaluate_pairs(streams, split_configs()["sealed"], list(SIMS))
    out["frozen_similarity"] = {}
    for v in ("v1", "v2"):
        fz = json.loads((OUT / f"frozen_params_grid_{v}.json").read_text(encoding="utf-8"))
        out["frozen_similarity"][v] = {
            b: (e["detector"]["params"] or {}).get("similarity") for b, e in fz["budgets"].items()
        }
    _write("pairs_results.json", out)
    print("pairs done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["prepare", "tune", "sealed", "pairs"])
    ap.add_argument("--grid", choices=["v1", "v2"], default="v2")
    a = ap.parse_args()
    if a.stage == "tune":
        stage_tune(a.grid)
    else:
        {"prepare": stage_prepare, "sealed": stage_sealed, "pairs": stage_pairs}[a.stage]()


if __name__ == "__main__":
    main()
