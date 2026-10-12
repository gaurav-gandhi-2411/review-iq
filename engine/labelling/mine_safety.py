"""Pre-registered safety-language miner for the rare `urgency=high` class (S23 item 2).

Patterns are fixed here BEFORE any panel label on the hits exists. Hits are candidates, not labels.
Every keyword has its own bucket so the report can show mining precision per keyword once the panel has labelled.
Hinglish equivalents are romanised only (the corpus is romanised); Devanagari forms are included where common.

    python -m engine.labelling.mine_safety --corpus data/processed/flipkart_deduped.jsonl --out <hits.jsonl> --counts reports/labelling/safety/mine_counts.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from engine.labelling.pilot_sample import clean
from engine.labelling.scale_sample import text_sha

# Word-boundary stems; negation is NOT filtered (the panel decides "no burn marks" is not urgent).
KEYWORDS: dict[str, str] = {
    "fire": r"(?:caught|catch(?:es|ing)?|on|set on|burst into) fire|\bfire hazard|\bflames?\b|\baag\b|\bआग\b",
    "burn": r"\bburn(?:t|ed|s|ing)?\b|\bjal(?:a|ne|gaya|gya)\b|\bjalna\b",
    "smoke": r"\bsmok(?:e|ing|ed|es)\b|\bdhuan\w*\b|\bdhua\b",
    "spark": r"\bspark(?:s|ed|ing)?\b|\bchingari\b",
    "shock": r"\b(?:electric(?:al)? )?shock(?:s|ed|ing)?\b|\bcurrent (?:laga|lag|mara|maar)\w*\b|\belectrocut\w+",
    "explode": r"\bexplo(?:de|ded|des|ding|sion)\b|\bblast(?:ed)?\b|\bblew up\b|\bphat(?:a|gaya)\b",
    "melt": r"\bmelt(?:ed|s|ing)?\b|\bpighal\w*\b",
    "allergy": r"\ballerg(?:y|ic|ies)\b|\ballergic reaction\b|\banaphyla\w+",
    "rash": r"\brash(?:es)?\b|\bhives\b|\bitch(?:y|ing)\b|\bkhujli\b|\bred bumps\b|\bbreak ?out\b",
    "injury": r"\binjur(?:y|ed|ies)\b|\bbleed(?:ing)?\b|\bcut (?:my|his|her) (?:finger|hand)\b|\bchot\b|\bkhoon\b",
    "hospital": r"\bhospital\w*\b|\bdoctor\b|\ber visit\b|\bemergency\b|\bclinic\b|\bdawakhana\b",
    "choking": r"\bchok(?:e|ed|ing)\b|\bchoking hazard\b|\bswallow(?:ed)? (?:a )?(?:small|part|piece)\b|\bgala (?:ghut|ghutna)\w*\b",
    "sharp": r"\bsharp (?:edge|edges|metal|corner|corners)\b|\bcut me\b|\bcuts? (?:my|your) (?:hand|finger)s?\b|\bnukila\b",
}
PATTERNS = {k: re.compile(v, re.I) for k, v in KEYWORDS.items()}
MIN_CHARS = 25


def hits_for(text: str) -> list[str]:
    return [k for k, p in PATTERNS.items() if p.search(text)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--text-field", default="text")
    ap.add_argument("--out", required=True)
    ap.add_argument("--counts", required=True)
    ap.add_argument("--ledger", default="reports/labelling/ledger.json")
    ap.add_argument("--source-tag", default="flipkart")
    a = ap.parse_args()
    ledger = set(json.loads(Path(a.ledger).read_text(encoding="utf-8"))["entries"])
    seen: set[str] = set()
    per_kw: Counter[str] = Counter()
    n = excluded = kept = 0
    with Path(a.corpus).open(encoding="utf-8") as f, Path(a.out).open("w", encoding="utf-8") as out:
        for ln in f:
            row = json.loads(ln)
            n += 1
            t = clean(row.get(a.text_field, ""))
            if len(t) < MIN_CHARS:
                continue
            h = hits_for(t)
            if not h:
                continue
            sha = text_sha(t)
            if sha in ledger or sha in seen:
                excluded += 1
                continue
            seen.add(sha)
            kept += 1
            per_kw.update(h)
            out.write(
                json.dumps(
                    {
                        "sha": sha,
                        "src": a.source_tag,
                        "id": row.get("id"),
                        "kw": h,
                        "product": row.get("product_name"),
                        "text": t,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    Path(a.counts).write_text(
        json.dumps(
            {
                "corpus_rows": n,
                "hits_kept": kept,
                "hits_excluded_ledger_or_dup": excluded,
                "per_keyword": dict(per_kw.most_common()),
            },
            indent=1,
        )
        + "\n"
    )
    print(
        json.dumps(
            {"rows": n, "kept": kept, "excluded": excluded, "per_kw": dict(per_kw.most_common())}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
