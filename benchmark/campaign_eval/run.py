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
from benchmark.campaign_eval.grid import B1_THETAS, B2_THETAS, build_grid

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


def _column_table(results: list[dict], method: str, n_cols: int) -> dict[str, np.ndarray]:
    return an.column_stats(results, method, n_cols)


def stage_tune() -> None:
    products, cfg_split = _split(), split_configs()
    sel = full_selection()
    t0 = time.time()
    tasks = [
        (k, sample_tuning_configs(k, cfg_split["tuning"]), sel, True) for k in products["tuning"]
    ]
    tasks += [(k, cfg_split["validation"], sel, True) for k in products["validation"]]
    results = _run_pool(tasks)
    n_t = len(products["tuning"])
    tune, val = results[:n_t], results[n_t:]
    save_pickle({"tuning": tune, "validation": val}, "tune_results.pkl")
    print(f"evaluated {len(results)} streams in {time.time() - t0:.0f}s")

    grid = build_grid()
    cols = {
        "detector": ("det", len(grid)),
        "b1": ("b1", len(B1_THETAS)),
        "b2": ("b2", len(B2_THETAS)),
    }
    stats = {
        m: (an.column_stats(tune, k, n), an.column_stats(val, k, n)) for m, (k, n) in cols.items()
    }
    frozen: dict = {"seed": 42, "budgets": {}}
    report: dict = {"n_tuning_streams": n_t, "n_validation_streams": len(val), "budgets": {}}
    for budget in an.BUDGETS:
        entry: dict = {}
        # signal (d) ablation: enabled only if it adds >= 1.0 pp tuning recall at this budget
        t, v = stats["detector"]
        within = t["far"] <= budget
        best_off = t["rec"][within & ~grid.use_m].max() if (within & ~grid.use_m).any() else 0.0
        best_on = t["rec"][within & grid.use_m].max() if (within & grid.use_m).any() else 0.0
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
                    else {"theta": float((B1_THETAS if m == "b1" else B2_THETAS)[col])}
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
    _write("tuning_results.json", report)
    _write("frozen_params.json", frozen)
    print(json.dumps(report["budgets"], indent=1)[:3000])


def _frozen_selection(frozen: dict) -> tuple[Selection, dict[str, dict[int, int]]]:
    """Only the frozen columns are evaluated on the sealed set."""
    cols: dict[str, list[int]] = {"det": [], "b1": [], "b2": []}
    for b in frozen["budgets"].values():
        for name, k in (
            ("detector", "det"),
            ("b1", "b1"),
            ("b2", "b2"),
            ("b1_half", "b1"),
            ("b2_half", "b2"),
        ):
            c = b[name]["column"]
            if c is not None and c not in cols[k]:
                cols[k].append(c)
    pos = {k: {c: i for i, c in enumerate(v)} for k, v in cols.items()}
    sel = Selection(
        np.array(cols["det"], dtype=int),
        np.array([B1_THETAS[c] for c in cols["b1"]]),
        np.array([B2_THETAS[c] for c in cols["b2"]]),
    )
    return sel, pos


def stage_sealed() -> None:
    if (OUT / "sealed_results.json").exists():
        sys.exit("sealed_results.json exists: the sealed set is run once (spec section 7)")
    frozen = json.loads((OUT / "frozen_params.json").read_text(encoding="utf-8"))
    products, cfg_split = _split(), split_configs()
    sel, pos = _frozen_selection(frozen)
    tasks = [(k, cfg_split["sealed"], sel, True) for k in products["sealed"]]
    results = _run_pool(tasks)
    save_pickle(results, "sealed_results.pkl")
    out: dict = {
        "label": "SYNTHETIC injected campaigns on ASSUMED-ORGANIC clean streams",
        "budgets": {},
    }
    for budget, fz in frozen["budgets"].items():
        res: dict = {}
        frames = {}
        for name, k in (
            ("detector", "det"),
            ("b1", "b1"),
            ("b2", "b2"),
            ("b1_half", "b1"),
            ("b2_half", "b2"),
        ):
            c = fz[name]["column"]
            if c is None:
                continue
            frames[name] = an.Frame(results, k, pos[k][c])
            res[name] = an.summarize(frames[name])
            res[name]["params"] = fz[name]["params"]
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
        out["budgets"][budget] = res
    _write("sealed_results.json", out)
    print("sealed done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["prepare", "tune", "sealed"])
    a = ap.parse_args()
    {"prepare": stage_prepare, "tune": stage_tune, "sealed": stage_sealed}[a.stage]()


if __name__ == "__main__":
    main()
