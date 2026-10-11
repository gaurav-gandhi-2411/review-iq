"""Review-pattern detector: flags unusual PATTERNS across a product's reviews.

Spec (pre-registered): docs/specs/campaign-detection.md. Offline evaluation:
benchmark/campaign_eval/. Gated behind settings.enable_fake_campaign_detector (off by default;
the flag keeps its historical name so no env/deploy config changes).

PRODUCT RULE: this module flags patterns (counts, time windows, shared text, rating shifts). It
never labels an individual review as fake or inauthentic, and no string it emits may say so
(tests/unit/test_campaign_wording.py enforces that).

Signals, each evaluated as-of a review's arrival using only earlier reviews (no look-ahead), for
window widths of 6h, 24h, 72h and 168h against that product's own trailing 180-day history:
  burst       window count vs the product's own rate, quasi-Poisson with a dispersion estimate
  template    largest group of near-identical texts (character 5-gram Jaccard)
  rating      window mean rating vs history mean (standardised difference)
  mismatch    star rating pointing the opposite way from the wording (lexicon heuristic)
  verified    drop in verified-buyer share (optional input; absent in most data)
An alert needs two signals fired, or one signal far beyond its threshold (see `is_alert`).

Replaces the earlier text-duplicate-within-48h detector, whose reviewer-identity evidence was a
stub (always equal to the review count) and which ignored ratings; see the spec's audit section.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.detectors.campaign_signals import (
    DEFAULT_PARAMS,
    CampaignParams,
    ProductStream,
    Review,
    WindowEvidence,
    epoch_seconds,
)

SIGNAL_BURST = "burst"
SIGNAL_TEMPLATE = "template"
SIGNAL_RATING = "rating_shift"
SIGNAL_MISMATCH = "rating_text_mismatch"
SIGNAL_VERIFIED = "verified_share"

DAY_S = 86400.0


# ---- alert rule ---------------------------------------------------------------------------


def fired_signals(ev: WindowEvidence, p: CampaignParams) -> tuple[list[str], bool]:
    """(fired signal names, whether any single signal is beyond the strong multiple)."""
    fired: list[str] = []
    strong = False
    c = p.strong_multiplier
    if ev.burst is not None and ev.burst >= p.theta_burst:
        fired.append(SIGNAL_BURST)
        strong |= ev.burst >= c * p.theta_burst
    if ev.template_k >= p.k_min:
        fired.append(SIGNAL_TEMPLATE)
        strong |= ev.template_k >= math.ceil(c * p.k_min)
    if ev.rating_z is not None and abs(ev.rating_z) >= p.theta_rating:
        fired.append(SIGNAL_RATING)
        strong |= abs(ev.rating_z) >= c * p.theta_rating
    if p.use_mismatch and ev.mismatch_z is not None and ev.mismatch_z >= p.theta_mismatch:
        fired.append(SIGNAL_MISMATCH)
        strong |= ev.mismatch_z >= c * p.theta_mismatch
    if ev.verified_z is not None and ev.verified_z >= p.theta_verified:
        fired.append(SIGNAL_VERIFIED)
        strong |= ev.verified_z >= c * p.theta_verified
    return fired, strong


def is_alert(ev: WindowEvidence, p: CampaignParams) -> tuple[bool, list[str]]:
    fired, strong = fired_signals(ev, p)
    return (len(fired) >= 2 or strong), fired


@dataclass(frozen=True)
class Alert:
    index: int
    timestamp: float
    width_hours: int
    fired: tuple[str, ...]
    evidence: WindowEvidence


def scan_stream(
    stream: ProductStream, start_index: int = 0, apply_warmup: bool = True
) -> list[Alert]:
    """Alerts opened over the stream (cooldown applied), evaluating each arrival from
    `start_index` and the narrowest window that satisfies the alert rule."""
    p = stream.p
    alerts: list[Alert] = []
    last_t = -math.inf
    t0 = stream.ts[0] if len(stream) else 0.0
    for i in range(max(start_index, 0), len(stream)):
        t = stream.ts[i]
        if apply_warmup and (i < p.warmup_reviews or t - t0 < p.warmup_days * DAY_S):
            continue
        if t - last_t < p.cooldown_days * DAY_S:
            continue
        for w in sorted(p.windows_hours):
            ev = stream.evidence_at(i, w)
            if ev is None:
                continue
            ok, fired = is_alert(ev, p)
            if ok:
                alerts.append(Alert(i, t, w, tuple(fired), ev))
                last_t = t
                break
    return alerts


# ---- user-facing output (counts, windows, shared text; never a verdict on one review) ------


def window_label(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} day" + ("" if days == 1 else "s")
    return f"{hours} hours"


def build_explanation(ev: WindowEvidence, fired: Sequence[str]) -> str:
    """Counts, window and shared text only. Names no individual review and gives no verdict."""
    parts = [
        f"{ev.n} reviews in the last {window_label(ev.width_hours)}"
        f" (about {ev.expected:.1f} expected from this product's last 180 days)."
    ]
    if SIGNAL_TEMPLATE in fired:
        parts.append(f"{ev.template_k} of them share near-identical text.")
    if SIGNAL_RATING in fired and ev.window_mean is not None and ev.history_mean is not None:
        parts.append(
            f"Their average rating is {ev.window_mean:.1f} versus {ev.history_mean:.1f} "
            "over the previous 180 days."
        )
    if SIGNAL_MISMATCH in fired:
        parts.append(
            f"{ev.mismatch_count} of them have a star rating that points the opposite way "
            "from the wording."
        )
    if SIGNAL_VERIFIED in fired and ev.verified_window_share is not None:
        parts.append(
            f"{ev.verified_window_share:.0%} are marked as verified purchases versus "
            f"{(ev.verified_history_share or 0.0):.0%} earlier."
        )
    parts.append("Worth reviewing together.")
    return " ".join(parts)


@dataclass
class CampaignFlag:
    """One alert on one product: the pattern, which signals fired, and the counts."""

    product_id: str
    alert_time: datetime
    width_hours: int
    signals_fired: list[str]
    review_count: int
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "review_count": self.review_count,
            "window_label": window_label(self.width_hours),
            "signals_fired": list(self.signals_fired),
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


def flag_from_alert(stream: ProductStream, product_id: str, alert: Alert) -> CampaignFlag:
    ev = alert.evidence
    t_end = datetime.fromtimestamp(alert.timestamp, tz=UTC)
    t_start = t_end - timedelta(hours=alert.width_hours)
    lo = alert.index - ev.n + 1
    evidence: dict[str, Any] = {
        "window": {
            "start": t_start.isoformat().replace("+00:00", "Z"),
            "end": t_end.isoformat().replace("+00:00", "Z"),
        },
        "window_hours": alert.width_hours,
        "review_count": ev.n,
        "expected_count": round(ev.expected, 2),
        "burst_strength": None if ev.burst is None else round(ev.burst, 2),
        "near_identical_group_size": ev.template_k,
        "window_mean_rating": None if ev.window_mean is None else round(ev.window_mean, 2),
        "history_mean_rating": None if ev.history_mean is None else round(ev.history_mean, 2),
        "review_ids": [stream.reviews[j].review_id for j in range(lo, alert.index + 1)],
        "near_identical_review_ids": [stream.reviews[j].review_id for j in ev.template_members],
    }
    return CampaignFlag(
        product_id=product_id,
        alert_time=t_end,
        width_hours=alert.width_hours,
        signals_fired=list(alert.fired),
        review_count=ev.n,
        explanation=build_explanation(ev, alert.fired),
        evidence=evidence,
    )


def scan_corpus(
    reviews: Sequence[Review],
    params: CampaignParams = DEFAULT_PARAMS,
    since: datetime | None = None,
) -> list[CampaignFlag]:
    """Group reviews by product, scan each product's stream, return alerts (newest first).
    `since` keeps only alerts opened at or after it (the sweep passes a recent cutoff so that
    enabling the detector does not replay a product's whole history as alerts)."""
    by_product: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        by_product[r.product_id].append(r)
    flags: list[CampaignFlag] = []
    for pid, prs in by_product.items():
        if len(prs) < params.warmup_reviews:
            continue
        stream = ProductStream(prs, params)
        for alert in scan_stream(stream):
            if since is None or alert.timestamp >= epoch_seconds(since):
                flags.append(flag_from_alert(stream, pid, alert))
    return sorted(flags, key=lambda f: f.alert_time, reverse=True)


def campaign_reviews_from_rows(rows: list[dict[str, Any]]) -> list[Review]:
    """Adapt app.core.storage_pg.list_dated_extractions_pg rows. Those rows carry no rating and
    no verified flag today, so only the burst and template signals are active in production
    until ingestion plumbs them (a rating or verified key is used when a row has one)."""
    return [
        Review(
            review_id=row["id"],
            product_id=row["product"] or "unknown product",
            timestamp=row["review_date"],
            text=row["review_text"] or "",
            rating=row.get("rating"),
            verified=row.get("verified"),
        )
        for row in rows
    ]
