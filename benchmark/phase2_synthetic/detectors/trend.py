"""SYNTHETIC-VALIDATED DETECTOR -- this code's logic is proven against a synthetic testbed with
PLANTED, KNOWN ground truth (benchmark/phase2_synthetic/). It is NOT proven against real seller
data -- planted patterns may not match how real complaint trends actually look in production. Do
not quote synthetic precision/recall as real-world accuracy. Ready to run against live tenant
data, not yet validated against it.

Trend detector: flags a (product, topic) pair where a specific complaint theme's frequency is
genuinely RISING over time (escalating dissatisfaction), as distinct from a product with a
stable-but-nonzero complaint rate. This is a MODERATION-PRIORITIZATION SIGNAL, not a verdict --
mirrors review-iq's existing authenticity feature and the phase2_campaign/ detector: a confidence
score plus concrete evidence surfaced for a human moderator, never an automated "this product is
in crisis" label.

General-purpose by design: scans every product for every topic actually appearing in its
reviews' extracted `topics`. No product ID or topic name is hardcoded into the detection logic.

Self-contained on purpose (see benchmark/phase2_synthetic/detectors/common.py's own docstring for
the same convention) -- loading/parsing helpers below are prefixed `_trend_` and this module does
not import from sibling detector files, to avoid any coupling to code still being written
concurrently in this directory.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# At least this many negative mentions of a topic (across the whole product lifetime) before a
# phase-over-phase trend is even considered -- guards against a single 1-review "trend".
MIN_TOTAL_MENTIONS = 6

# The product's own observed date range is split into this many equal-length contiguous phases.
N_PHASES = 4

# Spearman rank correlation between phase-index and phase-count must clear this bar -- a clean
# monotonic rise, not just "more complaints somewhere late".
CORRELATION_THRESHOLD = 0.8

# Absolute rise from first phase to last phase must clear this bar too -- rejects a
# flat-but-positively-correlated-by-noise sequence (e.g. 1 -> 1 -> 1 -> 2 can correlate highly
# while being a trivially small, likely-noise rise).
MIN_RISE = 3

# Confidence saturates (rise contribution maxes out) once the absolute rise reaches this many
# mentions -- a bigger rise than this doesn't make the signal more actionable for a moderator.
RISE_SATURATION = 8.0

# A review counts as a negative mention of a topic when the topic is in its `topics` list AND its
# sentiment is one of these -- "mixed" reviews still carry a real complaint about the topic even
# if the overall review isn't purely negative.
NEGATIVE_SENTIMENTS = frozenset({"negative", "mixed"})


@dataclass(frozen=True)
class _trend_ReviewRecord:
    """One review normalized into the fields the trend scan needs.

    `topics` and `sentiment` come from the production extraction pipeline's output
    (`extraction.topics`, `extraction.sentiment`), not re-derived from raw review text --
    detectors consume already-extracted fields, matching the production contract.
    """

    review_id: str
    product_id: str
    timestamp: datetime
    topics: tuple[str, ...]
    sentiment: str | None


@dataclass(frozen=True)
class TrendFlag:
    """One flagged (product, topic) pair: a moderation-prioritization signal, not a verdict."""

    product_id: str
    topic: str
    confidence: float
    evidence: dict[str, Any]


def _trend_parse_timestamp(raw: str) -> datetime:
    """Parse an ISO8601 UTC timestamp with a trailing `Z` (e.g. `2026-03-09T00:00:00Z`), which
    `datetime.fromisoformat` cannot parse directly on Python < 3.11."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _trend_load_extractions(path: Path) -> list[_trend_ReviewRecord]:
    """Load `extractions.jsonl` into normalized review records.

    Records whose extraction failed (`extraction._error` set) are skipped -- they have no
    reliable `topics`/`sentiment` to detect on.
    """
    records: list[_trend_ReviewRecord] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            extraction = raw.get("extraction") or {}
            if extraction.get("_error"):
                continue
            records.append(
                _trend_ReviewRecord(
                    review_id=raw["review_id"],
                    product_id=raw["product_id"],
                    timestamp=_trend_parse_timestamp(raw["timestamp"]),
                    topics=tuple(extraction.get("topics") or []),
                    sentiment=extraction.get("sentiment"),
                )
            )
    return records


