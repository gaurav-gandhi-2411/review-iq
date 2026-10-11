"""Z2b: false-positive cost of the input STRIP (app/core/injection_controls.controlled_input).

PR #221 measured how often the input DETECTOR flags real reviews (0/106 held-out, 0/42 dev) but never
applied the strip or looked at what it removes. This script runs the production input path exactly
as the extraction endpoints do (app/api/v2/extract.py): controlled_input(raw) with the input flag
ON -> detect_language(ctl.text) -> sanitize(ctl.text) -> wrap_for_llm -> build_prompt, and compares
it with the flag-OFF path (sanitize(raw)). Zero LLM calls, zero quota, no network, deterministic
(no randomness is used; "seed 42" is therefore not applicable).

PRE-REGISTERED DECISION THRESHOLD (written before the first run of this script):
  "near zero" (NEAR_ZERO) requires ALL of:
    1. 0 affected reviews among the first-party set (held-out 106 + dev, attack fixtures excluded);
       the rule-of-three 95% upper bound (3/n) is reported, expected about 2% for n~148;
    2. on the large real-review corpus (245,757 Flipkart, if supplied) an affected rate <= 1e-4
       (<= 25 reviews) and no emptied review; the Wilson/rule-of-three upper bound is reported;
    3. every removed sentence is printed for a human to read (stdout) before any enable decision.
  Anything else is NOT_NEAR_ZERO and the runbook procedure is written as conditional.

"Affected" = the controlled text differs from the raw text because a sentence was removed
(ControlledInput.stripped). "Changed prompt" = the final user prompt differs from the flag-off one.

Licence rule: review text is read locally only. The committed JSON holds counts, ids, rule names,
lengths and a sha256 of each removed sentence, never review text. Removed text is printed to stdout
only with --print-removed.

Usage:
    python -m eval.measure_injection_strip_fp --large-corpus data/processed/flipkart_deduped.jsonl \
        --print-removed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HELD_OUT_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
DEV_DIR = ROOT / "eval" / "fixtures"
HELD_OUT_CASSETTES = ROOT / "eval" / "cassettes" / "held_out_cassettes.json"
DEV_CASSETTES = ROOT / "eval" / "cassettes" / "cassettes.json"
TOKEN_COST = ROOT / "eval" / "results" / "token_cost_measurement_n106.json"
OUT_PATH = ROOT / "eval" / "results" / "injection_strip_fp.json"
NEAR_ZERO_LARGE_RATE = 1e-4
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def rule_of_three(n: int) -> float:
    """95% upper bound on a rate when 0 events were seen in n trials."""
    return 3.0 / n if n else 1.0


def wilson_upper(k: int, n: int, z: float = 1.959964) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + half) / d)


def removed_sentences(raw: str, controlled: str, normalize_fn: Any, flags_fn: Any) -> list[str]:
    """The sentences the strip dropped, in order (same split as injection_controls)."""
    sents = _SENT_SPLIT.split(normalize_fn(raw).strip())
    return [s for s in sents if flags_fn(s)] if controlled != raw else []


def run_one(rid: str, raw: str, ic: Any, sanitize: Any, detect_language: Any) -> dict[str, Any]:
    """Run the endpoint input path for one review with the input control ON, vs OFF."""
    ctl = ic.controlled_input(raw)  # flag read from settings (set ON by main)
    lang_on = detect_language(ctl.text)
    clean_on, _ = sanitize(ctl.text)
    lang_off = detect_language(raw)
    clean_off, _ = sanitize(raw)
    removed = removed_sentences(raw, ctl.text, ic.normalize, ic.input_flags) if ctl.stripped else []
    return {
        "id": rid,
        "affected": ctl.stripped,
        "rules": list(ctl.rules),
        "sentences_removed": ctl.sentences_removed,
        "emptied": ctl.emptied,
        "chars_before": len(raw),
        "chars_after": len(ctl.text),
        "sanitized_differs": clean_on != clean_off,
        "language_changes": lang_on != lang_off,
        "language_off": lang_off,
        "language_on": lang_on,
        "_removed": removed,
        "_clean_on": clean_on,
        "_clean_off": clean_off,
    }


def load_first_party() -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, Any]]:
    """(held_out, dev, provenance). Dev = exactly the set scripts/measure_injection_control_options
    used: eval/fixtures/*.json + eval/fixtures/hi-en/*.json (runner's collector), minus attack
    fixtures whose id contains 'injection'."""
    held_paths = sorted(HELD_OUT_DIR.glob("hien-*.json"))
    held = [json.loads(p.read_text(encoding="utf-8")) for p in held_paths]
    dev_paths = sorted(p for p in DEV_DIR.glob("*.json") if not p.name.startswith("."))
    dev_paths += sorted(p for p in (DEV_DIR / "hi-en").glob("*.json") if not p.name.startswith("."))
    dev_all = [json.loads(p.read_text(encoding="utf-8")) for p in dev_paths]
    excluded = sorted(d["id"] for d in dev_all if "injection" in d["id"])
    dev = [d for d in dev_all if "injection" not in d["id"]]
    prov = {
        "held_out_files": len(held_paths),
        "dev_files_top_level": len([p for p in dev_paths if p.parent == DEV_DIR]),
        "dev_files_hi_en": len([p for p in dev_paths if p.parent != DEV_DIR]),
        "dev_excluded_attack_fixtures": excluded,
        "dev_counted": len(dev),
    }
    return (
        [(d["id"], d["review_text"]) for d in held],
        [(d["id"], d["review_text"]) for d in dev],
        prov,
    )


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--large-corpus", type=Path, default=None)
    ap.add_argument("--extra-jsonl", type=Path, action="append", default=[])
    ap.add_argument("--print-removed", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    os.environ["ENABLE_FIELD_INJECTION_INPUT_CONTROL"] = "true"
    os.environ["ENABLE_FIELD_INJECTION_OUTPUT_CHECK"] = "false"
    from app.core import injection_controls as ic
    from app.core.config import get_settings
    from app.core.language import detect_language
    from app.core.llm import _SYSTEM_PROMPT
    from app.core.prompts import build_prompt
    from app.core.providers.groq import _make_cassette_key
    from app.core.sanitize import sanitize, wrap_for_llm

    get_settings.cache_clear()
    settings = get_settings()
    assert settings.enable_field_injection_input_control is True  # noqa: S101

    held, dev, prov = load_first_party()
    held_cass = set(json.loads(HELD_OUT_CASSETTES.read_text(encoding="utf-8")))
    dev_cass = set(json.loads(DEV_CASSETTES.read_text(encoding="utf-8")))
    models = [settings.groq_model_small, settings.groq_model_large]

    def keys(clean: str, lang: str) -> list[str]:
        prompt = build_prompt(wrap_for_llm(clean), lang)
        return [_make_cassette_key(m, _SYSTEM_PROMPT, prompt) for m in models]

    result: dict[str, Any] = {}
    all_affected_first_party: list[dict[str, Any]] = []
    for name, items, cass in (("held_out", held, held_cass), ("dev", dev, dev_cass)):
        rows = [run_one(rid, raw, ic, sanitize, detect_language) for rid, raw in items]
        aff = [r for r in rows if r["affected"]]
        per_rule: dict[str, int] = dict.fromkeys(ic.RULES, 0)
        for r in aff:
            for rule in r["rules"]:
                per_rule[rule] += 1
        cassette = []
        for r in aff:
            k_off = keys(r["_clean_off"], r["language_off"])
            k_on = keys(r["_clean_on"], r["language_on"])
            cassette.append(
                {
                    "id": r["id"],
                    "baseline_key_in_cassette": any(k in cass for k in k_off),
                    "stripped_key_in_cassette": any(k in cass for k in k_on),
                    "prompt_changes": k_off != k_on,
                }
            )
        result[name] = {
            "n": len(rows),
            "n_affected": len(aff),
            "rule_of_three_upper_95": rule_of_three(len(rows)) if not aff else None,
            "wilson_upper_95": round(wilson_upper(len(aff), len(rows)), 6),
            "per_rule": per_rule,
            "n_emptied": sum(r["emptied"] for r in aff),
            "n_sanitized_differs": sum(r["sanitized_differs"] for r in rows),
            "n_language_changes_any_review": sum(r["language_changes"] for r in rows),
            "affected": [
                {k: v for k, v in r.items() if not k.startswith("_")}
                | {
                    "removed_sha256": [
                        hashlib.sha256(s.encode()).hexdigest()[:16] for s in r["_removed"]
                    ]
                }
                for r in aff
            ],
            "cassette_check": cassette,
        }
        all_affected_first_party += [{"corpus": name, **r} for r in aff]

    n_fp = len(held) + len(dev)
    k_fp = len(all_affected_first_party)
    result["first_party_combined"] = {
        "n": n_fp,
        "n_affected": k_fp,
        "rule_of_three_upper_95": rule_of_three(n_fp) if k_fp == 0 else None,
        "wilson_upper_95": round(wilson_upper(k_fp, n_fp), 6),
        "note": "held-out and dev are drawn from different sources; n is summed only because "
        "the question is 'any affected among every first-party review'.",
    }

    big: dict[str, Any] = {}
    sources: list[tuple[str, Path]] = []
    if args.large_corpus:
        sources.append(("flipkart_deduped_full", args.large_corpus))
    for p in args.extra_jsonl:
        sources.append((f"{p.parent.name}/{p.stem}", p))
    for label, path in sources:
        raw_bytes = path.read_bytes()
        n = 0
        aff_rows: list[dict[str, Any]] = []
        per_rule = dict.fromkeys(ic.RULES, 0)
        for i, line in enumerate(raw_bytes.decode("utf-8").splitlines()):
            if not line.strip():
                continue
            obj = json.loads(line)
            text = obj.get("text") or obj.get("review_text") or ""
            rid = str(obj.get("id") or f"{obj.get('source', 'row')}:{obj.get('source_row', i)}")
            n += 1
            ctl = ic.controlled_input(text)
            if not ctl.stripped:
                continue
            rem = removed_sentences(text, ctl.text, ic.normalize, ic.input_flags)
            for rule in ctl.rules:
                per_rule[rule] += 1
            aff_rows.append(
                {
                    "id": rid,
                    "rules": list(ctl.rules),
                    "sentences_removed": ctl.sentences_removed,
                    "emptied": ctl.emptied,
                    "chars_before": len(text),
                    "chars_after": len(ctl.text),
                    "removed_sha256": [hashlib.sha256(s.encode()).hexdigest()[:16] for s in rem],
                    "_removed": rem,
                }
            )
        big[label] = {
            "path_name": path.name,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "n": n,
            "n_affected": len(aff_rows),
            "rate": len(aff_rows) / n if n else None,
            "rule_of_three_upper_95": rule_of_three(n) if not aff_rows else None,
            "wilson_upper_95": wilson_upper(len(aff_rows), n),
            "per_rule": per_rule,
            "n_emptied": sum(r["emptied"] for r in aff_rows),
            "affected": [{k: v for k, v in r.items() if k != "_removed"} for r in aff_rows],
            "_rows": aff_rows,
        }
    result["additional_corpora"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "_rows"} for k, v in big.items()
    }

    # ---- re-extraction cost plan (not run) -------------------------------------------------
    tc = json.loads(TOKEN_COST.read_text(encoding="utf-8"))
    pt = tc["per_tier"]
    n_s, n_l = pt["small"]["n"], pt["large"]["n"]
    mean_tok = (
        n_s * pt["small"]["tokens_total"]["mean"] + n_l * pt["large"]["tokens_total"]["mean"]
    ) / (n_s + n_l)
    need_live = [
        c
        for r in ("held_out", "dev")
        for c in result[r]["cassette_check"]
        if not c["stripped_key_in_cassette"]
    ]
    result["re_extraction_plan_not_run"] = {
        "affected_reviews_needing_live_call": len(need_live),
        "tokens_per_call_measured_blended": round(mean_tok, 1),
        "source": "eval/results/token_cost_measurement_n106.json",
        "total_tokens_if_run": round(len(need_live) * mean_tok),
        "note": "Calls = affected first-party reviews whose stripped prompt has no cassette entry; "
        "each would also need the same-prompt noise floor (eval/results/noise_floor_baseline.json) "
        "to separate a strip effect from run-to-run variation.",
    }

    n_large_aff = sum(v["n_affected"] for v in big.values())
    large_ok = all(
        (v["rate"] or 0.0) <= NEAR_ZERO_LARGE_RATE and v["n_emptied"] == 0 for v in big.values()
    )
    verdict = "NEAR_ZERO" if (k_fp == 0 and large_ok) else "NOT_NEAR_ZERO"
    result["verdict"] = {
        "value": verdict,
        "first_party_affected": k_fp,
        "additional_corpora_affected": n_large_aff,
        "large_corpus_checked": bool(big),
        "threshold": "see module docstring: 0 affected in first-party, large rate <= 1e-4, no emptied",
    }

    rules_blob = json.dumps({k: v.pattern for k, v in ic.RULES.items()}, sort_keys=True)
    out = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git("rev-parse", "HEAD"),
        "script": "eval/measure_injection_strip_fp.py",
        "zero_quota": True,
        "rule_set": {
            "module": "app/core/injection_controls.py",
            "module_git_blob": _git("hash-object", "app/core/injection_controls.py"),
            "rules": sorted(ic.RULES),
            "rules_sha256": hashlib.sha256(rules_blob.encode()).hexdigest(),
            "introduced_in": "PR #221 (853e87f); no version constant exists in code",
        },
        "pipeline": "controlled_input(raw) -> detect_language -> sanitize -> wrap_for_llm -> "
        "build_prompt (as app/api/v2/extract.py::_run_extraction_v2)",
        "provenance": prov,
        **result,
    }
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ---- stdout: removed text for a human (never committed) ----------------------------------
    print(f"first-party: {k_fp}/{n_fp} affected; verdict={verdict}")
    for name, items in (("held_out", held), ("dev", dev)):
        by_id = dict(items)
        for r in result[name]["affected"]:
            print(
                f"[{name}] {r['id']} rules={r['rules']} chars {r['chars_before']}->"
                f"{r['chars_after']} emptied={r['emptied']}"
            )
            if args.print_removed:
                for s in removed_sentences(by_id[r["id"]], "x", ic.normalize, ic.input_flags):
                    print("    REMOVED:", s[:160].replace("\n", " "))
    for label, v in big.items():
        print(f"{label}: {v['n_affected']}/{v['n']} affected, per_rule={v['per_rule']}")
        if args.print_removed:
            for r in v["_rows"]:
                print(
                    f"  {r['id']} rules={r['rules']} {r['chars_before']}->{r['chars_after']}"
                    f" emptied={r['emptied']}"
                )
                for s in r["_removed"]:
                    print("      REMOVED:", s[:160].replace("\n", " "))


if __name__ == "__main__":
    main()
