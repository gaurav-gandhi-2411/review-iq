from __future__ import annotations

import json
import re
from pathlib import Path

# The 3 Kaggle scrape sources encode a stripped special character (rupee sign, multiplication
# sign, non-breaking space) inconsistently: sometimes as a run of literal `?` glyphs, sometimes
# as raw non-ASCII bytes (e.g. `\xa0\xa0`), sometimes as nothing at all. Matches a run of 2+
# non-ASCII/`?` characters standing in for that stripped character.
_VARIANT_RUN = re.compile(r"[^\x00-\x7F?]{2,}|\?{2,}")
# A single leftover marker character can also appear alone (not as part of a 2+ run), typically
# at the very end of the name -- strip it too.
_TRAILING_SINGLE = re.compile(r"[?\xa0]$")
_WHITESPACE = re.compile(r"\s+")


def canonical_product(name: str) -> str:
    """Collapse encoding-variant product name strings into a single canonical key.

    review-iq's 3 licensed Kaggle source datasets encode the same underlying product name
    differently depending on how each scraper handled a stripped special character (rupee sign,
    multiplication sign, non-breaking space): as a run of `?` glyphs, as raw non-ASCII bytes, or
    as nothing at all. This normalizes those encoding variants to one key so campaign-detection
    clustering isn't fragmented by scrape artifacts, e.g.:
      "Product Name??(Color, Size)"
      "Product Name\xa0\xa0(Color, Size)"
      "Product Name(Color, Size)"
    all canonicalize to the same key. Only the encoding-artifact marker is stripped -- the rest
    of the name is left intact, so genuinely different products do not accidentally merge.
    """
    stripped = _VARIANT_RUN.sub("", name)
    stripped = _TRAILING_SINGLE.sub("", stripped)
    collapsed = _WHITESPACE.sub(" ", stripped)
    return collapsed.strip().lower()


def _run_assertions() -> None:
    """Real-corpus assertions proving canonical_product merges encoding variants without
    accidentally merging distinct products. Plain asserts -- this is a research script, not
    part of the pytest suite.
    """
    # (a) known mojibake variants of the SAME product (real pairs from
    # data/processed/flipkart_deduped.jsonl) must canonicalize to the same key.
    merge_pairs = [
        (
            "Candes 12 L Room/Personal Air Cooler??(White, Black, Elegant High Speed-Honey "
            "Comb Cooling Pad & Ice Chamber, Blower)",
            "Candes 12 L Room/Personal Air Cooler??????(White, Black, Elegant High Speed-Honey "
            "Comb Cooling Pad & Ice Chamber, Blower)",
        ),
        (
            "Candes 60 L Room/Personal Air Cooler??(White, Black, CRETA)",
            "Candes 60 L Room/Personal Air Cooler??????(White, Black, CRETA)",
        ),
        (
            "MAHARAJA WHITELINE 65 L Desert Air Cooler??????(White, Grey, Rambo Grey / AC-303)",
            "MAHARAJA WHITELINE 65 L Desert Air Cooler??(White, Grey, Rambo Grey / AC-303)",
        ),
        (
            "colcum Collapsible Wardrobe 88130 Micro Fiber Collapsible Wardrobe\xa0\xa0"
            "(Finish Color - Maroon, DIY(Do-It-Yourself))",
            "colcum Collapsible Wardrobe 88130 Micro Fiber Collapsible Wardrobe??"
            "(Finish Color - Maroon, DIY(Do-It-Yourself))",
        ),
        (
            "S . K Store Carbon Steel Collapsible Wardrobe??(Finish Color - Black, "
            "DIY(Do-It-Yourself))",
            "S . K Store Carbon Steel Collapsible Wardrobe\xa0\xa0(Finish Color - Black, "
            "DIY(Do-It-Yourself))",
        ),
    ]
    for left, right in merge_pairs:
        assert canonical_product(left) == canonical_product(right), (
            f"expected merge: {left!r} vs {right!r}"
        )

    # (b) genuinely different real product_name values must NOT accidentally merge.
    distinct_names = [
        "StarAndDaisy Plastic Study Table\xa0\xa0(Finish Color - Pink, DIY(Do-It-Yourself))",
        "Home Sizzler 153 cm (5.02 ft) Polyester Room Darkening Window Curtain (Pack Of 2)"
        "\xa0\xa0(Floral, Brown)",
        "Revital Men Multivitamin with Calcium, Zinc & Ginseng for Immunity, Strong Bones & "
        "Energy??(30 Capsules)",
        "Butterfly JADE Electric Rice Cooker??(1.8 L, White)",
        "Green Home Reusable Latex Hand Gloves for Kitchen Black Wet and Dry Glove??"
        "(Free Size)",
    ]
    canonical_keys = [canonical_product(n) for n in distinct_names]
    assert len(set(canonical_keys)) == len(distinct_names), (
        f"unrelated products collapsed: {canonical_keys}"
    )

    print(f"assertions passed: {len(merge_pairs)} merge pairs, "
          f"{len(distinct_names)} distinct products stayed distinct")


def _validate_corpus(path: Path) -> dict[str, float | int]:
    """Load the deduped corpus and report raw distinct product_name values vs distinct
    canonical keys. A modest reduction is expected (encoding-variant collapse); a collapse
    below ~40% of raw distinct names would indicate over-merging and needs investigation.
    """
    raw_names: set[str] = set()
    canonical_keys: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            name = record["product_name"]
            raw_names.add(name)
            canonical_keys.add(canonical_product(name))

    n_raw = len(raw_names)
    n_canonical = len(canonical_keys)
    ratio = n_canonical / n_raw if n_raw else 0.0
    return {
        "n_raw_distinct_names": n_raw,
        "n_canonical_keys": n_canonical,
        "canonical_to_raw_ratio": round(ratio, 4),
    }


def main() -> None:
    """Run assertions against real corpus examples, then report canonicalization impact
    on the full deduped corpus."""
    _run_assertions()

    corpus_path = Path(__file__).resolve().parents[2] / "data" / "processed" / \
        "flipkart_deduped.jsonl"
    stats = _validate_corpus(corpus_path)
    print("\ncanonicalization validation report")
    print(f"  raw distinct product_name values: {stats['n_raw_distinct_names']}")
    print(f"  distinct canonical keys:          {stats['n_canonical_keys']}")
    print(f"  canonical/raw ratio:              {stats['canonical_to_raw_ratio']}")
    if stats["canonical_to_raw_ratio"] < 0.4:
        print("  WARNING: ratio below 0.4 -- possible over-merging, investigate before "
              "proceeding")


if __name__ == "__main__":
    main()