def _trend_phase_windows(
    min_ts: datetime, max_ts: datetime, n_phases: int
) -> list[tuple[datetime, datetime]]:
    """Split [min_ts, max_ts] into `n_phases` equal-length contiguous windows.

    Degenerate case (all reviews at the same instant, zero-width range): returns `n_phases`
    identical zero-width windows -- `_trend_phase_index` below always maps to phase 0 in this
    case, so counts still land somewhere sane rather than raising.
    """
    total_seconds = (max_ts - min_ts).total_seconds()
    if total_seconds <= 0:
        return [(min_ts, max_ts) for _ in range(n_phases)]
    width = (max_ts - min_ts) / n_phases
    return [(min_ts + i * width, min_ts + (i + 1) * width) for i in range(n_phases)]


def _trend_phase_index(ts: datetime, min_ts: datetime, width_seconds: float, n_phases: int) -> int:
    """Which phase (0..n_phases-1) a timestamp falls into, given the product's phase width.

    The final phase is inclusive of `max_ts` (capped at `n_phases - 1`) since equal-width
    division of a closed interval would otherwise place the single latest review just past the
    last window's boundary.
    """
    if width_seconds <= 0:
        return 0
    idx = int((ts - min_ts).total_seconds() / width_seconds)
    return min(max(idx, 0), n_phases - 1)


