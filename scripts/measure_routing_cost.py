"""S15c-S2: how much of the held-out headline is language MISROUTING rather than extraction error.

Zero quota, zero network, zero DB: reads the RECORDED predictions/field scores in
eval/results/held_out_scoring_v2.json (cassette-replay output, both conditions) plus the held-out
gold fixtures' review text, and re-derives everything deterministically. Nothing is re-extracted.

Conditions (from ADR 0021):
  as_deployed      detect_language() picks the prompt, exactly like /v2/extract and /demo/extract
  language_forced  the corpus label picks the prompt (what the CI-gate eval always did)

Headlines reported (GG has decided the vacuous constant `stars` field is excluded from both):
  ex_stars                 mean of the 9 non-constant fields (INCLUDES `language`)
  ex_stars_ex_language     mean of the 8 fields left after also dropping `language`
`language` is scored against the corpus label; under language_forced the prompt itself tells the
model `language: always "hi-en"`, so it scores 100% by echo, not by extraction skill. Including it
in the misrouting delta therefore inflates the effect; ex_stars_ex_language is the honest number.
(Session 15d D7: ex_stars_ex_language is now also the published README headline, produced by
eval/score_held_out_corpus_v2.py::summarize; this script's own bootstrap is paired across the two
conditions, so its single-condition CIs differ from that headline's by a few hundredths of a pp.)

All CIs are percentile bootstrap, 10,000 resamples, seed 42, NOT clamped to [0, 1]; paired (the
same fixture indices are resampled for both conditions). Subsets resample within the subset.

Also computes offline detector baselines (always-hi-en, always-en, the production detector, and
lexicon variants built ONLY from the production regexes) so S2c's claims about what a "better
detector" could reach rest on numbers, not belief. Those are diagnostics on the same 106 texts,
not tuned proposals.

Usage: uv run python scripts/measure_routing_cost.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.core.language import (  # noqa: E402
    _STRONG_HINGLISH,
    _WEAK_HINGLISH,
    _get_lingua_detector,
    detect_language,
)
from eval.provenance import get_git_sha, now_iso  # noqa: E402
from eval.wilson import wilson_ci  # noqa: E402

HELD_OUT_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
PREDICTIONS = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
OUT_PATH = ROOT / "eval" / "results" / "routing_cost_n106.json"
N_RESAMPLES = 10_000
SEED = 42  # rule 40; matches eval/measure_scorer_delta.py and eval/bootstrap.py
MIN_N_FOR_CI = 10  # below this a percentile bootstrap CI is not informative; flagged in output


def _ci(samples: list[float]) -> list[float]:
    s = sorted(samples)
    return [s[int(0.025 * len(s))], s[int(0.975 * len(s)) - 1]]


def boot_mean_ci(values: list[float]) -> list[float]:
    """Percentile-bootstrap 95% CI of the mean (unclamped)."""
    rng = random.Random(SEED)  # noqa: S311 -- statistical resampling, not security
    n = len(values)
    return _ci([mean(rng.choices(values, k=n)) for _ in range(N_RESAMPLES)])


def boot_fraction_ci(deltas: list[float], as_deployed: list[float]) -> list[float]:
    """CI of mean(delta) / (1 - mean(as_deployed)): share of the gap-to-100% that is misrouting.

    Paired: one set of resampled indices feeds both numerator and denominator.
    """
    rng = random.Random(SEED)  # noqa: S311
    n = len(deltas)
    idx_range = range(n)
    out = []
    for _ in range(N_RESAMPLES):
        idx = rng.choices(idx_range, k=n)
        d = sum(deltas[i] for i in idx) / n
        a = sum(as_deployed[i] for i in idx) / n
        out.append(d / (1.0 - a))
    return _ci(out)


def _stat(values: list[float]) -> dict[str, Any]:
    n = len(values)
    return {
        "n": n,
        "mean": mean(values),
        "ci95": boot_mean_ci(values) if n >= 2 else None,
        "ci_informative": n >= MIN_N_FOR_CI,
        "n_up": sum(1 for v in values if v > 0),
        "n_down": sum(1 for v in values if v < 0),
        "n_zero": sum(1 for v in values if v == 0),
    }


def _headline(recs: list[dict[str, Any]], cond: str, fields: list[str]) -> list[float]:
    return [mean(r[cond]["field_scores"][f] for f in fields) for r in recs]


def _subset_block(
    recs: list[dict[str, Any]], all_recs_n: int, fields: dict[str, list[str]]
) -> dict[str, Any]:
    """as/forced/delta stats for one subset of fixtures, for each headline definition."""
    block: dict[str, Any] = {"n": len(recs), "headlines": {}}
    for name, flds in fields.items():
        a = _headline(recs, "as_deployed", flds)
        f = _headline(recs, "language_forced", flds)
        d = [y - x for x, y in zip(a, f, strict=True)]
        block["headlines"][name] = {
            "as_deployed_mean": mean(a) if a else None,
            "language_forced_mean": mean(f) if f else None,
            "delta": _stat(d) if d else None,
            # How much of the WHOLE-corpus (n=106) delta this subset supplies: sum(d)/106.
            "contribution_to_corpus_delta": sum(d) / all_recs_n,
        }
    return block


def _strong(t: str) -> bool:
    return bool(_STRONG_HINGLISH.search(t))


def _n_weak(t: str) -> int:
    return len(_WEAK_HINGLISH.findall(t))


def _route(label: str) -> str:
    # build_prompt sends everything except hi / hi-en to the en prompt.
    return label if label in ("hi", "hi-en") else "en"


# Every variant is built ONLY from the production regexes (no new vocabulary, no fitted threshold).
RULES = {
    "always_hi_en": lambda t: "hi-en",
    "always_en": lambda t: "en",
    "production_detect_language": lambda t: _route(detect_language(t)),
    "strong_regex_only": lambda t: "hi-en" if _strong(t) else "en",
    "strong_or_any_weak_marker": lambda t: "hi-en" if _strong(t) or _n_weak(t) >= 1 else "en",
    "strong_or_2plus_weak_markers": lambda t: "hi-en" if _strong(t) or _n_weak(t) >= 2 else "en",
}


def _external_controls() -> dict[str, Any]:
    """Real-English false-positive check the 106 cannot give (it holds only 5 en fixtures).

    Two OFFLINE labelled sets that are NOT the held-out corpus:
      benchmark_gold  benchmark/dataset/gold.jsonl (real Flipkart text, `slice` en / hi-en / hi)
      ci_gate_en      eval/fixtures/*.json with ground_truth.language == "en" (hand-built)
    """
    sets: dict[str, list[tuple[str, str]]] = {"benchmark_gold": [], "ci_gate_en": []}
    gold = ROOT / "benchmark" / "dataset" / "gold.jsonl"
    for line in gold.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            sets["benchmark_gold"].append((row["text"], row["slice"]))
    for p in sorted((ROOT / "eval" / "fixtures").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("ground_truth", {}).get("language") == "en":
            sets["ci_gate_en"].append((d["review_text"], "en"))
    out: dict[str, Any] = {}
    for set_name, rows in sets.items():
        counts = {s: sum(1 for _, x in rows if x == s) for s in sorted({x for _, x in rows})}
        block: dict[str, Any] = {"slice_counts": counts}
        for name, fn in RULES.items():
            entry: dict[str, Any] = {}
            for label in ("en", "hi-en"):
                sub = [t for t, x in rows if x == label]
                if not sub:
                    continue
                k = sum(1 for t in sub if fn(t) == label)
                p_hat = k / len(sub)
                entry[f"recall_{label}"] = {
                    "k": k,
                    "n": len(sub),
                    "rate": p_hat,
                    "wilson95": list(wilson_ci(p_hat, len(sub))),
                }
            block[name] = entry
        out[set_name] = block
    return out


def _detector_baselines(recs: list[dict[str, Any]], texts: dict[str, str]) -> dict[str, Any]:
    ids = [r["id"] for r in recs]
    gt = [r["gt_language"] for r in recs]
    lingua = _get_lingua_detector()
    from lingua import Language  # noqa: PLC0415 -- optional dep, same lazy import as language.py

    strong, n_weak, rules = _strong, _n_weak, RULES
    out: dict[str, Any] = {}
    for name, fn in rules.items():
        pred = [fn(texts[i]) for i in ids]
        hit = [p == g for p, g in zip(pred, gt, strict=True)]
        n_hien = sum(1 for g in gt if g == "hi-en")
        n_en = sum(1 for g in gt if g == "en")
        rec_hien = sum(1 for p, g in zip(pred, gt, strict=True) if g == "hi-en" and p == "hi-en")
        rec_en = sum(1 for p, g in zip(pred, gt, strict=True) if g == "en" and p == "en")
        # Session 15d: these are AGREEMENT with the (noisy, alpha 0.380) corpus label, not
        # accuracy -- keys renamed accordingly; an always-hi-en "score" here is a base-rate artifact.
        acc = sum(hit) / len(hit)
        out[name] = {
            "agreement_with_corpus_label": acc,
            "agreement_wilson95": list(wilson_ci(acc, len(hit))),
            "n_correct": sum(hit),
            "recall_hi_en": rec_hien / n_hien,
            "recall_hi_en_n": n_hien,
            "recall_en": rec_en / n_en,
            "recall_en_n": n_en,
            "n_pred_hi_en": sum(1 for p in pred if p == "hi-en"),
        }

    # detect_language() step 4 ("English confidence < 0.5 -> other") uses a lingua model built from
    # ONLY {English, Hindi}. Report the observed confidence range on all 106 Latin-script texts:
    # if it is constant, that step cannot discriminate anything on this corpus.
    if lingua is not None:
        conf = [lingua.compute_language_confidence(texts[i], Language.ENGLISH) for i in ids]
        out["lingua_english_confidence"] = {
            "min": min(conf),
            "max": max(conf),
            "n_below_0_5": sum(1 for c in conf if c < 0.5),
            "note": "if min == max == 1.0 the lingua step is a no-op for Roman-script text",
        }

    # Are the missed hi-en fixtures shorter / marker-free? (mechanism, not a claim about traffic)
    det = {r["id"]: r["detected_language"] for r in recs}
    missed = [i for i, g in zip(ids, gt, strict=True) if g == "hi-en" and det[i] != "hi-en"]
    caught = [i for i, g in zip(ids, gt, strict=True) if g == "hi-en" and det[i] == "hi-en"]
    out["missed_vs_caught_hi_en"] = {
        "n_missed": len(missed),
        "n_caught": len(caught),
        "median_chars_missed": median(len(texts[i]) for i in missed),
        "median_chars_caught": median(len(texts[i]) for i in caught),
        "missed_with_any_weak_marker": sum(1 for i in missed if n_weak(texts[i]) >= 1),
        "missed_with_no_strong_marker": sum(1 for i in missed if not strong(texts[i])),
    }
    return out


def main() -> int:
    payload = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    recs: list[dict[str, Any]] = payload["records"]
    n = len(recs)
    texts: dict[str, str] = {}
    for p in sorted(HELD_OUT_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if "review_text" in d:
            texts[d["id"]] = d["review_text"]
    assert set(texts) == {r["id"] for r in recs}, "fixtures and recorded predictions disagree"

    all_fields = list(recs[0]["as_deployed"]["field_scores"])
    constant = payload["constant_fields"]
    ex_stars = [f for f in all_fields if f not in constant]
    ex_stars_ex_lang = [f for f in ex_stars if f != "language"]
    heads = {"ex_stars": ex_stars, "ex_stars_ex_language": ex_stars_ex_lang}

    # ---- self-checks: this script must reproduce the recorded artifact's own numbers ----
    for cond in ("as_deployed", "language_forced"):
        rec_val = payload["overall_score_excluding_constant_fields"][cond]
        mine = mean(_headline(recs, cond, ex_stars))
        assert abs(mine - rec_val) < 1e-9, (cond, mine, rec_val)
    assert (
        sum(1 for r in recs if r["detected_language"] != r["gt_language"])
        == (payload["n_language_mismatched"])
    )
    # `language` under as_deployed is exactly "did the detector agree with the corpus label"
    # (the model echoes the prompt's label); under language_forced it is 100% by construction.
    for r in recs:
        agree = r["detected_language"] == r["gt_language"]
        assert r["as_deployed"]["field_scores"]["language"] == (1.0 if agree else 0.0), r["id"]
        assert r["language_forced"]["field_scores"]["language"] == 1.0, r["id"]

    mism = [r for r in recs if r["detected_language"] != r["gt_language"]]
    match = [r for r in recs if r["detected_language"] == r["gt_language"]]
    dir_a = [r for r in mism if r["gt_language"] == "hi-en" and r["detected_language"] == "en"]
    dir_b = [r for r in mism if r["gt_language"] == "en" and r["detected_language"] == "hi-en"]
    assert len(dir_a) + len(dir_b) == len(mism), "an unexpected mismatch direction exists"

    # ---- sanity check the task demands: matched fixtures must have delta EXACTLY 0 ----
    matched_max_abs_delta = 0.0
    for r in match:
        assert r["as_deployed"]["predicted"] == r["language_forced"]["predicted"], r["id"]
        assert not r.get("forced_call_made", False), r["id"]
        matched_max_abs_delta = max(
            matched_max_abs_delta,
            *(
                abs(r["language_forced"]["field_scores"][f] - r["as_deployed"]["field_scores"][f])
                for f in all_fields
            ),
        )
    assert matched_max_abs_delta == 0.0

    result: dict[str, Any] = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "predictions_source": str(PREDICTIONS.relative_to(ROOT)).replace("\\", "/"),
        "predictions_git_sha": payload.get("git_sha"),
        "scorer_version": payload.get("scorer_version"),
        "method": {
            "bootstrap": "percentile, paired, 10000 resamples, seed 42, unclamped",
            "headline_fields": heads,
            "constant_fields_excluded": constant,
            "n": n,
        },
        "sanity": {
            "reproduces_recorded_headline": True,
            "n_matched": len(match),
            "matched_all_predictions_identical_and_no_forced_call": True,
            "matched_max_abs_field_delta": matched_max_abs_delta,
            "language_field_as_deployed_equals_detector_agreement": True,
            "language_field_forced_is_100pct_by_echo": True,
        },
        "corpus": {
            "gt_language_counts": {
                g: sum(1 for r in recs if r["gt_language"] == g)
                for g in sorted({r["gt_language"] for r in recs})
            },
            "detected_language_counts": {
                g: sum(1 for r in recs if r["detected_language"] == g)
                for g in sorted({r["detected_language"] for r in recs})
            },
            "n_mismatched": len(mism),
            "n_mismatch_gt_hi_en_detected_en": len(dir_a),
            "n_mismatch_gt_en_detected_hi_en": len(dir_b),
            "detector_agreement_with_corpus_label": 1 - len(mism) / n,
            "detector_agreement_wilson95": list(wilson_ci(1 - len(mism) / n, n)),
        },
        "headline": {},
        "per_field": {},
        "by_mismatch_status": {},
        "detector_baselines": _detector_baselines(recs, texts),
        "detector_external_controls": _external_controls(),
    }

    # ---- whole-corpus headline, both definitions, with gap attribution ----
    for name, flds in heads.items():
        a = _headline(recs, "as_deployed", flds)
        f = _headline(recs, "language_forced", flds)
        d = [y - x for x, y in zip(a, f, strict=True)]
        gap = 1 - mean(a)
        result["headline"][name] = {
            "fields": flds,
            "as_deployed_mean": mean(a),
            "as_deployed_ci95": boot_mean_ci(a),
            "language_forced_mean": mean(f),
            "language_forced_ci95": boot_mean_ci(f),
            "paired_delta": _stat(d),
            "gap_to_100_as_deployed": gap,
            "gap_to_100_language_forced": 1 - mean(f),
            "share_of_gap_attributable_to_misrouting": mean(d) / gap,
            "share_of_gap_ci95": boot_fraction_ci(d, a),
            "share_of_gap_residual": 1 - mean(d) / gap,
        }

    # ---- per-field deltas (all 10 fields), whole corpus and the 55 mismatched ----
    for label, subset in (
        ("all_106", recs),
        ("mismatched_55", mism),
        ("gt_hi_en_det_en_53", dir_a),
    ):
        block: dict[str, Any] = {}
        for fld in all_fields:
            a = [r["as_deployed"]["field_scores"][fld] for r in subset]
            f = [r["language_forced"]["field_scores"][fld] for r in subset]
            d = [y - x for x, y in zip(a, f, strict=True)]
            st = _stat(d)
            block[fld] = {
                "as_deployed_mean": mean(a),
                "language_forced_mean": mean(f),
                "delta": st["mean"],
                "delta_ci95": st["ci95"],
                "n_up": st["n_up"],
                "n_down": st["n_down"],
                # Contribution to the 8-field headline delta: field delta / 8 (0 for the
                # excluded fields), so the per-field lines sum to the headline delta.
                "contribution_to_ex_stars_ex_language_headline": (
                    st["mean"] / len(ex_stars_ex_lang) if fld in ex_stars_ex_lang else 0.0
                ),
            }
        result["per_field"][label] = block

    # ---- split by mismatch status / direction ----
    for label, subset in (
        ("mismatched_55", mism),
        ("matched_51", match),
        ("gt_hi_en_detected_en", dir_a),
        ("gt_en_detected_hi_en", dir_b),
    ):
        result["by_mismatch_status"][label] = _subset_block(subset, n, heads)

    # ---- what recorded data says about "no routing" (always send the hi-en prompt) ----
    # The hi-en prompt's result is RECORDED for every fixture where it was actually sent: gt hi-en
    # (language_forced; equals as_deployed when the detector agreed) and the gt-en fixtures the
    # detector sent to hi-en (as_deployed). Only the 3 gt-en fixtures detected en lack it.
    hien_scores: dict[str, dict[str, float]] = {}
    for r in recs:
        if r["gt_language"] == "hi-en":
            hien_scores[r["id"]] = r["language_forced"]["field_scores"]
        elif r["detected_language"] == "hi-en":
            hien_scores[r["id"]] = r["as_deployed"]["field_scores"]
    covered = [r for r in recs if r["id"] in hien_scores]
    policy: dict[str, Any] = {
        "n_covered": len(covered),
        "n_not_recorded": n - len(covered),
        "not_recorded_are_gt_en_detected_en": all(
            r["gt_language"] == "en" and r["detected_language"] == "en"
            for r in recs
            if r["id"] not in hien_scores
        ),
        "headlines": {},
    }
    for name, flds in (("ex_stars_ex_language", ex_stars_ex_lang),):
        p_hien = [mean(hien_scores[r["id"]][f] for f in flds) for r in covered]
        p_as = _headline(covered, "as_deployed", flds)
        p_forced = _headline(covered, "language_forced", flds)
        d_vs_as = [x - y for x, y in zip(p_hien, p_as, strict=True)]
        d_vs_forced = [x - y for x, y in zip(p_hien, p_forced, strict=True)]
        policy["headlines"][name] = {
            "always_hi_en_mean": mean(p_hien),
            "as_deployed_mean_same_fixtures": mean(p_as),
            "language_forced_mean_same_fixtures": mean(p_forced),
            "always_hi_en_minus_as_deployed": _stat(d_vs_as),
            "always_hi_en_minus_language_forced": _stat(d_vs_forced),
        }
    result["policy_always_hi_en_prompt_recorded_subset"] = policy

    # ---- sensitivity: does the finding survive the OLD strict exact-match scorer (ADR 0021)? ----
    # Only the per-fixture strict overall over all 10 fields is recorded, not per-field strict
    # scores. `stars` and `language` are exact-match fields in both scorers (free-text scoring
    # only touches product/topics/known-gaps, ADR 0030), so their strict score == corrected score
    # and the strict 8-field mean can be recovered exactly: (10 * overall - stars - language) / 8.
    n_all = len(all_fields)
    strict8: dict[str, list[float]] = {}
    for cond in ("as_deployed", "language_forced"):
        strict8[cond] = [
            (
                n_all * r[cond]["overall_score_strict"]
                - r[cond]["field_scores"]["stars"]
                - r[cond]["field_scores"]["language"]
            )
            / (n_all - 2)
            for r in recs
        ]
    d_strict = [
        b - a for a, b in zip(strict8["as_deployed"], strict8["language_forced"], strict=True)
    ]
    result["sensitivity_strict_scorer_ex_stars_ex_language"] = {
        "as_deployed_mean": mean(strict8["as_deployed"]),
        "language_forced_mean": mean(strict8["language_forced"]),
        "paired_delta": _stat(d_strict),
        "derivation": "(10*overall_score_strict - stars - language)/8; assumes stars/language "
        "are exact-match under both scorers (BELIEVED per eval scoring_notes + ADR 0030)",
    }
    # Whole-corpus 10-field delta (stars + language included) = the ADR 0021 / ADR 0023 quantity.
    d10 = [r["language_forced"]["overall_score"] - r["as_deployed"]["overall_score"] for r in recs]
    result["adr_0021_quantity_all_10_fields"] = {
        "paired_delta": _stat(d10),
        "language_field_share_of_that_delta": (
            mean(
                r["language_forced"]["field_scores"]["language"]
                - r["as_deployed"]["field_scores"]["language"]
                for r in recs
            )
            / n_all
        )
        / mean(d10),
    }

    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for name in heads:
        h = result["headline"][name]
        pd = h["paired_delta"]
        print(
            f"[{name}] as_deployed {h['as_deployed_mean']:.4f}  forced {h['language_forced_mean']:.4f}"
            f"  delta {pd['mean']:+.4f} [{pd['ci95'][0]:+.4f}, {pd['ci95'][1]:+.4f}]"
            f"  misrouting share of gap {h['share_of_gap_attributable_to_misrouting']:.3f}"
            f" [{h['share_of_gap_ci95'][0]:.3f}, {h['share_of_gap_ci95'][1]:.3f}]"
        )
    print(f"Written: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
