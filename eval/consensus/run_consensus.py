"""End-to-end consensus-labeling run: calibrate -> label -> validate -> grow -> report.

Usage:
    uv run python eval/consensus/run_consensus.py [--max-new-en N] [--max-new-hien N]

Pipeline:
  1. Load the calibration report (eval/consensus/results/calibration_report.json) --
     run calibration fresh if missing. Determines the ACTIVE PANEL for this run (a
     judge that fails calibration is dropped, never silently kept -- see
     calibration.py).
  2. Build the full item list: every already-committed eval/fixtures/ entry (mode
     "validate") + up to `--max-new-en`/`--max-new-hien` new candidates from
     eval/data/flipkart_candidates.jsonl (mode "grow", excluding anything already a
     fixture). Real yield for hi/hi-en is low and capped by the actual corpus, not by
     an arbitrary target -- see candidates.py and this run's printed summary.
  3. Label every item with every active-panel judge (concurrent per item, paced
     between items), computing per-field consensus (voting.consensus_for_item).
     Results stream to eval/consensus/results/consensus_labels.jsonl (resumable --
     already-labeled ids are skipped on a rerun).
  4. Validation pass: compare consensus silver labels to the ALREADY-COMMITTED ground
     truth for existing fixtures -- reports the agreement rate honestly (large
     disagreement is a finding, not hidden).
  5. Growth: candidates that reach a non-split consensus on ALL of
     sentiment/urgency/buy_again/language become new fixture files under
     eval/fixtures/ (en) or eval/fixtures/hi-en/ (hi-en), continuing the existing
     numbering.
  6. Reliability stats: Krippendorff's alpha (nominal: sentiment/buy_again/language;
     ordinal: urgency, stars_inferred) and Fleiss' kappa (fully-covered subset) across
     ALL labeled items (existing + growth), from the RAW per-judge votes, not the
     silver labels -- this measures how much the panel actually agrees, independent of
     which items became fixtures.
  7. MDE (eval/power_analysis.py) for the resulting eval-set size, overall and per
     language.
  8. Writes eval/results/agreement_latest.json (the canonical metrics artifact
     scripts/render_metrics.py's marker-block mechanism reads from) and prints a
     console summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.agreement import fleiss_kappa, krippendorff_alpha  # noqa: E402
from eval.consensus import calibration, panel  # noqa: E402
from eval.consensus.build_report import (  # noqa: E402
    build_new_fixture,
    fleiss_table,
    passes_growth_gate,
    raw_votes_matrix,
    validation_agreement,
)
from eval.consensus.candidates import (  # noqa: E402
    FLIPKART_CANDIDATES_PATH,
    existing_text_keys,
    load_existing_fixtures,
    load_jsonl,
    select_growth_candidates,
)
from eval.consensus.voting import consensus_for_item  # noqa: E402
from eval.power_analysis import minimum_detectable_effect  # noqa: E402

RESULTS_DIR = ROOT / "eval" / "consensus" / "results"
CONSENSUS_LABELS_PATH = RESULTS_DIR / "consensus_labels.jsonl"
CALIBRATION_REPORT_PATH = RESULTS_DIR / "calibration_report.json"
AGREEMENT_LATEST_PATH = ROOT / "eval" / "results" / "agreement_latest.json"
CONSENSUS_SUMMARY_PATH = RESULTS_DIR / "consensus_summary.json"

EXTRACTION_RESULTS_PATH = ROOT / "eval" / "results" / "latest.json"

DELAY_SECONDS = 2.0  # courtesy pacing on the dedicated benchmark key's own budget

# Fields Krippendorff's alpha / Fleiss' kappa are computed over -- closed category
# sets only, see eval/agreement.py's docstring for why open-list fields are excluded.
NOMINAL_FIELDS: tuple[str, ...] = ("sentiment", "buy_again", "language")
ORDINAL_FIELDS: dict[str, list[Any]] = {
    "urgency": ["low", "medium", "high"],
    "stars_inferred": [1, 2, 3, 4, 5],
}


def get_active_panel() -> list[dict[str, str]]:
    """Load (or run fresh) calibration; return the JUDGE_MODELS configs that passed."""
    if CALIBRATION_REPORT_PATH.exists():
        report = json.loads(CALIBRATION_REPORT_PATH.read_text(encoding="utf-8"))
        print(f"Using existing calibration report: {CALIBRATION_REPORT_PATH}")
    else:
        report = asyncio.run(calibration.run_calibration())
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        CALIBRATION_REPORT_PATH.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    active_ids = set(report["active_panel"])
    active = [m for m in panel.JUDGE_MODELS if m["id"] in active_ids]
    if report["dropped"]:
        for d in report["dropped"]:
            print(f"  DROPPED (calibration): {d['model_id']} -- {d['reason']}")
    print(f"Active panel: {[m['id'] for m in active]}")
    return active


async def _label_one_judge(
    client: Any, model_cfg: dict[str, str], text: str
) -> dict[str, Any] | None:
    try:
        raw = await panel.call_judge(client, model_cfg["id"], text)
    except Exception as exc:  # noqa: BLE001 -- record the error, don't crash the whole run
        print(f"    ERROR [{model_cfg['id']}]: {str(exc)[:150]}")
        return None
    parsed = panel.parse_judge_response(raw)
    return parsed.model_dump() if parsed else None


async def label_all(items: list[dict[str, Any]], active_panel: list[dict[str, str]]) -> None:
    """Label every item in `items` with every judge in `active_panel`; stream results to disk."""
    client = panel.make_groq_client()

    existing_ids: set[str] = set()
    if CONSENSUS_LABELS_PATH.exists():
        for line in CONSENSUS_LABELS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing_ids.add(json.loads(line)["id"])

    pending = [item for item in items if item["id"] not in existing_ids]
    print(f"Items: {len(items)}  Already labeled: {len(existing_ids)}  Pending: {len(pending)}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with CONSENSUS_LABELS_PATH.open("a", encoding="utf-8") as fh:
        for i, item in enumerate(pending, 1):
            t0 = time.monotonic()
            outputs = await asyncio.gather(
                *[_label_one_judge(client, m, item["text"]) for m in active_panel]
            )
            judge_outputs = {m["id"]: out for m, out in zip(active_panel, outputs, strict=True)}
            consensus = consensus_for_item(judge_outputs)
            latency_ms = int((time.monotonic() - t0) * 1000)

            record = {
                "id": item["id"],
                "mode": item["mode"],
                "language_hint": item.get("language_hint"),
                "text": item["text"],
                "source": item.get("source", ""),
                "judge_outputs": judge_outputs,
                "consensus": consensus,
                "latency_ms": latency_ms,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()

            summary = "  ".join(
                f"{f}={consensus[f]['silver']}({consensus[f]['agreement']})"
                for f in ("sentiment", "urgency", "buy_again", "language")
            )
            print(f"  [{i}/{len(pending)}] {item['id']}: {summary}  {latency_ms}ms")
            await asyncio.sleep(DELAY_SECONDS)


def build_item_list(
    max_new_en: int, max_new_hien: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the full labeling item list: existing fixtures (validate) + growth candidates.

    Excludes fixtures this SAME pipeline already wrote (`is_consensus_grown`) from the
    "validate" set -- otherwise a rerun after a crash (or any rerun once growth fixtures
    exist on disk) would re-label its own prior output against itself, a circular
    "validation" that burns live API calls for zero new information (a real incident,
    not hypothetical -- see candidates.py's `is_consensus_grown` comment). Still included
    in `existing_text_keys()` for de-dup, so growth never re-proposes the same text twice.
    """
    all_existing = load_existing_fixtures()
    existing = [fx for fx in all_existing if not fx["is_consensus_grown"]]
    items: list[dict[str, Any]] = [
        {
            "id": fx["id"],
            "mode": "validate",
            "language_hint": fx["language"],
            "text": fx["review_text"],
            "source": fx["path"],
        }
        for fx in existing
    ]

    all_candidates = load_jsonl(FLIPKART_CANDIDATES_PATH)
    used = existing_text_keys()

    en_growth = select_growth_candidates(
        all_candidates, used, "en", char_range=(60, 500), max_count=max_new_en
    )
    hien_growth = select_growth_candidates(
        all_candidates, used, "hi-en", char_range=(30, 600), max_count=max_new_hien
    )
    hi_growth = select_growth_candidates(
        all_candidates, used, "hi", char_range=(20, 600), max_count=1000
    )
    print(
        f"Growth candidates available: en={len(en_growth)} (capped {max_new_en}), "
        f"hi-en={len(hien_growth)} (capped {max_new_hien}), hi={len(hi_growth)} "
        "(real corpus yield -- see eval/data/README.md's documented low-yield caveat)"
    )

    for c in en_growth + hien_growth:
        items.append(
            {
                "id": c["consensus_id"],
                "mode": "grow",
                "language_hint": c["language"],
                "text": c["text"],
                "source": c["source"],
            }
        )

    return items, existing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-new-en", type=int, default=200)
    parser.add_argument("--max-new-hien", type=int, default=100)
    args = parser.parse_args()

    active_panel = get_active_panel()
    if len(active_panel) < 2:
        print(
            f"FATAL: only {len(active_panel)} judge(s) passed calibration -- need at least 2 "
            "for meaningful consensus voting. Stopping rather than proceeding with 1 judge "
            "producing fake 'unanimous' labels."
        )
        sys.exit(1)

    items, existing_fixtures = build_item_list(args.max_new_en, args.max_new_hien)
    asyncio.run(label_all(items, active_panel))

    # --- Load all results back for reporting ---
    all_records = [
        json.loads(line)
        for line in CONSENSUS_LABELS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Recompute consensus FRESH from the raw judge_outputs rather than trusting the
    # cached "consensus" field written at labeling time -- judge_outputs is the true
    # raw data; consensus is a derived value that must never go stale relative to the
    # current voting logic. This makes a voting-logic bug fix (e.g. the stars_inferred
    # int-rounding fix) apply retroactively on a report-only rerun, with no new live
    # API calls, instead of requiring a full expensive relabel.
    for rec in all_records:
        rec["consensus"] = consensus_for_item(rec["judge_outputs"])
    by_id = {r["id"]: r for r in all_records}
    judge_ids = [m["id"] for m in active_panel]

    # --- Validation pass ---
    validation = validation_agreement(existing_fixtures, by_id)

    # --- Growth: write new fixtures ---
    growth_records = [r for r in all_records if r["mode"] == "grow"]
    new_fixtures_written: dict[str, int] = {"en": 0, "hi-en": 0}
    split_excluded = 0

    en_next_idx = 29  # existing top-level fixtures run 001..028
    hien_next_idx = 16  # existing eval/fixtures/hi-en/ runs 001..015

    for rec in growth_records:
        if not passes_growth_gate(rec["consensus"]):
            split_excluded += 1
            continue
        lang = rec["consensus"]["language"]["silver"]
        if lang == "en":
            fixture_id = f"{en_next_idx:03d}_consensus_grown"
            out_path = ROOT / "eval" / "fixtures" / f"{fixture_id}.json"
            en_next_idx += 1
        elif lang == "hi-en":
            fixture_id = f"{hien_next_idx:03d}"
            out_path = ROOT / "eval" / "fixtures" / "hi-en" / f"{fixture_id}.json"
            hien_next_idx += 1
        else:
            continue  # consensus disagreed on language in a way that doesn't map cleanly
        fixture = build_new_fixture(fixture_id, rec["text"], rec["consensus"], rec["source"])
        out_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
        new_fixtures_written[lang if lang in new_fixtures_written else "en"] += 1

    # --- Reliability stats across ALL labeled items (existing + growth) ---
    alpha_report: dict[str, Any] = {}
    kappa_report: dict[str, Any] = {}
    for field in NOMINAL_FIELDS:
        matrix = raw_votes_matrix(all_records, field, judge_ids)
        alpha_report[field] = {"level": "nominal", "alpha": krippendorff_alpha(matrix, "nominal")}
        table = fleiss_table(all_records, field, judge_ids)
        kappa_report[field] = {
            "n_items": len(table),
            "kappa": fleiss_kappa(table) if table else None,
        }

    for field, categories in ORDINAL_FIELDS.items():
        matrix = raw_votes_matrix(all_records, field, judge_ids)
        alpha_report[field] = {
            "level": "ordinal",
            "alpha": krippendorff_alpha(matrix, "ordinal", categories=categories),
        }

    # --- Per-language final counts (existing + newly written) ---
    lang_counts: dict[str, int] = {}
    for fx in existing_fixtures:
        lang_counts[fx["language"]] = lang_counts.get(fx["language"], 0) + 1
    lang_counts["en"] = lang_counts.get("en", 0) + new_fixtures_written.get("en", 0)
    lang_counts["hi-en"] = lang_counts.get("hi-en", 0) + new_fixtures_written.get("hi-en", 0)

    # --- MDE (overall + per language) ---
    overall_n = len(existing_fixtures) + sum(new_fixtures_written.values())
    base_rate = 0.5
    if EXTRACTION_RESULTS_PATH.exists():
        base_rate = json.loads(EXTRACTION_RESULTS_PATH.read_text(encoding="utf-8"))["overall_score"]
    mde_report = {
        "overall_n": overall_n,
        "mde_worst_case_p_0.5": minimum_detectable_effect(overall_n, p=0.5),
        f"mde_at_current_score_p_{base_rate:.3f}": minimum_detectable_effect(
            overall_n, p=base_rate
        ),
        "per_language_n": lang_counts,
        "per_language_mde_worst_case_p_0.5": {
            lang: minimum_detectable_effect(n, p=0.5) for lang, n in lang_counts.items() if n > 1
        },
    }

    # --- Calibration/panel metadata (for the README render block) ---
    calib_report = json.loads(CALIBRATION_REPORT_PATH.read_text(encoding="utf-8"))
    panel_by_id = {m["id"]: m for m in panel.JUDGE_MODELS}
    active_panel_meta = [
        {"id": jid, "family": panel_by_id[jid]["family"], "owner": panel_by_id[jid]["owner"]}
        for jid in judge_ids
    ]

    summary = {
        "active_panel": active_panel_meta,
        "dropped_judges": calib_report["dropped"],
        "validation": validation,
        "growth": {
            "candidates_considered": len(growth_records),
            "new_fixtures_written": new_fixtures_written,
            "excluded_split_or_insufficient": split_excluded,
        },
        "reliability": {"krippendorff_alpha": alpha_report, "fleiss_kappa": kappa_report},
        "mde": mde_report,
        "final_eval_set_size": overall_n,
        "final_per_language_counts": lang_counts,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CONSENSUS_SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    AGREEMENT_LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGREEMENT_LATEST_PATH.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n" + "=" * 60)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWritten: {CONSENSUS_SUMMARY_PATH}")
    print(f"Written: {AGREEMENT_LATEST_PATH}")


if __name__ == "__main__":
    main()
