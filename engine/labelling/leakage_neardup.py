"""Near-duplicate and quoted-substring leakage scan for the sealed review-intent set (Amendment 2, S23).

Exact content hashes miss a sealed review that was lightly edited, re-cased, re-punctuated or quoted inside a
longer text. Both texts are normalised (NFKC, casefold, keep letters and digits only), then:

  * near-duplicate: exact Jaccard of 4-character shingle sets >= 0.9 (stronger than a MinHash estimate of it);
  * quoted substring: >= 90 percent of a sealed item's 12-character shingles occur in the candidate string, for
    sealed items of at least 40 normalised characters.

The committed fingerprint file holds only CRC32 shingle hashes per sealed item (no text); build it locally from
the private text file: `python -m engine.labelling.leakage_neardup build --texts <scale.jsonl>`.
Candidates are every line, JSON string value and blank-line-separated paragraph of every tracked text file.
Limits: a sealed review split across two non-adjacent lines, or paraphrased beyond 0.9 Jaccard, is not caught;
translations are not caught.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import zlib
from pathlib import Path

from engine.labelling.leakage_ledger import ROOT, _strings, sealed_hashes, tracked_files
from engine.labelling.pilot_sample import clean
from engine.labelling.scale_sample import text_sha

FP = ROOT / "reports" / "labelling" / "sealed_fingerprints.json"
JACCARD = 0.9
CONTAIN = 0.9
MIN_QUOTE_CHARS = 40
MIN_CAND_CHARS = 20
MAX_BYTES = 5_000_000


def norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", t).casefold() if c.isalnum())


def shingles(s: str, k: int) -> set[int]:
    return {zlib.crc32(s[i : i + k].encode("utf-8")) for i in range(len(s) - k + 1)}


def build_fingerprints(texts: Path) -> dict:
    sealed = sealed_hashes()
    items = {}
    for ln in texts.open(encoding="utf-8"):
        d = json.loads(ln)
        c = clean(d["text"])
        if text_sha(c) not in sealed:
            continue
        n = norm(c)
        items[text_sha(c)] = {
            "n": len(n),
            "gj": sorted(shingles(n, 4)),
            "g12": sorted(shingles(n, 12)),
        }
    return {"jaccard": JACCARD, "contain": CONTAIN, "items": items}


def candidates(path: Path):
    try:
        if path.stat().st_size > MAX_BYTES:
            return
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    yield from raw.splitlines()
    yield from (p.replace("\n", " ") for p in re.split(r"\n\s*\n", raw))
    strings = []
    try:
        if path.suffix == ".json":
            strings = list(_strings(json.loads(raw)))
        elif path.suffix == ".jsonl":
            for ln in raw.splitlines():
                try:
                    strings += list(_strings(json.loads(ln)))
                except ValueError:
                    continue
    except ValueError:
        return
    yield from strings


def scan(fp: dict, files: list[str] | None = None, root: Path = ROOT) -> list[tuple[str, str, str]]:
    items = {h: (set(v["gj"]), set(v["g12"]), v["n"]) for h, v in fp["items"].items()}
    index12: dict[int, list[str]] = {}
    for h, (_, g12, _) in items.items():
        for g in g12:
            index12.setdefault(g, []).append(h)
    indexj: dict[int, list[str]] = {}
    for h, (gj, _, _) in items.items():
        for g in gj:
            indexj.setdefault(g, []).append(h)
    hits: set[tuple[str, str, str]] = set()
    for rel in files if files is not None else tracked_files():
        seen: set[str] = set()
        for cand in candidates(root / rel):
            n = norm(cand)
            if len(n) < MIN_CAND_CHARS or n in seen:
                continue
            seen.add(n)
            # Cheap prefilter: a near-duplicate or a quote shares 12-grams at (almost) every position, so
            # sampling every 2nd position is enough to find it; only survivors pay for the full computation.
            if not any(
                zlib.crc32(n[i : i + 12].encode("utf-8")) in index12
                for i in range(0, len(n) - 11, 2)
            ):
                continue
            gj = shingles(n, 4)
            near = {h for g in gj if g in indexj for h in indexj[g]}
            if not near:
                continue
            g12 = shingles(n, 12)
            for h in near:
                sj, s12, ln = items[h]
                if len(sj & gj) / max(1, len(sj | gj)) >= JACCARD:
                    hits.add((rel, h, "near-duplicate"))
                elif ln >= MIN_QUOTE_CHARS and s12 and len(s12 & g12) / len(s12) >= CONTAIN:
                    hits.add((rel, h, "quoted-substring"))
    return sorted(hits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "scan"])
    ap.add_argument("--texts", type=Path)
    a = ap.parse_args()
    if a.cmd == "build":
        fp = build_fingerprints(a.texts)
        FP.write_text(json.dumps(fp, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"fingerprinted {len(fp['items'])} sealed items")
        return 0
    fp = json.loads(FP.read_text(encoding="utf-8"))
    hits = scan(fp)
    for rel, h, kind in hits:
        print(f"{kind}: {rel} (sealed hash {h})")
    print(f"{len(hits)} near-duplicate / quoted hits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
