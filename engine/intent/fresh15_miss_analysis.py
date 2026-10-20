"""S29 item 4: error analysis of the fresh-set needs_action=yes items that router R1 did not route (Amendment 15's 14 misses).

    PYTHONPATH=. python -m engine.intent.fresh15_miss_analysis --items D:/ml-cache/s28/fresh15.jsonl \
        --artifacts D:/ml-cache/s25/classify_artifacts_onnx --judges D:/ml-cache/s28/panel/out/judges \
        --out reports/intent/fresh15/miss_analysis.json

Descriptive only: nothing is tuned and no threshold or rule changes. Review text stays private (never written to the output); the
output carries ids, panel tallies, encoder probabilities, lengths and the reader's tags. The tags in `READ` are one reader's judgement
of the action signal (BELIEVED, not a panel label). After this analysis the 14 missed ids are development-exposed and must not be
counted in any later evaluation.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from app.core.classify import encoder_first as EF
from app.core.classify import runtime

from engine.intent.fresh15_eval import load_votes
from engine.labelling.pilot_diag import consensus_label
from engine.labelling.repilot2_report import wilson

MAX_TOKENS = 128  # the served model truncates here (head only)
BATCH = 100  # bounded CPU burst: this runs on a laptop

# One reader's tag per missed id (BELIEVED). signal = what a human would act on; frame = how the review presents itself.
READ: dict[str, dict[str, str]] = {
    "fresh15-apparel-0074": {
        "signal": "fit complaint (garment too small and short)",
        "frame": "negative",
    },
    "fresh15-apparel-0083": {
        "signal": "explicit return for size (runs big)",
        "frame": "positive, embedded problem",
    },
    "fresh15-apparel-0133": {
        "signal": "exchange for a defect (missing buttons)",
        "frame": "positive, embedded problem",
    },
    "fresh15-apparel-0145": {
        "signal": "defect (button comes undone), 'my only complaint'",
        "frame": "positive, embedded problem",
    },
    "fresh15-apparel-0220": {
        "signal": "defect (embroidery unravelling), asks what to do",
        "frame": "positive, embedded problem",
    },
    "fresh15-apparel-0230": {
        "signal": "durability worry (snags easily)",
        "frame": "positive, embedded problem",
    },
    "fresh15-apparel-0282": {
        "signal": "returned for itchy fabric",
        "frame": "positive, embedded problem",
    },
    "fresh15-apparel-0536": {
        "signal": "exchanging for size",
        "frame": "positive, embedded problem",
    },
    "fresh15-beauty-0059": {
        "signal": "underperforms (residue), does not recommend",
        "frame": "negative, no concrete request",
    },
    "fresh15-beauty-0304": {
        "signal": "design flaw (no applicator)",
        "frame": "positive, embedded problem",
    },
    "fresh15-food-0059": {
        "signal": "packaging damage risk (cue is past the 128-token cut)",
        "frame": "positive, embedded problem",
    },
    "fresh15-food-0066": {"signal": "taste dislike only", "frame": "negative, no concrete request"},
    "fresh15-food-0523": {"signal": "taste dislike only", "frame": "negative, no concrete request"},
    "fresh15-food-0616": {"signal": "taste dislike only", "frame": "negative, no concrete request"},
}


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4), "wilson95": [round(lo, 4), round(hi, 4)]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--judges", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    items = [json.loads(x) for x in a.items.read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = [r["id"] for r in items]
    votes = load_votes(a.judges, ids)
    th = runtime.load_thresholds(runtime.THRESHOLDS_ONNX_PATH)
    backend = runtime.OnnxBackend(a.artifacts, th, threads=2)
    tasks: list[dict] = []
    for s in range(0, len(items), BATCH):
        tasks += runtime.decisions(th, backend.logits([r["text"] for r in items[s : s + BATCH]]))
    from tokenizers import (
        Tokenizer,
    )  # lazy: the unit tests import this module on CI runners without the serving extras

    tok = Tokenizer.from_file(str(a.artifacts / "hf" / "tokenizer.json"))
    ntok = {r["id"]: len(tok.encode(r["text"]).ids) for r in items}
    star = {r["id"]: r.get("stars") for r in items}
    gold = {i: consensus_label(list(votes[i].values())) for i in ids}
    route = {r["id"]: bool(EF.route_r1(tasks[k]).route) for k, r in enumerate(items)}
    p_yes = {
        r["id"]: float(tasks[k]["needs_action"]["probabilities"]["yes"])
        for k, r in enumerate(items)
    }
    yes = [i for i in ids if gold[i] == "yes"]
    miss = [i for i in yes if not route[i]]
    assert set(miss) == set(READ), "the reader's tags must cover exactly the missed ids"

    def recall(sel: list[str]) -> dict:
        return rate(sum(route[i] for i in sel), len(sel))

    out = {
        "spec": "docs/specs/review-intent.md Amendment 15 (error analysis, S29); descriptive, nothing tuned",
        "n_gold_yes": len(yes),
        "n_missed": len(miss),
        "tally_of_misses": {
            "4-0 yes": sum(1 for i in miss if list(votes[i].values()).count("yes") == 4),
            "3-1 yes": sum(1 for i in miss if list(votes[i].values()).count("yes") == 3),
        },
        "recall_by_stars": {
            ">=4 stars": recall([i for i in yes if (star[i] or 0) >= 4]),
            "<=3 stars": recall([i for i in yes if (star[i] or 0) < 4]),
        },
        "recall_by_length": {
            f"<= {MAX_TOKENS} tokens": recall([i for i in yes if ntok[i] <= MAX_TOKENS]),
            f"> {MAX_TOKENS} tokens (head-truncated)": recall(
                [i for i in yes if ntok[i] > MAX_TOKENS]
            ),
        },
        "median_tokens": {
            "missed": st.median(ntok[i] for i in miss),
            "routed gold-yes": st.median(ntok[i] for i in yes if route[i]),
        },
        "frame_counts": {
            f: sum(1 for i in miss if READ[i]["frame"] == f)
            for f in sorted({v["frame"] for v in READ.values()})
        },
        "misses": [
            {
                "id": i,
                "stars": star[i],
                "n_tokens": ntok[i],
                "panel_yes_of_4": list(votes[i].values()).count("yes"),
                "encoder_p_yes": round(p_yes[i], 3),
                **READ[i],
            }
            for i in sorted(miss)
        ],
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "n_missed",
                    "tally_of_misses",
                    "recall_by_stars",
                    "recall_by_length",
                    "frame_counts",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
