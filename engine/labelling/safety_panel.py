"""Panel-label the mined safety candidates and report mining precision per keyword (S23 item 6a).

    python -m engine.labelling.safety_panel items --hits D:/ml-cache/s22/safety_hits_flipkart.jsonl \
        --corpus D:/ml-cache/review-corpus --flipkart <vernacular_subset.jsonl> \
        --out D:/ml-cache/s23/safety_items.jsonl --manifest reports/labelling/safety/manifest.json
    python -m engine.labelling.safety_panel report --manifest ... --judges reports/labelling/safety/judges \
        --out reports/labelling/safety/panel_report.json --labels reports/labelling/safety/labels.jsonl \
        --md docs/reports/review-intent-safety-mining.md

The candidate set is ENRICHED by keyword mining: the miner's recall cannot be measured here (only mined items are
labelled), and any classifier precision measured on this stratum is not a base-rate precision. Items are drawn with
the patterns of `mine_safety` (fixed before any label), minus the leakage ledger, the sealed items and anything that
near-duplicates or quotes a sealed item. Review text goes only to the private items file; the manifest, the labels
and the report carry ids, hashes, keyword tags and labels.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import zlib
from collections import Counter
from pathlib import Path

from engine.labelling import agreement as A
from engine.labelling.leakage_neardup import CONTAIN, FP, JACCARD, MIN_QUOTE_CHARS, norm, shingles
from engine.labelling.mine_safety import KEYWORDS, MIN_CHARS, hits_for
from engine.labelling.pilot_sample import clean, load_all
from engine.labelling.repilot2_report import wilson
from engine.labelling.repilot_report import consensus_label
from engine.labelling.scale_report import PANEL, gwet_ac1
from engine.labelling.scale_sample import split_of, text_sha

SEED = 45  # draws of the non-Flipkart sources (Flipkart is taken whole); fixed before any label
OTHER_CAP = 150  # per non-Flipkart source: keeps the panel run inside one Kaggle session
CATEGORY = {"flipkart": "vernacular", "sephora": "beauty", "food": "food", "apparel": "apparel"}
CORPUS_KEY = {"sephora": "beauty", "food": "food", "apparel": "apparel"}
# Romanised / Devanagari Hindi tokens inside the mined patterns: an item matching any is a Hinglish hit.
HINGLISH = re.compile(
    r"\baag\b|आग|\bjal(?:a|ne|gaya|gya)\b|\bjalna\b|\bdhuan|\bdhua\b|\bchingari\b|\bcurrent (?:laga|lag|mara|maar)"
    r"|\bphat(?:a|gaya)\b|\bpighal|\bkhujli\b|\bchot\b|\bkhoon\b|\bdawakhana\b|\bgala (?:ghut|ghutna)|\bnukila\b",
    re.I,
)


def is_hinglish(text: str) -> bool:
    """A mined Hindi token OR the production detector says hi-en / hi (lets Hinglish reviews with an English
    safety word, e.g. 'bahut smoke aa raha hai', count). The detector is imperfect (ADR 0021); a flag, not a label."""
    from app.core.language import detect_language

    return bool(HINGLISH.search(text)) or detect_language(text) in ("hi-en", "hi")


def retag(a: argparse.Namespace) -> None:
    """Recompute the `hinglish` flag in the committed manifest from the private items file."""
    text = {
        r["id"]: r["text"]
        for r in map(json.loads, Path(a.items).read_text(encoding="utf-8").splitlines())
    }
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    for m in man["items"]:
        m["hinglish"] = is_hinglish(text[m["id"]])
    Path(a.manifest).write_text(dump_manifest(man), encoding="utf-8")
    print(json.dumps({"hinglish": sum(m["hinglish"] for m in man["items"])}))


TASKS = ("needs_action", "broad_intent", "sentiment", "urgency")
GATE = {"needs_action": "C1_needs_action", "broad_intent": "C2_broad_intent",
        "sentiment": "T2_sentiment", "urgency": "T3_urgency"}  # fmt: skip


class SealedIndex:
    """Near-duplicate / quoted-substring test of one candidate against the committed sealed fingerprints."""

    def __init__(self, fp: dict) -> None:
        self.items = {h: (set(v["gj"]), set(v["g12"]), v["n"]) for h, v in fp["items"].items()}
        self.i12: dict[int, list[str]] = {}
        self.ij: dict[int, list[str]] = {}
        for h, (gj, g12, _) in self.items.items():
            for g in g12:
                self.i12.setdefault(g, []).append(h)
            for g in gj:
                self.ij.setdefault(g, []).append(h)

    def leaks(self, text: str) -> bool:
        n = norm(text)
        if len(n) < 20:
            return False
        if not any(
            zlib.crc32(n[i : i + 12].encode("utf-8")) in self.i12 for i in range(0, len(n) - 11, 2)
        ):
            return False
        gj = shingles(n, 4)
        near = {h for g in gj if g in self.ij for h in self.ij[g]}
        if not near:
            return False
        g12 = shingles(n, 12)
        for h in near:
            sj, s12, ln = self.items[h]
            if len(sj & gj) / max(1, len(sj | gj)) >= JACCARD:
                return True
            if ln >= MIN_QUOTE_CHARS and s12 and len(s12 & g12) / len(s12) >= CONTAIN:
                return True
        return False


def mine_other(
    corpus: Path, flipkart: Path, banned: set[str], seen: set[str]
) -> dict[str, list[dict]]:
    """Hits of the same patterns in the non-Flipkart corpora (same length floor as the scale sample)."""
    frames = load_all(corpus, flipkart)
    out: dict[str, list[dict]] = {}
    for src, key in CORPUS_KEY.items():
        rows = []
        for text in frames[key]["text"]:
            if not isinstance(text, str):
                continue
            t = clean(text)
            if len(t) < max(MIN_CHARS, 40) or len(t) > 1000:
                continue
            kw = hits_for(t)
            if not kw:
                continue
            sha = text_sha(t)
            if sha in banned or sha in seen:
                continue
            seen.add(sha)
            rows.append({"sha": sha, "src": src, "kw": kw, "text": t})
        out[src] = rows
    return out


def build_items(a: argparse.Namespace) -> None:
    led = json.loads(Path(a.ledger).read_text(encoding="utf-8"))
    banned = set(led["entries"]) | set(led["sealed_excluded"])
    index = SealedIndex(json.loads(FP.read_text(encoding="utf-8")))
    seen: set[str] = set()
    pools: dict[str, list[dict]] = {"flipkart": []}
    dropped = Counter()
    for ln in Path(a.hits).read_text(encoding="utf-8").splitlines():
        h = json.loads(ln)
        if h["sha"] in banned:
            dropped["flipkart_ledger"] += 1
        elif h["sha"] in seen:
            dropped["flipkart_dup"] += 1
        elif index.leaks(h["text"]):
            dropped["flipkart_sealed_neardup"] += 1
        else:
            seen.add(h["sha"])
            pools["flipkart"].append({k: h[k] for k in ("sha", "src", "kw", "text")})
    if not a.skip_other:
        other = mine_other(Path(a.corpus), Path(a.flipkart), banned, seen)
        rng = random.Random(SEED)
        for src, rows in other.items():
            rows = [r for r in rows if not index.leaks(r["text"])]
            rows.sort(key=lambda r: r["sha"])  # fixed order before the seeded draw
            pools[src] = sorted(rng.sample(rows, min(OTHER_CAP, len(rows))), key=lambda r: r["sha"])
            dropped[f"{src}_pool_after_exclusions"] = len(rows)
    items, manifest = [], []
    for src, rows in pools.items():
        for n, r in enumerate(rows):
            iid = f"safety-{src}-{n:04d}"
            sp = split_of(r["sha"])
            hing = is_hinglish(r["text"])
            items.append({"id": iid, "stratum": "safety", "category": CATEGORY[src], "text": r["text"],
                          "sha": r["sha"], "split": sp})  # fmt: skip
            manifest.append({"id": iid, "src": src, "sha": r["sha"], "kw": r["kw"], "split": sp,
                             "hinglish": hing})  # fmt: skip
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items), encoding="utf-8"
    )
    Path(a.manifest).parent.mkdir(parents=True, exist_ok=True)
    man = {"seed": SEED, "other_cap": OTHER_CAP, "keywords": sorted(KEYWORDS), "dropped": dict(dropped),
           "n": len(manifest), "per_src": dict(Counter(m["src"] for m in manifest)), "items": manifest}  # fmt: skip
    Path(a.manifest).write_text(dump_manifest(man), encoding="utf-8")
    print(json.dumps({"n": len(items), "per_src": dict(Counter(m["src"] for m in manifest)),
                      "dropped": dict(dropped)}))  # fmt: skip


def dump_manifest(man: dict) -> str:
    """JSON with one item per line (keeps the committed file diffable: 1,800 items, ~1,800 lines)."""
    head = json.dumps({k: v for k, v in man.items() if k != "items"}, indent=1)[:-2]
    rows = ",\n".join(" " + json.dumps(m) for m in man["items"])
    return head + ',\n "items": [\n' + rows + "\n]\n}\n"


def prop(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "p": round(k / n, 4) if n else None, "wilson95": [lo, hi]}


def precision_block(ids: list[str], urg: dict[str, str | None]) -> dict:
    n = len(ids)
    hi = sum(urg[i] == "high" for i in ids)
    mh = sum(urg[i] in ("medium", "high") for i in ids)
    nc = sum(urg[i] is None for i in ids)
    return {
        "n_candidates": n,
        "no_consensus": nc,
        "high": prop(hi, n),
        "medium_or_high": prop(mh, n),
    }


def build_report(manifest: dict, items: dict[str, dict[str, dict]]) -> tuple[dict, list[dict]]:
    man = {m["id"]: m for m in manifest["items"]}
    missing = [i for i in man if i not in items]
    if missing:
        raise ValueError(f"{len(missing)} manifest ids have no judge record")
    judges = [j for j in PANEL if any(j in r for r in items.values())]

    def val(i: str, j: str, t: str):  # noqa: ANN202
        rec = items[i].get(j)
        return (rec.get("text") or {}).get(t) if rec else None

    cons = {i: {t: consensus_label([val(i, j, t) for j in judges]) for t in TASKS} for i in man}
    urg = {i: cons[i]["urgency"] for i in man}
    ids = sorted(man)
    rep: dict = {"judges": judges, "n_items": len(ids), "overall": precision_block(ids, urg)}
    rep["per_src"] = {s: precision_block([i for i in ids if man[i]["src"] == s], urg)
                      for s in sorted({m["src"] for m in man.values()})}  # fmt: skip
    rep["per_keyword"] = {k: precision_block([i for i in ids if k in man[i]["kw"]], urg)
                          for k in manifest["keywords"]}  # fmt: skip
    rep["per_keyword_flipkart"] = {
        k: precision_block(
            [i for i in ids if k in man[i]["kw"] and man[i]["src"] == "flipkart"], urg
        )
        for k in manifest["keywords"]
    }
    rep["hinglish"] = {
        "definition": "text matches a Hindi token of the mined patterns (HINGLISH regex) or app.core.language.detect_language says hi-en/hi (imperfect detector)",
        "hinglish": precision_block([i for i in ids if man[i]["hinglish"]], urg),
        "english_only": precision_block([i for i in ids if not man[i]["hinglish"]], urg),
        "per_keyword": {
            k: precision_block([i for i in ids if man[i]["hinglish"] and k in man[i]["kw"]], urg)
            for k in manifest["keywords"]
        },  # fmt: skip
    }
    rep["positives_by_split"] = {
        sp: sum(urg[i] == "high" for i in ids if man[i]["split"] == sp)
        for sp in ("train", "val", "test")
    }
    rep["candidates_by_split"] = dict(Counter(man[i]["split"] for i in ids))
    rep["positives_train_plus_val"] = (
        rep["positives_by_split"]["train"] + rep["positives_by_split"]["val"]
    )
    rep["positives_sealed_stratum"] = rep["positives_by_split"]["test"]
    rep["urgency_consensus_counts"] = dict(Counter(str(urg[i]) for i in ids))
    tasks = A.build_tasks(judges, {i: items[i] for i in ids}, "v3")
    rep["agreement"] = {}
    for t, key in GATE.items():
        r = dict(tasks[key])
        r["gate"] = A.verdict(r)
        ratings = [[val(i, j, t) for j in judges] for i in ids]
        r["gwet_ac1"] = round(gwet_ac1(ratings), 4)
        rep["agreement"][key] = r
    rep["caveat"] = (
        "Candidates are keyword-mined (enriched). Miner recall is unmeasured; precision here is the share of "
        "mined hits the panel calls high, not a base rate, and a classifier scored on this stratum is not "
        "scored on the natural distribution. Keyword rows overlap (an item with two keywords counts in both)."
    )
    labels = [{"id": i, "sha": man[i]["sha"], "src": man[i]["src"], "kw": man[i]["kw"],
               "hinglish": man[i]["hinglish"], "split": man[i]["split"], **cons[i]} for i in ids]  # fmt: skip
    return rep, labels


def _pct(b: dict) -> str:
    if not b["n"]:
        return "n/a"
    return f"{b['k']}/{b['n']} = {100 * b['p']:.1f}% [{100 * b['wilson95'][0]:.1f}, {100 * b['wilson95'][1]:.1f}]"


def render_md(rep: dict, meta: dict) -> str:
    L = ["# Safety-language mining: panel precision (S23 item 6a)", "",
         "Generated by `python -m engine.labelling.safety_panel report`; every number comes from "
         f"`reports/labelling/safety/panel_report.json` (judge commit `{meta.get('commit', 'unknown')}`). "
         "Do not hand-edit.", "",
         f"**Caveat.** {rep['caveat']}", "",
         f"Judges: {', '.join(rep['judges'])}. Consensus = `repilot_report.consensus_label` (at least 3 valid "
         "votes and a clear majority). Intervals are Wilson 95%.", "",
         "## Overall", "",
         f"- candidates: {rep['overall']['n_candidates']} (no urgency consensus: {rep['overall']['no_consensus']})",
         f"- urgency=high: {_pct(rep['overall']['high'])}",
         f"- urgency in medium/high: {_pct(rep['overall']['medium_or_high'])}", "",
         "## By source", "", "| source | candidates | high | medium or high |", "|---|---|---|---|"]  # fmt: skip
    for s, b in rep["per_src"].items():
        L.append(f"| {s} | {b['n_candidates']} | {_pct(b['high'])} | {_pct(b['medium_or_high'])} |")
    L += ["", "## Per keyword (all sources; rows overlap)", "",
          "| keyword | candidates | high | medium or high |", "|---|---|---|---|"]  # fmt: skip
    for k, b in rep["per_keyword"].items():
        L.append(f"| {k} | {b['n_candidates']} | {_pct(b['high'])} | {_pct(b['medium_or_high'])} |")
    L += ["", "## Per keyword (Flipkart only)", "",
          "| keyword | candidates | high | medium or high |", "|---|---|---|---|"]  # fmt: skip
    for k, b in rep["per_keyword_flipkart"].items():
        L.append(f"| {k} | {b['n_candidates']} | {_pct(b['high'])} | {_pct(b['medium_or_high'])} |")
    h = rep["hinglish"]
    L += ["", "## Hinglish hits reported separately", "", f"Definition: {h['definition']}.", "",
          "| group | candidates | high | medium or high |", "|---|---|---|---|",
          f"| hinglish | {h['hinglish']['n_candidates']} | {_pct(h['hinglish']['high'])} | {_pct(h['hinglish']['medium_or_high'])} |",
          f"| other | {h['english_only']['n_candidates']} | {_pct(h['english_only']['high'])} | {_pct(h['english_only']['medium_or_high'])} |",
          "", "| hinglish keyword | candidates | high |", "|---|---|---|"]  # fmt: skip
    for k, b in h["per_keyword"].items():
        if b["n_candidates"]:
            L.append(f"| {k} | {b['n_candidates']} | {_pct(b['high'])} |")
    L += ["", "## Yield and split", "",
          f"- candidates by split (content-hash rule `scale_sample.split_of`): {rep['candidates_by_split']}",
          f"- urgency=high positives by split: {rep['positives_by_split']}",
          f"- positives available for train+val: {rep['positives_train_plus_val']}",
          f"- positives in the sealed safety stratum (test split): {rep['positives_sealed_stratum']}",
          f"- urgency consensus counts: {rep['urgency_consensus_counts']}", "",
          "## Panel agreement on this stratum", "",
          "| task | alpha | clear consensus | unanimous | Gwet AC1 | gate |", "|---|---|---|---|---|---|"]  # fmt: skip
    for k, r in rep["agreement"].items():
        L.append(
            f"| {k} | {r['alpha']} | {r['clear_consensus']} | {r['unanimous']} | {r['gwet_ac1']} | {r['gate']} |"
        )
    return "\n".join(L) + "\n"


def run_report(a: argparse.Namespace) -> None:
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    items: dict[str, dict[str, dict]] = {}
    for d in a.judges:
        _, part = A.load_judges(Path(d))
        for i, recs in part.items():
            items.setdefault(i, {}).update(recs)
    rep, labels = build_report(manifest, items)
    meta = {"commit": a.commit}
    rep["meta"] = meta
    Path(a.out).write_text(json.dumps(rep, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    Path(a.labels).write_text("".join(json.dumps(r) + "\n" for r in labels), encoding="utf-8")
    Path(a.md).parent.mkdir(parents=True, exist_ok=True)
    Path(a.md).write_text(render_md(rep, meta), encoding="utf-8")
    print(f"wrote {a.out}, {a.labels}, {a.md}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("items")
    p.add_argument("--hits", required=True)
    p.add_argument("--corpus", default="D:/ml-cache/review-corpus")
    p.add_argument("--flipkart", default="data/processed/vernacular_subset.jsonl")
    p.add_argument("--ledger", default="reports/labelling/ledger.json")
    p.add_argument("--out", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--skip-other", action="store_true")
    p = sub.add_parser("retag")
    p.add_argument("--items", required=True)
    p.add_argument("--manifest", required=True)
    p = sub.add_parser("report")
    p.add_argument("--manifest", required=True)
    p.add_argument("--judges", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--md", required=True)
    p.add_argument(
        "--commit", default="unknown", help="repo commit the Kaggle kernel ran (status.json)"
    )
    a = ap.parse_args()
    {"items": build_items, "retag": retag, "report": run_report}[a.cmd](a)


if __name__ == "__main__":
    main()
