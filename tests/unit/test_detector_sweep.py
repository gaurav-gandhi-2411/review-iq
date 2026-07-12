"""Unit tests for app.core.alerts.detector_sweep -- per-org isolation, threshold filtering,
dedupe key construction. Not a re-run of either detector's own validation (already proven);
these tests only prove the SWEEP wiring is correct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.alerts.detector_sweep import (
    BATCH_DEFECT_ALERT_THRESHOLD,
    FAKE_CAMPAIGN_ALERT_THRESHOLD,
    _batch_defect_dedupe_key,
    _fake_campaign_dedupe_key,
    run_detector_sweep,
)

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _spike_rows(product: str = "Widget", n: int = 6) -> list[dict]:
    """Rows shaped to trigger an obvious batch-defect spike (all same instant, same topic)."""
    return [
        {
            "id": f"defect-{product}-{i}",
            "product": product,
            "topics": ["battery"],
            "sentiment": "negative",
            "review_date": _NOW,
            "review_text": f"battery died review {i}",
        }
        for i in range(n)
    ]


def _campaign_rows(product: str = "Widget", n: int = 8) -> list[dict]:
    """Rows shaped to trigger an obvious campaign flag: baseline spread + a near-dup text burst.
    Empirically verified to score confidence ~0.875 (comfortably above
    FAKE_CAMPAIGN_ALERT_THRESHOLD=0.5) at build time."""
    import random

    random.seed(7)
    base = _NOW - timedelta(days=60)
    rows = [
        {
            "id": f"base-{product}-{i}",
            "product": product,
            "topics": [],
            "sentiment": "neutral",
            "review_date": base + timedelta(days=random.uniform(0, 50)),
            "review_text": f"baseline unique review number {i} about the {product.lower()}",
        }
        for i in range(15)
    ]
    template = (
        "This product exceeded my expectations completely and arrived very quickly in "
        "perfect condition"
    )
    for i in range(n):
        rows.append(
            {
                "id": f"burst-{product}-{i}",
                "product": product,
                "topics": [],
                "sentiment": "positive",
                "review_date": _NOW + timedelta(hours=i * 2),
                "review_text": template,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Dedupe key construction
# ---------------------------------------------------------------------------


def test_batch_defect_dedupe_key_is_month_bucketed() -> None:
    key1 = _batch_defect_dedupe_key("Widget", "battery", "2026-06-15T00:00:00+00:00")
    key2 = _batch_defect_dedupe_key("Widget", "battery", "2026-06-20T00:00:00+00:00")
    key3 = _batch_defect_dedupe_key("Widget", "battery", "2026-07-01T00:00:00+00:00")
    assert key1 == key2, "same month -> same key, robust to minor window drift across sweeps"
    assert key1 != key3, "different month -> different key, a later recurrence can still alert"
    assert key1 == "batch_defect:Widget:battery:2026-06"


def test_fake_campaign_dedupe_key_is_day_bucketed() -> None:
    key1 = _fake_campaign_dedupe_key("Widget", "2026-06-15T04:00:00Z")
    key2 = _fake_campaign_dedupe_key("Widget", "2026-06-15T20:00:00Z")
    key3 = _fake_campaign_dedupe_key("Widget", "2026-06-16T04:00:00Z")
    assert key1 == key2, "same day -> same key"
    assert key1 != key3, "different day -> different key"
    assert key1 == "fake_campaign:Widget:2026-06-15"


# ---------------------------------------------------------------------------
# run_detector_sweep
# ---------------------------------------------------------------------------


def _settings(batch_defect: bool, fake_campaign: bool) -> MagicMock:
    s = MagicMock()
    s.enable_batch_defect_detector = batch_defect
    s.enable_fake_campaign_detector = fake_campaign
    return s


@pytest.mark.asyncio
async def test_both_disabled_skips_entirely_without_querying_orgs() -> None:
    with (
        patch(
            "app.core.alerts.detector_sweep.get_settings",
            return_value=_settings(False, False),
        ),
        patch(
            "app.core.alerts.detector_sweep.list_orgs_with_dated_extractions_pg"
        ) as mock_list_orgs,
    ):
        result = await run_detector_sweep(MagicMock())

    mock_list_orgs.assert_not_called()
    assert result["batch_defect"]["orgs"] == 0
    assert result["fake_campaign"]["orgs"] == 0


@pytest.mark.asyncio
async def test_batch_defect_alert_fires_above_threshold() -> None:
    with (
        patch(
            "app.core.alerts.detector_sweep.get_settings",
            return_value=_settings(True, False),
        ),
        patch(
            "app.core.alerts.detector_sweep.list_orgs_with_dated_extractions_pg",
            return_value=["org-1"],
        ),
        patch(
            "app.core.alerts.detector_sweep.list_dated_extractions_pg",
            return_value=_spike_rows(),
        ),
        patch(
            "app.core.alerts.detector_sweep.evaluate_and_alert",
            new=AsyncMock(return_value=[MagicMock()]),
        ) as mock_alert,
    ):
        result = await run_detector_sweep(MagicMock())

    assert result["batch_defect"]["orgs"] == 1
    assert result["batch_defect"]["sent"] == 1
    mock_alert.assert_awaited_once()
    call_kwargs = mock_alert.call_args.kwargs
    assert call_kwargs["org_id"] == "org-1"
    assert call_kwargs["review_id"].startswith("batch_defect:Widget:battery:")
    assert call_kwargs["precomputed_events"][0].event_type == "batch_defect"


@pytest.mark.asyncio
async def test_below_threshold_flag_never_calls_evaluate_and_alert() -> None:
    """A weak spike (below BATCH_DEFECT_ALERT_THRESHOLD) must not reach the alert engine at
    all -- confirms threshold filtering happens before evaluate_and_alert, not after."""
    # 4 mentions (bare minimum), spread so the ratio is real but well under the 0.7 alert bar.
    weak_rows = [
        {
            "id": f"weak-{i}",
            "product": "Widget",
            "topics": ["battery"],
            "sentiment": "negative",
            "review_date": _NOW,
            "review_text": "x",
        }
        for i in range(4)
    ] + [
        {
            "id": f"outside-{i}",
            "product": "Widget",
            "topics": ["battery"],
            "sentiment": "negative",
            "review_date": _NOW - timedelta(days=15 * (i + 1)),
            "review_text": "x",
        }
        for i in range(3)
    ]
    with (
        patch(
            "app.core.alerts.detector_sweep.get_settings",
            return_value=_settings(True, False),
        ),
        patch(
            "app.core.alerts.detector_sweep.list_orgs_with_dated_extractions_pg",
            return_value=["org-1"],
        ),
        patch(
            "app.core.alerts.detector_sweep.list_dated_extractions_pg",
            return_value=weak_rows,
        ),
        patch("app.core.alerts.detector_sweep.evaluate_and_alert", new=AsyncMock()) as mock_alert,
    ):
        result = await run_detector_sweep(MagicMock())

    mock_alert.assert_not_called()
    assert result["batch_defect"]["sent"] == 0


@pytest.mark.asyncio
async def test_fake_campaign_alert_fires_above_threshold() -> None:
    with (
        patch(
            "app.core.alerts.detector_sweep.get_settings",
            return_value=_settings(False, True),
        ),
        patch(
            "app.core.alerts.detector_sweep.list_orgs_with_dated_extractions_pg",
            return_value=["org-1"],
        ),
        patch(
            "app.core.alerts.detector_sweep.list_dated_extractions_pg",
            return_value=_campaign_rows(),
        ),
        patch(
            "app.core.alerts.detector_sweep.evaluate_and_alert",
            new=AsyncMock(return_value=[MagicMock()]),
        ) as mock_alert,
    ):
        result = await run_detector_sweep(MagicMock())

    assert result["fake_campaign"]["sent"] == 1
    call_kwargs = mock_alert.call_args.kwargs
    assert call_kwargs["review_id"].startswith("fake_campaign:Widget:")
    assert call_kwargs["precomputed_events"][0].event_type == "fake_campaign"


@pytest.mark.asyncio
async def test_one_org_failure_does_not_abort_sweep_for_other_orgs() -> None:
    async def _fake_evaluate(**kwargs: object) -> list[object]:
        if kwargs["org_id"] == "org-a":
            raise RuntimeError("simulated DB error")
        return [MagicMock()]

    with (
        patch(
            "app.core.alerts.detector_sweep.get_settings",
            return_value=_settings(True, False),
        ),
        patch(
            "app.core.alerts.detector_sweep.list_orgs_with_dated_extractions_pg",
            return_value=["org-a", "org-b"],
        ),
        patch(
            "app.core.alerts.detector_sweep.list_dated_extractions_pg",
            return_value=_spike_rows(),
        ),
        patch(
            "app.core.alerts.detector_sweep.evaluate_and_alert",
            new=AsyncMock(side_effect=_fake_evaluate),
        ),
    ):
        result = await run_detector_sweep(MagicMock())

    assert result["batch_defect"]["failed_orgs"] == ["org-a"]
    assert result["batch_defect"]["orgs"] == 1
    assert result["batch_defect"]["sent"] == 1


@pytest.mark.asyncio
async def test_thresholds_are_the_documented_values() -> None:
    """Pin the exact threshold values -- a silent change here would be a real product-risk
    regression (looser thresholds = more false-positive-prone alerts to real sellers)."""
    assert BATCH_DEFECT_ALERT_THRESHOLD == 0.7
    assert FAKE_CAMPAIGN_ALERT_THRESHOLD == 0.5