def _trend_spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman rank correlation between two equal-length sequences.

    Implemented as Pearson correlation on ranks (pandas' own `.corr(method="spearman")` shells
    out to `scipy.stats.spearmanr`, which is not an installed dependency here) -- ranking via
    `pd.Series.rank()` then Pearson-correlating the ranks is mathematically equivalent to
    Spearman's rho and needs only pandas.

    Returns 0.0 (rather than NaN) when the correlation is undefined -- e.g. one sequence has zero
    variance -- so downstream threshold comparisons behave predictably instead of silently always
    failing on a NaN comparison.
    """
    x_ranks = pd.Series(x).rank()
    y_ranks = pd.Series(y).rank()
    corr = x_ranks.corr(y_ranks, method="pearson")
    return 0.0 if pd.isna(corr) else float(corr)


def scan_for_trends(
    records: Sequence[_trend_ReviewRecord],
    min_total_mentions: int = MIN_TOTAL_MENTIONS,
    n_phases: int = N_PHASES,
    correlation_threshold: float = CORRELATION_THRESHOLD,
    min_rise: int = MIN_RISE,
    rise_saturation: float = RISE_SATURATION,
) -> list[TrendFlag]:
    """Scan all products for any (product, topic) pair whose negative-mention frequency is
    genuinely rising over time.

    General-purpose: every product_id and every topic string actually present in `records` is
    considered -- nothing here is specific to any particular product or topic name.
    """
    by_product: dict[str, list[_trend_ReviewRecord]] = defaultdict(list)
    for record in records:
        by_product[record.product_id].append(record)

    flags: list[TrendFlag] = []
    for product_id, product_records in by_product.items():
        timestamps = [r.timestamp for r in product_records]
        min_ts, max_ts = min(timestamps), max(timestamps)
        width_seconds = (max_ts - min_ts).total_seconds() / n_phases
        windows = _trend_phase_windows(min_ts, max_ts, n_phases)

        by_topic: dict[str, list[_trend_ReviewRecord]] = defaultdict(list)
        for record in product_records:
            if record.sentiment not in NEGATIVE_SENTIMENTS:
                continue
            for topic in record.topics:
                by_topic[topic].append(record)

        for topic, topic_records in by_topic.items():
            if len(topic_records) < min_total_mentions:
                continue

            phase_counts = [0] * n_phases
            phase_review_ids: list[list[str]] = [[] for _ in range(n_phases)]
            for record in topic_records:
                idx = _trend_phase_index(record.timestamp, min_ts, width_seconds, n_phases)
                phase_counts[idx] += 1
                phase_review_ids[idx].append(record.review_id)

            correlation = _trend_spearman(list(range(n_phases)), phase_counts)
            first_count, last_count = phase_counts[0], phase_counts[-1]
            rise = last_count - first_count

            if correlation < correlation_threshold or rise < min_rise:
                continue

            # Needs BOTH a clean upward shape (correlation) AND a meaningful absolute rise --
            # neither alone is a strong signal. E.g. 1 -> 1 -> 1 -> 4 has a big last-phase jump
            # but a weak correlation across the flat middle; 1 -> 2 -> 3 -> 4 has perfect
            # correlation but a small rise; 1 -> 3 -> 5 -> 8 scores highest on both.
            confidence = min(1.0, correlation) * min(1.0, rise / rise_saturation)
            confidence = round(max(0.0, confidence), 3)

            evidence: dict[str, Any] = {
                "phase_counts": phase_counts,
                "phase_windows": [
                    {"start": w[0].isoformat(), "end": w[1].isoformat()} for w in windows
                ],
                "correlation": round(correlation, 3),
                "first_phase_count": first_count,
                "last_phase_count": last_count,
                "review_ids": [rid for ids in phase_review_ids for rid in ids],
            }
            flags.append(
                TrendFlag(
                    product_id=product_id, topic=topic, confidence=confidence, evidence=evidence
                )
            )

    flags.sort(key=lambda f: f.confidence, reverse=True)
    return flags


def _trend_flag_to_dict(flag: TrendFlag) -> dict[str, Any]:
    """Flatten a `TrendFlag` into the JSONL output record shape."""
    return {
        "product_id": flag.product_id,
        "topic": flag.topic,
        "confidence": flag.confidence,
        "evidence": flag.evidence,
    }


def write_report(flags: list[TrendFlag], path: Path) -> None:
    """Write a human-readable markdown report of all flagged (product, topic) pairs."""
    lines = [
        "# Phase 2 Trend Detector -- Flagged Products",
        "",
        "Moderation-prioritization signal only -- confidence + evidence for human review, "
        "never an automated verdict that a product is in crisis.",
        "",
        f"Total flagged (product, topic) pairs: {len(flags)}",
        "",
    ]
    for i, flag in enumerate(flags, start=1):
        counts_str = " -> ".join(str(c) for c in flag.evidence["phase_counts"])
        lines.append(f"## {i}. confidence={flag.confidence} -- {flag.product_id} / {flag.topic!r}")
        lines.append("")
        lines.append(
            f"- {flag.topic!r} complaints rising: {counts_str} across "
            f"{len(flag.evidence['phase_counts'])} phases "
            f"(correlation={flag.evidence['correlation']})"
        )
        lines.append(f"- total negative mentions: {len(flag.evidence['review_ids'])}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Load `extractions.jsonl`, scan all products, and write `trend_flags.jsonl` +
    `TREND_REPORT.md`. Exits cleanly (no crash) if `extractions.jsonl` doesn't exist yet -- it is
    produced by a slow background extraction process that may not have finished."""
    extractions_path = Path(__file__).resolve().parents[1] / "extractions.jsonl"
    if not extractions_path.exists():
        print(
            f"extractions.jsonl not found at {extractions_path} -- nothing to scan, exiting "
            "cleanly. Run again once the background extraction process has produced it."
        )
        return

    records = _trend_load_extractions(extractions_path)
    flags = scan_for_trends(records)

    out_dir = Path(__file__).resolve().parent
    jsonl_path = out_dir / "trend_flags.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for flag in flags:
            f.write(json.dumps(_trend_flag_to_dict(flag), ensure_ascii=False) + "\n")

    report_path = out_dir / "TREND_REPORT.md"
    write_report(flags, report_path)

    print("trend scan report")
    print(f"  reviews scanned: {len(records)}")
    print(f"  flagged (product, topic) pairs: {len(flags)}")
    print(f"  wrote {jsonl_path}")
    print(f"  wrote {report_path}")


if __name__ == "__main__":
    main()
