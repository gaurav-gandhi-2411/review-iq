"""Authenticity eval runner.

Usage:
  uv run python eval/authenticity/runner.py [--corpus labeled|held-out] [--mode replay|record]
                                            [--dry-run] [--out PATH]

  --corpus labeled   (default) the 40 in-repo labelled fixtures (eval/authenticity/fixtures/)
  --corpus held-out  the 106 quarantined held-out reviews (eval/fixtures/_held_out_hindi_hinglish/).
                     These carry NO authenticity label, so only per-item predictions + flag rate
                     are emitted -- no precision/recall/F1 (see scoring.predictions_only_report).
  --mode replay      (default) LLM calls served from eval/cassettes/authenticity_cassettes.json;
                     a missing key aborts the run (exit 3) before any scoring -- never a live call.
  --mode record      EXPLICIT opt-in: makes live Groq calls and records them. Never used in CI.
  --dry-run          skip the LLM entirely, heuristics-only scoring (CI smoke test)

Results go to eval/results/authenticity_<corpus>_<mode>.json. This runner never writes
eval/results/authenticity_latest.json (quarantined -- see that file's own `status` field).

Exit codes: 0 ok (labeled corpus: precision >= 0.80), 1 precision gate failed, 2 INVALID RUN
(LLM error), 3 cassette miss in replay mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Add project root to sys.path so 'app' is importable
sys.path.insert(0, str(Path(__file__).parents[2]))

from app.core.authenticity.engine import score_single
from app.core.authenticity.heuristics import compute_heuristic_score
from app.core.authenticity.schema import AuthenticityLabel, AuthenticityResult
from app.core.config import get_settings
from app.core.language import detect_language

from eval.authenticity import replay as replay_mod
from eval.authenticity.scoring import labelled_report, predictions_only_report
from eval.provenance import get_git_sha, now_iso
from eval.wilson import wilson_ci

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "labeled.jsonl"
HELD_OUT_DIR = Path(__file__).parents[1] / "fixtures" / "_held_out_hindi_hinglish"
# Canonical results file consumed by scripts/render_metrics.py — see eval/runner.py's
# RESULTS_DIR/LATEST_RESULTS_PATH for the sibling convention used by the main extraction eval.
RESULTS_DIR = Path(__file__).parents[1] / "results"
RESULTS_PATH = RESULTS_DIR / "authenticity_latest.json"
PRECISION_GATE = 0.80
RECALL_TARGET = 0.60


def load_fixtures() -> list[dict]:
    """Load all fixtures from labeled.jsonl."""
    with FIXTURES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def is_flagged_true(true_label: str) -> bool:
    """Ground truth: suspicious or likely_fake = flagged."""
    return true_label in ("suspicious", "likely_fake")


def is_flagged_pred(result: AuthenticityResult) -> bool:
    """Prediction: suspicious or likely_fake = flagged."""
    return result.label in (AuthenticityLabel.SUSPICIOUS, AuthenticityLabel.LIKELY_FAKE)


def compute_metrics(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Compute precision, recall, and F1 from confusion matrix counts."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def write_results(
    tp: int,
    fp: int,
    fn: int,
    tn: int,
    mode: str,
    provenance_note: str | None = None,
    out_path: Path = RESULTS_PATH,
) -> None:
    """Write the authenticity eval's confusion matrix + metrics + Wilson CIs as JSON.

    `n` for each metric follows its own binomial trial count — Wilson's interval assumes
    independent Bernoulli trials, so each proportion needs the count it was actually computed
    over (see eval/wilson.py for the full limitation writeup):
      - precision: n = tp + fp (count of positive predictions)
      - recall:    n = tp + fn (count of actual positives)
      - f1: F1 is a harmonic mean of precision and recall, not itself a single binomial
        proportion — no n is exactly correct. As a documented, conservative approximation we
        use n = tp+fp+fn+tn (every scored fixture). Treat the f1 CI as a looser sanity bound,
        not a precise interval.
    """
    m = compute_metrics(tp, fp, fn)
    n_precision = tp + fp
    n_recall = tp + fn
    n_total = tp + fp + fn + tn

    precision_lo, precision_hi = wilson_ci(m["precision"], n_precision)
    recall_lo, recall_hi = wilson_ci(m["recall"], n_recall)
    f1_lo, f1_hi = wilson_ci(m["f1"], n_total)

    payload = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "mode": mode,
        "provenance_note": provenance_note,
        "n": n_total,
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": {
            "value": m["precision"],
            "n": n_precision,
            "ci_95": {"lower": precision_lo, "upper": precision_hi},
        },
        "recall": {
            "value": m["recall"],
            "n": n_recall,
            "ci_95": {"lower": recall_lo, "upper": recall_hi},
        },
        "f1": {
            "value": m["f1"],
            "n": n_total,
            "ci_95": {"lower": f1_lo, "upper": f1_hi},
            "note": "n=all scored fixtures (approximation) -- F1 is not a single binomial proportion.",
        },
        "precision_gate": PRECISION_GATE,
        "recall_target": RECALL_TARGET,
        "gate_passed": m["precision"] >= PRECISION_GATE,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def score_heuristic_only(text: str, stars: int | None) -> AuthenticityResult:
    """Dry-run path: heuristics only, no LLM call."""
    heuristic_score, flags = compute_heuristic_score(text, stars)
    return AuthenticityResult.from_signals(
        heuristic_score=heuristic_score,
        llm_score=None,
        flags=flags,
        reasons="heuristic-only (dry-run)",
        review_text=text,
        model_used=None,
    )


def load_held_out_fixtures() -> list[dict[str, Any]]:
    """Load the 106 quarantined held-out reviews as {id, text, stars, language}.

    These fixtures carry extraction ground truth only (sentiment/urgency/...); there is NO
    authenticity/suspicious label, so no `true_label` key is set.
    """
    items: list[dict[str, Any]] = []
    for p in sorted(HELD_OUT_DIR.glob("hien-*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        gt = data.get("ground_truth", {})
        items.append(
            {
                "id": data["id"],
                "text": data["review_text"],
                "stars": gt.get("stars"),  # observed rating only; stars_inferred is not an input
                "language": gt.get("language"),
            }
        )
    return items


def load_corpus(corpus: str) -> tuple[list[dict[str, Any]], str, bool]:
    """Return (items, labels_source, is_held_out) for `corpus`."""
    if corpus == "held-out":
        return (
            load_held_out_fixtures(),
            "none: eval/fixtures/_held_out_hindi_hinglish/hien-*.json carry extraction labels "
            "only (product/stars/pros/cons/buy_again/sentiment/topics/competitor_mentions/"
            "urgency/feature_requests/language); no authenticity/suspicious/fake label exists",
            True,
        )
    if corpus == "labeled":
        return (
            load_fixtures(),
            "eval/authenticity/fixtures/labeled.jsonl (true_label: genuine|suspicious|"
            "likely_fake; in-repo fixture set, not a held-out sample)",
            False,
        )
    raise ValueError(f"unknown corpus {corpus!r}")


def _item_record(
    item: dict[str, Any], result: AuthenticityResult | None, key: str | None
) -> dict[str, Any]:
    """Per-item prediction record with full provenance (key, model, tokens, signals)."""
    from app.core.providers import cassette as cassette_module

    tokens = cassette_module.replay(key) if key else None
    rec: dict[str, Any] = {
        "id": item["id"],
        "detected_language": detect_language(item["text"]),
        "stars": item.get("stars"),
        "cassette_key": key,
        "tokens_in": tokens[1] if tokens else None,
        "tokens_out": tokens[2] if tokens else None,
    }
    if "true_label" in item:
        rec["true_label"] = item["true_label"]
    if result is not None:
        rec.update(
            {
                "pred_label": result.label.value,
                "pred_flagged": is_flagged_pred(result),
                "score": result.score,
                "flags": [f.value for f in result.flags],
                "llm_signal_ok": result.llm_signal_ok,
                "model_used": result.model_used,
            }
        )
    return rec


def build_output(
    *,
    corpus: str,
    mode: str,
    items: list[dict[str, Any]],
    records: list[dict[str, Any]],
    labels_source: str,
    is_held_out: bool,
    models: dict[str, str],
    cassette_path: Path,
    n_resamples: int | None = None,
) -> dict[str, Any]:
    """Assemble the output JSON: provenance header + report + per-item records.

    Ground truth is used only if the corpus has a label source; otherwise the report is
    predictions-only (flag rate, no P/R/F1).
    """
    y_pred = [bool(r["pred_flagged"]) for r in records]
    has_labels = all("true_label" in it for it in items) and len(items) > 0
    if has_labels:
        y_true = [is_flagged_true(it["true_label"]) for it in items]
        kw: dict[str, Any] = {} if n_resamples is None else {"n_resamples": n_resamples}
        report = labelled_report(y_true, y_pred, is_held_out=is_held_out, **kw)
    else:
        report = predictions_only_report(y_pred)
    try:
        cassette_file = str(cassette_path.resolve().relative_to(Path(__file__).parents[2]))
    except ValueError:
        cassette_file = str(cassette_path)
    return {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "mode": mode,
        "corpus": corpus,
        "n": len(records),
        "runs": 1,
        "single_run": True,
        "labels_source": labels_source,
        "is_held_out": is_held_out,
        "models": models,
        "cassette": {
            "path": cassette_file.replace("\\", "/"),
            "entry_count": replay_mod.cassette_entry_count(cassette_path),
            "key_scheme": replay_mod.CASSETTE_KEY_SCHEME,
        },
        "report": report,
        "records": records,
    }


async def run_eval(
    corpus: str = "labeled",
    mode: str = "replay",
    dry_run: bool = False,
    out_path: Path | None = None,
    cassette_path: Path = replay_mod.AUTHENTICITY_CASSETTES_PATH,
) -> int:
    """Score `corpus`; return the process exit code (see module docstring)."""
    items, labels_source, is_held_out = load_corpus(corpus)
    settings = get_settings()
    model = settings.groq_model_large  # authenticity always uses the large model
    run_mode = "dry-run" if dry_run else mode

    keys: dict[str, str] = {}
    if not dry_run:
        if mode == "replay":
            try:
                keys = replay_mod.preflight([it["text"] for it in items], model, cassette_path)
            except replay_mod.CassetteMissError as exc:
                print(f"CASSETTE MISS: {exc}")
                return 3
        else:
            if not settings.groq_api_key:
                print("record mode needs GROQ_API_KEY; refusing.")
                return 2
            keys = {
                str(i): replay_mod.authenticity_cassette_key(it["text"], model)
                for i, it in enumerate(items)
            }
        replay_mod.configure_cassettes(mode, cassette_path)  # type: ignore[arg-type]

    results: list[AuthenticityResult] = []
    records: list[dict[str, Any]] = []
    llm_error_count = 0
    for i, item in enumerate(items):
        text, stars = item["text"], item.get("stars")
        if dry_run:
            result = score_heuristic_only(text, stars)
        else:
            result = await score_single(text, stars=stars, settings=settings)
            if not result.llm_signal_ok:
                llm_error_count += 1
                print(f"  [LLM ERROR on item {item['id']}]")
        results.append(result)
        records.append(_item_record(item, result, keys.get(str(i))))
        truth = f" true={item['true_label']:<12}" if "true_label" in item else ""
        print(f"[{i + 1:03d}]{truth} pred={result.label.value:<12} score={result.score:.2f}")

    if llm_error_count > 0:
        print(f"\nINVALID RUN: LLM signal failed on {llm_error_count}/{len(items)} rows.")
        return 2

    payload = build_output(
        corpus=corpus,
        mode=run_mode,
        items=items,
        records=records,
        labels_source=labels_source,
        is_held_out=is_held_out,
        models={
            "groq_model_small": settings.groq_model_small,
            "groq_model_large": settings.groq_model_large,
            "authenticity_model": model,
        },
        cassette_path=cassette_path,
    )
    report = payload["report"]
    print("\n" + json.dumps(report, indent=2))
    out = out_path or RESULTS_DIR / f"authenticity_{corpus.replace('-', '_')}_{run_mode}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Results written to {out}")

    if corpus == "labeled":
        precision = report["precision"]["value"]
        gate_pass = precision is not None and precision >= PRECISION_GATE
        print(f"Precision gate (>= {PRECISION_GATE:.2f}): {'PASS' if gate_pass else 'FAIL'}")
        return 0 if gate_pass else 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", choices=["labeled", "held-out"], default="labeled")
    parser.add_argument("--mode", choices=["replay", "record"], default="replay")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(run_eval(args.corpus, args.mode, args.dry_run, args.out)))
