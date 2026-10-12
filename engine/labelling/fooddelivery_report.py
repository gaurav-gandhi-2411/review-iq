"""Generated report for the out-of-domain food-delivery auxiliary labels (S23). No review text in the output.

    python -m engine.labelling.fooddelivery_report --manifest reports/labelling/fooddelivery/manifest.json \
        --judges <judges_A> <judges_B> --texts <private fd_items.jsonl> --yield reports/labelling/fooddelivery/yield.json \
        --source reports/labelling/fooddelivery/source_metadata.json --out-dir reports/fooddelivery_aux

Every number in report.md is rendered from report.json, which is computed here from the judge files: no hand-typed
metrics. Two-judge first-pass agreement (llama3.1 vs qwen3) is Krippendorff alpha / raw agreement; the 'clear
consensus' gate of section 5 needs three raters and is reported on the mistral-tiebreak subset only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from engine.labelling import agreement as A
from engine.labelling import schema as S
from engine.labelling import two_judge as TJ
from engine.labelling.fooddelivery_aux import FOOD_SPECIFIC_RE, tags
from engine.labelling.prompts_v3 import BROAD_INTENTS
from engine.labelling.repilot2_report import wilson
from engine.labelling.scale_report import MIN_POSITIVES, _nan_to_none, load_items

LEVELS = {
    "needs_action": list(S.YES_NO),
    "broad_intent": list(BROAD_INTENTS),
    "sentiment": list(S.SENTIMENTS),
    "urgency": list(S.URGENCY),
}
PANEL = ("llama31_8b", "qwen3_8b", "mistral_7b")


def _v(rec: dict | None, task: str):  # noqa: ANN202
    t = rec.get("text") if rec else None
    return t.get(task) if t else A.MISSING


def build_report(manifest: dict, items: dict, texts: dict[str, str], yield_rep: dict) -> dict:
    rows = manifest["items"]
    ids = [r["id"] for r in rows]
    pool_of = {r["id"]: r["pool"] for r in rows}
    labelled = {i: TJ.label_item(*(items[i].get(j) for j in PANEL)) for i in ids}
    rep: dict = {"domain": manifest["domain"], "split": manifest["split"], "n_items": len(ids)}
    rep["first_pass_agreement_llama_vs_qwen"] = {
        t: A.task_report(
            [[_v(items[i].get(PANEL[0]), t), _v(items[i].get(PANEL[1]), t)] for i in ids], lv
        )
        for t, lv in LEVELS.items()
    }
    sub = [i for i in ids if items[i].get(PANEL[2])]
    rep["mistral_tiebreak"] = {
        "ran_on": len(sub),
        "fraction": round(len(sub) / len(ids), 4),
        "planning_assumption": 0.30,
        "three_judge_agreement_on_subset": {
            t: A.task_report([[_v(items[i].get(j), t) for j in PANEL] for i in sub], lv)
            for t, lv in LEVELS.items()
        },
    }
    rep["label_provenance"] = {
        t: dict(Counter(labelled[i][t]["how"] for i in ids)) for t in TJ.TASKS
    }
    bi = [labelled[i]["broad_intent"]["label"] for i in ids]
    n_lab = sum(x is not None for x in bi)
    rep["broad_intent"] = {
        "n_labelled": n_lab,
        "abstained": len(ids) - n_lab,
        "labelled_fraction_wilson95": wilson(n_lab, len(ids)),
        "classes": {
            k: {
                "n": bi.count(k),
                "by_pool": {
                    p: sum(1 for i, x in zip(ids, bi, strict=True) if x == k and pool_of[i] == p)
                    for p in ("regex_enriched", "random")
                },
                "insufficient_data_at_sealed_threshold": bi.count(k) < MIN_POSITIVES,
            }
            for k in BROAD_INTENTS
        },
    }
    for t in ("sentiment", "urgency", "needs_action"):
        c = Counter(labelled[i][t]["label"] for i in ids)
        rep[f"{t}_labels"] = {str(k): v for k, v in sorted(c.items(), key=lambda kv: str(kv[0]))}
    # How much of delivery/service is food-app specific vs transferable (lexical split; a crude proxy).
    split: dict = {}
    for k in ("delivery", "service"):
        pos = [i for i, x in zip(ids, bi, strict=True) if x == k]
        spec = [i for i in pos if FOOD_SPECIFIC_RE.search(texts[i])]
        split[k] = {
            "n": len(pos),
            "food_app_specific_lexical": len(spec),
            "transferable_lexical": len(pos) - len(spec),
            "share_food_app_specific": round(len(spec) / len(pos), 4) if pos else None,
            "note": "lexical proxy (FOOD_SPECIFIC_RE: dasher/driver/restaurant/food/menu/tip/app/order...); not a judged label",
        }
    rep["delivery_service_transfer_proxy"] = split
    rep["regex_vs_label"] = {
        k: {
            "label_in_regex_enriched": sum(1 for i, x in zip(ids, bi, strict=True) if x == k and pool_of[i] == "regex_enriched"),
            "strict_regex_precision": round(
                sum(1 for i, x in zip(ids, bi, strict=True) if x == k and tags(texts[i], strict=True)[k])
                / max(1, sum(1 for i in ids if tags(texts[i], strict=True)[k])), 4),
        }
        for k in ("delivery", "service")
    }  # fmt: skip
    rep["yield_estimate_ref"] = {
        k: yield_rep[k]
        for k in (
            "seed",
            "pool_after_clean_len_english_dedupe",
            "sample_regex_prevalence",
            "strict_enrichment_regex_prevalence",
        )
    }
    return rep


def render_md(rep: dict, source: dict) -> str:
    lic = source["data_licence"]
    L = [
        "# Out-of-domain food-delivery auxiliary labels (generated)",
        "",
        "Generated by `engine/labelling/fooddelivery_report.py` from the judge files; do not edit by hand.",
        f"domain=`{rep['domain']}`, split=`{rep['split']}` (training-only; never validation or sealed), n={rep['n_items']}.",
        "Evaluation of `delivery` and `service` stays 'insufficient data': this set is out-of-domain and is not evidence of",
        "merchant-review performance.",
        "",
        "## Consensus broad_intent",
        "",
        "| class | n | regex_enriched | random | below sealed threshold (20) |",
        "|---|---|---|---|---|",
    ]
    for k, v in rep["broad_intent"]["classes"].items():
        L.append(
            f"| {k} | {v['n']} | {v['by_pool']['regex_enriched']} | {v['by_pool']['random']} | {v['insufficient_data_at_sealed_threshold']} |"
        )
    bi = rep["broad_intent"]
    L += ["", f"Labelled {bi['n_labelled']} of {rep['n_items']}; abstained {bi['abstained']}.", "",
          "## First-pass agreement (llama3.1 vs qwen3, all items)", "",
          "| task | alpha | raw agreement | n |", "|---|---|---|---|"]  # fmt: skip
    for t, v in rep["first_pass_agreement_llama_vs_qwen"].items():
        L.append(f"| {t} | {v['alpha']} | {v['pairwise_agreement']} | {v['n_units']} |")
    mt = rep["mistral_tiebreak"]
    L += ["", f"Mistral tiebreak ran on {mt['ran_on']} items ({mt['fraction']}; planning assumption {mt['planning_assumption']}).",
          "", "Three-judge agreement on that subset (clear consensus needs three raters):", "",
          "| task | alpha | clear consensus | unanimous | n |", "|---|---|---|---|---|"]  # fmt: skip
    for t, v in mt["three_judge_agreement_on_subset"].items():
        L.append(
            f"| {t} | {v['alpha']} | {v.get('clear_consensus')} | {v.get('unanimous')} | {v['n_units']} |"
        )
    L += ["", "## delivery / service: food-app specific vs transferable (lexical proxy)", "",
          "| class | n | food-app specific | transferable | share specific |", "|---|---|---|---|---|"]  # fmt: skip
    for k, v in rep["delivery_service_transfer_proxy"].items():
        L.append(
            f"| {k} | {v['n']} | {v['food_app_specific_lexical']} | {v['transferable_lexical']} | {v['share_food_app_specific']} |"
        )
    L += ["", "## Source and licence", "",
          f"{source['name']}, doi:{source['doi']['id']}, version {source['version']}.",
          f"Licence ({lic['short_name']}, {lic['full_name']}, {lic['url']}), verbatim from the Mendeley Data API record:", "",
          f"> {lic['description']}", "",
          "Attribution (CC BY 4.0): Hossain, M.S., Li, L., Sun, P., Law, K.A., Ray, S. (2025) 'Food Delivery Apps (DoorDash, "
          "Grubhub & UberEats) Reviews', Mendeley Data, V1, doi:10.17632/m5jk7wzyg7.1, licensed CC BY 4.0. "
          "Changes: cleaned, deduplicated, subsampled and relabelled by an LLM judge pipeline; no review text is redistributed here.", "",
          "Unresolved: the reviews were scraped from Google Play; the dataset licence does not cure Google Play's terms of "
          "service for scraped content, and the record itself notes that further permission may be required for third-party content. "
          "Review text is kept in private storage and is not committed."]  # fmt: skip
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--judges", nargs="+", required=True)
    ap.add_argument("--texts", required=True)
    ap.add_argument("--yield", dest="yield_path", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    texts = {
        r["id"]: r["text"]
        for r in map(json.loads, Path(a.texts).read_text(encoding="utf-8").splitlines())
    }
    rep = _nan_to_none(
        build_report(
            man,
            load_items([Path(d) for d in a.judges]),
            texts,
            json.loads(Path(a.yield_path).read_text(encoding="utf-8")),
        )
    )
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(rep, indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )
    src = json.loads(Path(a.source).read_text(encoding="utf-8"))
    (out / "report.md").write_text(render_md(rep, src), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
