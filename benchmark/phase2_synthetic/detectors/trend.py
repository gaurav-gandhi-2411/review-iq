"""SYNTHETIC-VALIDATED DETECTOR -- this code's logic is proven against a synthetic testbed with
PLANTED, KNOWN ground truth (benchmark/phase2_synthetic/). It is NOT proven against real seller
data -- planted patterns may not match how real complaint trends actually look in production. Do
not quote synthetic precision/recall as real-world accuracy. Ready to run against live tenant
data, not yet validated against it.

MOST REAL-DATA-DEPENDENT OF THE THREE PHASE 2 DETECTORS. "Rising/falling over time" is the
pattern synthetic timestamps simulate LEAST realistically of the three. Batch-defect's shape (a
discrete event, then a burst) and fake-campaign's shape (coordinated reviewer/timing/text
clustering) are both fairly universal regardless of what's being sold. A real complaint TREND on
live data can look very different from this testbed's linear-rise-plus-random-jitter construction:
seasonality, promotional spikes, multiple overlapping causes, non-monotonic real-world dynamics.
Trust batch-defect and fake-campaign's synthetic-clean results more than this one's, even though
all three are equally "SYNTHETIC-VALIDATED, not proven on real data" -- this detector specifically
needs real-seller temporal data before its precision/recall claims should be trusted at all.

SCOPE (deliberately distinct from batch-defect, not redundant with it): batch-defect owns FAST
negative clusters -- a discrete event, then a short-window spike. This detector owns:
  1. SLOW/GRADUAL shifts over LONG windows -- a complaint creeping up over months, too gradual to
     ever trip batch-defect's short spike window.
  2. POSITIVE trends -- rising positive sentiment about a topic, entirely outside batch-defect's
     negatives-only scope (e.g. a product that genuinely improved).
  3. AGGREGATE directional sentiment movement -- the product's overall sentiment ratio drifting
     over time, not tied to any single complaint topic.

Three structural guards keep this detector from re-detecting batch-defect's job (see
scan_for_trends and its helpers):
  - CORRELATION_THRESHOLD: the phase sequence must be a clean monotonic shape, not noise.
  - MIN_NON_DECREASING_STEPS: at least this many of the phase-to-phase transitions must move in
    the flagged direction -- rejects "3 flat phases then 1 huge jump", which a fast batch-defect
    cluster landing in a single phase would otherwise produce.
  - MAX_PHASE_SHARE (topic scans only): no single phase may hold more than this fraction of a
    topic's total mentions -- directly rejects a fast cluster concentrated in one phase, which is
    exactly batch-defect's shape.
A fast, single-window spike fails at least one of these even if it happens to correlate well by
chance; a genuinely gradual, sustained shift passes all three.

This is a MODERATION-PRIORITIZATION SIGNAL, not a verdict -- mirrors review-iq's existing
authenticity feature and the phase2_campaign/ detector: a confidence score plus concrete evidence
surfaced for a human moderator, never an automated "this product is in crisis/thriving" label.

General-purpose by design: scans every product for every topic actually appearing in its reviews'
extracted `topics`, plus one product-level aggregate scan. No product ID or topic name is
hardcoded into the detection logic.

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

# At least this many mentions of a topic (of the relevant polarity, across the whole product
# lifetime) before a phase-over-phase trend is even considered -- guards against a single
# 1-review "trend".
MIN_TOTAL_MENTIONS = 6

# The product's own observed date range is split into this many equal-length contiguous phases --
# a LONG window per phase (the full history / 4), by construction the opposite shape of
# batch-defect's short WINDOW_DAYS=10 spike window.
N_PHASES = 4

# Spearman rank correlation between phase-index and phase-count/ratio must clear this bar -- a
# clean monotonic shape, not just "more activity somewhere late".
CORRELATION_THRESHOLD = 0.8

# Absolute rise from first phase to last phase must clear this bar too (topic scans, raw counts) --
# rejects a flat-but-positively-correlated-by-noise sequence (e.g. 1 -> 1 -> 1 -> 2 can correlate
# highly while being a trivially small, likely-noise rise).
MIN_RISE = 3

# Confidence saturates (rise contribution maxes out) once the absolute rise reaches this many
# mentions -- a bigger rise than this doesn't make the signal more actionable for a moderator.
RISE_SATURATION = 8.0

# No single phase may hold more than this fraction of a topic's total mentions -- a genuinely
# gradual trend spreads its mentions across phases; a fast cluster landing entirely inside one
# phase (batch-defect's shape) concentrates almost all of them there. This is the primary guard
# against trend re-detecting batch-defect's job on the topic-count scans.
MAX_PHASE_SHARE = 0.5

# At least this many of the (N_PHASES - 1) phase-to-phase transitions must move in the flagged
# direction (non-decreasing for a rise) -- rejects "flat, flat, flat, huge jump", which a fast
# single-phase cluster produces and which correlation alone does not reliably reject (a step
# function still ranks monotonically). Requires at least 2 of 3 transitions to move consistently
# with N_PHASES=4 -- allows one soft/flat step (real gradual trends have jitter) but not a shape
# driven by a single blip.
MIN_NON_DECREASING_STEPS = N_PHASES - 2

# A review counts as a negative mention of a topic when the topic is in its `topics` list AND its
# sentiment is one of these -- "mixed" reviews still carry a real complaint about the topic even
# if the overall review isn't purely negative.
NEGATIVE_SENTIMENTS = frozenset({"negative", "mixed"})

# A review counts as a positive mention of a topic when the topic is in its `topics` list AND its
# sentiment is this. "mixed" is deliberately excluded here (unlike NEGATIVE_SENTIMENTS) -- a mixed
# review already carries a genuine complaint, so counting it as positive evidence too would let one
# review contribute to both a rising-negative and a rising-positive flag on the same topic.
POSITIVE_SENTIMENTS = frozenset({"positive"})

# Aggregate (product-level, not topic-level) scan needs at least this many total reviews across
# the observed window before a sentiment-ratio trend is considered meaningful -- a smaller product
# doesn't have enough volume per phase for a ratio to mean anything.
MIN_TOTAL_REVIEWS_FOR_AGGREGATE = 12

# Aggregate sentiment ratio (fraction of reviews of the flagged polarity) must rise by at least
# this many percentage points from first phase to last phase.
MIN_RATIO_RISE = 0.15

# No single phase may hold more than this fraction of a product's TOTAL review volume (any
# sentiment) for the aggregate scan -- a genuinely gradual trend has roughly steady review arrival
# across phases (observed margin on this testbed's 2 planted trends: 0.288-0.300 max share).
# A concentrated event (a campaign burst or a batch-defect spike landing inside one phase) inflates
# that phase's overall volume even when its sentiment mix alone wouldn't cross MAX_PHASE_SHARE --
# found via false positives on SYN-CAMPAIGN-02 (aggregate_positive, total-volume share 0.452, its
# positive-match share was only 0.457 -- just under MAX_PHASE_SHARE=0.5) and SYN-BATCH-01
# (total-volume share 0.417) during rebuild validation. Threshold picked from the gap between
# these two clusters (~0.30 genuine vs ~0.42-0.45 confounded) on N=2 examples each side -- small
# sample, needs real-seller-data recalibration like every other threshold in this detector.
MAX_TOTAL_VOLUME_PHASE_SHARE = 0.35

# Ratio-rise value at which the aggregate confidence's rise contribution saturates.
RATIO_RISE_SATURATION = 0.4


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
    """One flagged trend: a moderation-prioritization signal, not a verdict.

    `trend_type` is one of "topic_negative", "topic_positive", "aggregate_negative",
    "aggregate_positive" -- see module docstring's SCOPE section. `topic` is None for the two
    aggregate types (the signal isn't tied to any single complaint/praise theme).
    """

    product_id: str
    trend_type: str
    topic: str | None
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
    variance (a perfectly flat phase series, such as a chronic-but-not-worsening complaint rate) --
    checked explicitly BEFORE the correlation call so this never emits numpy's "invalid value
    encountered in divide" RuntimeWarning from a zero-stddev intermediate, rather than relying on
    NaN comparison semantics after the fact (NaN >= threshold happens to already evaluate False,
    but that leaves the warning as noise in every caller's output).
    """
    x_ranks = pd.Series(x).rank()
    y_ranks = pd.Series(y).rank()
    if x_ranks.nunique() <= 1 or y_ranks.nunique() <= 1:
        return 0.0
    corr = x_ranks.corr(y_ranks, method="pearson")
    return 0.0 if pd.isna(corr) else float(corr)


def _max_phase_share(phase_counts: Sequence[float]) -> float:
    """Largest fraction of the total that any single phase holds. 0.0 for an all-zero series."""
    total = sum(phase_counts)
    return max(phase_counts) / total if total > 0 else 0.0


def _n_non_decreasing_steps(series: Sequence[float]) -> int:
    """How many of the phase-to-phase transitions are non-decreasing (series[i+1] >= series[i])."""
    return sum(1 for i in range(len(series) - 1) if series[i + 1] >= series[i])


def _sustained_rise(
    series: Sequence[float],
    correlation_threshold: float,
    min_rise: float,
    max_phase_share_cap: float | None,
) -> tuple[bool, float, float]:
    """Shared "is this a genuine sustained rise, not a blip or a fast cluster" check used by both
    the topic-count scans and the aggregate-ratio scan. Returns (passes, correlation, rise).

    `max_phase_share_cap` is None for ratio series (share doesn't apply the same way to a ratio as
    to a count -- the monotonic-steps guard below covers the "single blip" risk for ratios).
    """
    correlation = _trend_spearman(list(range(len(series))), series)
    rise = series[-1] - series[0]
    if correlation < correlation_threshold or rise < min_rise:
        return False, correlation, rise
    if max_phase_share_cap is not None and _max_phase_share(series) > max_phase_share_cap:
        return False, correlation, rise
    if _n_non_decreasing_steps(series) < MIN_NON_DECREASING_STEPS:
        return False, correlation, rise
    return True, correlation, rise


def _scan_topic_polarity(
    product_id: str,
    product_records: list[_trend_ReviewRecord],
    min_ts: datetime,
    width_seconds: float,
    windows: list[tuple[datetime, datetime]],
    n_phases: int,
    sentiment_set: frozenset[str],
    trend_type: str,
    min_total_mentions: int,
    correlation_threshold: float,
    min_rise: int,
    rise_saturation: float,
) -> list[TrendFlag]:
    """One polarity pass (negative or positive) of the topic-level scan -- identical shape for
    both, only the sentiment filter and output trend_type differ."""
    by_topic: dict[str, list[_trend_ReviewRecord]] = defaultdict(list)
    for record in product_records:
        if record.sentiment not in sentiment_set:
            continue
        for topic in record.topics:
            by_topic[topic].append(record)

    flags: list[TrendFlag] = []
    for topic, topic_records in by_topic.items():
        if len(topic_records) < min_total_mentions:
            continue

        phase_counts = [0] * n_phases
        phase_review_ids: list[list[str]] = [[] for _ in range(n_phases)]
        for record in topic_records:
            idx = _trend_phase_index(record.timestamp, min_ts, width_seconds, n_phases)
            phase_counts[idx] += 1
            phase_review_ids[idx].append(record.review_id)

        passes, correlation, rise = _sustained_rise(
            phase_counts, correlation_threshold, min_rise, MAX_PHASE_SHARE
        )
        if not passes:
            continue

        # Needs a clean upward shape (correlation) AND a meaningful absolute rise AND a sustained,
        # spread-out shape (guards in _sustained_rise) -- none alone is a strong signal.
        confidence = min(1.0, correlation) * min(1.0, rise / rise_saturation)
        confidence = round(max(0.0, confidence), 3)

        evidence: dict[str, Any] = {
            "phase_counts": phase_counts,
            "phase_windows": [
                {"start": w[0].isoformat(), "end": w[1].isoformat()} for w in windows
            ],
            "correlation": round(correlation, 3),
            "first_phase_count": phase_counts[0],
            "last_phase_count": phase_counts[-1],
            "max_phase_share": round(_max_phase_share(phase_counts), 3),
            "review_ids": [rid for ids in phase_review_ids for rid in ids],
        }
        flags.append(
            TrendFlag(
                product_id=product_id,
                trend_type=trend_type,
                topic=topic,
                confidence=confidence,
                evidence=evidence,
            )
        )
    return flags


def _scan_aggregate_polarity(
    product_id: str,
    product_records: list[_trend_ReviewRecord],
    min_ts: datetime,
    width_seconds: float,
    windows: list[tuple[datetime, datetime]],
    n_phases: int,
    sentiment_set: frozenset[str],
    trend_type: str,
) -> TrendFlag | None:
    """Product-level (not topic-level) directional sentiment-ratio scan: what fraction of ALL
    reviews in each phase carry the flagged polarity, regardless of topic. Catches a product
    getting generally better/worse over time even when no single complaint/praise theme dominates.
    """
    phase_total = [0] * n_phases
    phase_match = [0] * n_phases
    phase_review_ids: list[list[str]] = [[] for _ in range(n_phases)]
    for record in product_records:
        idx = _trend_phase_index(record.timestamp, min_ts, width_seconds, n_phases)
        phase_total[idx] += 1
        if record.sentiment in sentiment_set:
            phase_match[idx] += 1
            phase_review_ids[idx].append(record.review_id)

    if sum(phase_total) < MIN_TOTAL_REVIEWS_FOR_AGGREGATE or any(t == 0 for t in phase_total):
        return None

    # A single concentrated spike (batch-defect's shape) can still produce a smoothly-RISING
    # RATIO across phases if total review volume happens to shrink in later phases -- the ratio
    # itself doesn't carry "share" information the way a raw count does. Found via false positive
    # on SYN-BATCH-01 (aggregate_negative, confidence 1.0) during rebuild validation: its negative
    # MATCH count was concentrated almost entirely in its batch-defect spike window, but the ratio
    # series still looked like a clean gradual climb. Guard directly on the match-count
    # concentration (not the ratio) so this can't slip through the ratio-only checks.
    if _max_phase_share(phase_match) > MAX_PHASE_SHARE:
        return None
    # Total-volume concentration catches the same class of confound even when the concentrated
    # event's OWN sentiment share stays just under MAX_PHASE_SHARE (found via SYN-CAMPAIGN-02's
    # positive-burst false positive -- see MAX_TOTAL_VOLUME_PHASE_SHARE's comment).
    if _max_phase_share(phase_total) > MAX_TOTAL_VOLUME_PHASE_SHARE:
        return None

    ratios = [m / t for m, t in zip(phase_match, phase_total, strict=True)]
    passes, correlation, rise = _sustained_rise(
        ratios, CORRELATION_THRESHOLD, MIN_RATIO_RISE, max_phase_share_cap=None
    )
    if not passes:
        return None

    confidence = min(1.0, correlation) * min(1.0, rise / RATIO_RISE_SATURATION)
    confidence = round(max(0.0, confidence), 3)

    evidence: dict[str, Any] = {
        "phase_ratios": [round(r, 3) for r in ratios],
        "phase_totals": phase_total,
        "phase_match_counts": phase_match,
        "phase_windows": [
            {"start": w[0].isoformat(), "end": w[1].isoformat()} for w in windows
        ],
        "correlation": round(correlation, 3),
        "first_phase_ratio": round(ratios[0], 3),
        "last_phase_ratio": round(ratios[-1], 3),
        "review_ids": [rid for ids in phase_review_ids for rid in ids],
    }
    return TrendFlag(
        product_id=product_id,
        trend_type=trend_type,
        topic=None,
        confidence=confidence,
        evidence=evidence,
    )


def scan_for_trends(
    records: Sequence[_trend_ReviewRecord],
    min_total_mentions: int = MIN_TOTAL_MENTIONS,
    n_phases: int = N_PHASES,
    correlation_threshold: float = CORRELATION_THRESHOLD,
    min_rise: int = MIN_RISE,
    rise_saturation: float = RISE_SATURATION,
) -> list[TrendFlag]:
    """Scan all products for: rising-negative topic trends, rising-positive topic trends, and
    aggregate (product-level) directional sentiment-ratio movement in either polarity.

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

        flags.extend(
            _scan_topic_polarity(
                product_id, product_records, min_ts, width_seconds, windows, n_phases,
                NEGATIVE_SENTIMENTS, "topic_negative", min_total_mentions,
                correlation_threshold, min_rise, rise_saturation,
            )
        )
        flags.extend(
            _scan_topic_polarity(
                product_id, product_records, min_ts, width_seconds, windows, n_phases,
                POSITIVE_SENTIMENTS, "topic_positive", min_total_mentions,
                correlation_threshold, min_rise, rise_saturation,
            )
        )
        for sentiment_set, trend_type in (
            (NEGATIVE_SENTIMENTS, "aggregate_negative"),
            (POSITIVE_SENTIMENTS, "aggregate_positive"),
        ):
            agg_flag = _scan_aggregate_polarity(
                product_id, product_records, min_ts, width_seconds, windows, n_phases,
                sentiment_set, trend_type,
            )
            if agg_flag is not None:
                flags.append(agg_flag)

    flags.sort(key=lambda f: f.confidence, reverse=True)
    return flags


def _trend_flag_to_dict(flag: TrendFlag) -> dict[str, Any]:
    """Flatten a `TrendFlag` into the JSONL output record shape."""
    return {
        "product_id": flag.product_id,
        "trend_type": flag.trend_type,
        "topic": flag.topic,
        "confidence": flag.confidence,
        "evidence": flag.evidence,
    }


def write_report(flags: list[TrendFlag], path: Path) -> None:
    """Write a human-readable markdown report of all flagged trends."""
    lines = [
        "# Phase 2 Trend Detector -- Flagged Products",
        "",
        "Moderation-prioritization signal only -- confidence + evidence for human review, "
        "never an automated verdict that a product is in crisis or thriving.",
        "",
        f"Total flagged trends: {len(flags)}",
        "",
    ]
    for i, flag in enumerate(flags, start=1):
        label = f"{flag.product_id} / {flag.trend_type}"
        if flag.topic is not None:
            label += f" / {flag.topic!r}"
        lines.append(f"## {i}. confidence={flag.confidence} -- {label}")
        lines.append("")
        if "phase_counts" in flag.evidence:
            counts_str = " -> ".join(str(c) for c in flag.evidence["phase_counts"])
            lines.append(
                f"- {flag.topic!r} mentions ({flag.trend_type}): {counts_str} across "
                f"{len(flag.evidence['phase_counts'])} phases "
                f"(correlation={flag.evidence['correlation']}, "
                f"max_phase_share={flag.evidence['max_phase_share']})"
            )
            lines.append(f"- total mentions: {len(flag.evidence['review_ids'])}")
        else:
            ratios_str = " -> ".join(str(r) for r in flag.evidence["phase_ratios"])
            lines.append(
                f"- aggregate {flag.trend_type} ratio: {ratios_str} across "
                f"{len(flag.evidence['phase_ratios'])} phases "
                f"(correlation={flag.evidence['correlation']})"
            )
            lines.append(f"- phase totals: {flag.evidence['phase_totals']}")
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
    print(f"  flagged trends: {len(flags)}")
    print(f"  wrote {jsonl_path}")
    print(f"  wrote {report_path}")


if __name__ == "__main__":
    main()
