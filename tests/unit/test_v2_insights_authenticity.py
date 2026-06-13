"""Unit tests for GET /v2/insights/authenticity.

All storage and auth calls are mocked — no live DB connection.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from app.api.v2.insights import (
    DISPOSITION_DISPLAY,
    SIGNAL_DISPLAY,
    _map_disposition,
    _map_signal,
    _safe_rate,
)
from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityLabel

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)

# A representative raw summary dict that mirrors what storage_pg returns.
_RAW_SUMMARY: dict[str, Any] = {
    "total_audited": 10,
    "label_genuine": 6,
    "label_suspicious": 3,
    "label_likely_fake": 1,
    "mean_score": 0.72,
    "flag_frequency": [
        {"flag": "rating_text_mismatch", "count": 4},
        {"flag": "excessive_brevity", "count": 2},
    ],
    "time_series": [
        {"period": datetime(2024, 1, 1), "audited": 5, "flagged": 2},
        {"period": datetime(2024, 1, 8), "audited": 5, "flagged": 2},
    ],
}

# Empty-org raw summary — simulates a brand-new tenant with no audits.
_EMPTY_SUMMARY: dict[str, Any] = {
    "total_audited": 0,
    "label_genuine": 0,
    "label_suspicious": 0,
    "label_likely_fake": 0,
    "mean_score": None,
    "flag_frequency": [],
    "time_series": [],
}


@pytest.fixture()
async def client() -> httpx.AsyncClient:
    """Async HTTP test client with require_api_key bypassed."""
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: _CTX
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test 1: Happy-path — 200, correct org_id, full response shape
# ---------------------------------------------------------------------------


class TestAuthenticityInsightsHappyPath:
    async def test_returns_200_and_org_id(self, client: httpx.AsyncClient) -> None:
        """Storage returns known data; endpoint returns 200 and echoes org_id."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            resp = await client.get("/v2/insights/authenticity")

        assert resp.status_code == 200
        data = resp.json()
        assert data["org_id"] == _ORG_ID

    async def test_response_shape_complete(self, client: httpx.AsyncClient) -> None:
        """All required top-level keys are present in the response."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        required_keys = {
            "org_id",
            "window",
            "total_audited",
            "dispositions",
            "disposition_rates",
            "review_flag_rate",
            "mean_authenticity_score",
            "signal_frequency",
            "flag_rate_series",
            "moderation_note",
        }
        assert required_keys.issubset(data.keys())

    async def test_disposition_counts_correct(self, client: httpx.AsyncClient) -> None:
        """Mapped disposition counts match raw label counts from storage."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        assert data["dispositions"]["clear"] == 6
        assert data["dispositions"]["flagged_for_review"] == 3
        assert data["dispositions"]["priority_review"] == 1
        assert data["total_audited"] == 10

    async def test_review_flag_rate_computed(self, client: httpx.AsyncClient) -> None:
        """review_flag_rate = (suspicious + likely_fake) / total."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        # (3 + 1) / 10 = 0.4
        assert abs(data["review_flag_rate"] - 0.4) < 1e-6

    async def test_mean_score_passed_through(self, client: httpx.AsyncClient) -> None:
        """mean_authenticity_score matches storage value."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        assert abs(data["mean_authenticity_score"] - 0.72) < 1e-6

    async def test_signal_frequency_mapped(self, client: httpx.AsyncClient) -> None:
        """Stored flag names are mapped through SIGNAL_DISPLAY."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        signals = {entry["signal"]: entry["count"] for entry in data["signal_frequency"]}
        assert signals["rating_text_mismatch"] == 4
        assert signals["very_short"] == 2

    async def test_flag_rate_series_structure(self, client: httpx.AsyncClient) -> None:
        """Time series entries have period, review_flag_rate, and audited keys."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        series = data["flag_rate_series"]
        assert len(series) == 2
        for entry in series:
            assert "period" in entry
            assert "review_flag_rate" in entry
            assert "audited" in entry
        # 2/5 = 0.4 per bucket
        assert abs(series[0]["review_flag_rate"] - 0.4) < 1e-6

    async def test_window_echoed(self, client: httpx.AsyncClient) -> None:
        """Window object reflects since, until, and bucket params."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            resp = await client.get(
                "/v2/insights/authenticity",
                params={"since": "2024-01-01T00:00:00", "bucket": "day"},
            )

        data = resp.json()
        assert data["window"]["bucket"] == "day"
        assert data["window"]["since"] is not None
        assert data["window"]["until"] is None


# ---------------------------------------------------------------------------
# Test 2: HARD precision-first guard — no forbidden substrings in response
# ---------------------------------------------------------------------------


class TestPrecisionFirstGuard:
    """
    Legal / precision guard: the serialised response must never contain
    the raw stored labels 'fake', 'genuine', or 'suspicious'.

    This test is intentionally named to be unmistakable in CI output.
    """

    async def test_response_contains_no_verdict_language(self, client: httpx.AsyncClient) -> None:
        """HARD GUARD: 'fake', 'genuine', 'suspicious' must not appear anywhere in response JSON."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            resp = await client.get("/v2/insights/authenticity")

        body_text = resp.text.lower()
        assert "fake" not in body_text, (
            "Response contains forbidden substring 'fake' — precision-first contract violated"
        )
        assert "genuine" not in body_text, (
            "Response contains forbidden substring 'genuine' — precision-first contract violated"
        )
        assert "suspicious" not in body_text, (
            "Response contains forbidden substring 'suspicious' — precision-first contract violated"
        )

    async def test_response_contains_mapped_disposition_terms(
        self, client: httpx.AsyncClient
    ) -> None:
        """Mapped disposition keys (clear / flagged_for_review / priority_review) must appear."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            resp = await client.get("/v2/insights/authenticity")

        body_text = resp.text
        assert "clear" in body_text
        assert "flagged_for_review" in body_text
        assert "priority_review" in body_text

    async def test_moderation_note_does_not_contain_verdict_language(
        self, client: httpx.AsyncClient
    ) -> None:
        """The moderation_note field itself must not contain verdict language."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_RAW_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        note = data["moderation_note"].lower()
        assert "fake" not in note
        assert "genuine" not in note
        assert "suspicious" not in note


# ---------------------------------------------------------------------------
# Test 3: Mapping unit tests
# ---------------------------------------------------------------------------


class TestMappingConstants:
    def test_disposition_display_covers_all_labels(self) -> None:
        """Every AuthenticityLabel value maps to a non-verdict string."""
        verdict_words = {"fake", "genuine", "suspicious"}
        for label in AuthenticityLabel:
            mapped = DISPOSITION_DISPLAY.get(label.value)
            assert mapped is not None, (
                f"AuthenticityLabel.{label.name} missing from DISPOSITION_DISPLAY"
            )
            assert mapped.lower() not in verdict_words, f"Mapped value {mapped!r} is a verdict word"

    def test_signal_display_covers_all_flags(self) -> None:
        """Every AuthenticityFlag value maps to a non-verdict string."""
        verdict_words = {"fake", "genuine", "suspicious"}
        for flag in AuthenticityFlag:
            mapped = SIGNAL_DISPLAY.get(flag.value)
            assert mapped is not None, f"AuthenticityFlag.{flag.name} missing from SIGNAL_DISPLAY"
            assert mapped.lower() not in verdict_words, f"Mapped value {mapped!r} is a verdict word"

    def test_map_disposition_unknown_falls_back_to_review(self) -> None:
        """Unknown label hits the safe fallback 'review', not a raw stored value."""
        result = _map_disposition("unknown_future_label")
        assert result == "review"
        assert result not in {"fake", "genuine", "suspicious"}

    def test_map_signal_unknown_falls_back_to_other_signal(self) -> None:
        """Unknown flag hits the safe fallback 'other_signal', not a raw stored value."""
        result = _map_signal("future_unknown_flag")
        assert result == "other_signal"

    def test_disposition_display_values_are_unique(self) -> None:
        """Each raw label maps to a distinct display string."""
        values = list(DISPOSITION_DISPLAY.values())
        assert len(values) == len(set(values)), "DISPOSITION_DISPLAY has duplicate mapped values"

    def test_signal_display_values_cover_expected_flags(self) -> None:
        """SIGNAL_DISPLAY is non-empty and covers all 8 known flag values."""
        known_flags = {f.value for f in AuthenticityFlag}
        covered = set(SIGNAL_DISPLAY.keys())
        assert known_flags == covered, f"SIGNAL_DISPLAY is missing flags: {known_flags - covered}"


# ---------------------------------------------------------------------------
# Test 4: Empty-org — no divide-by-zero, all rates 0.0
# ---------------------------------------------------------------------------


class TestEmptyOrg:
    async def test_empty_org_returns_200(self, client: httpx.AsyncClient) -> None:
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_EMPTY_SUMMARY,
        ):
            resp = await client.get("/v2/insights/authenticity")
        assert resp.status_code == 200

    async def test_empty_org_rates_are_zero(self, client: httpx.AsyncClient) -> None:
        """All rates are 0.0 when total_audited = 0 — no ZeroDivisionError."""
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_EMPTY_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        assert data["total_audited"] == 0
        assert data["review_flag_rate"] == 0.0
        assert data["disposition_rates"]["clear"] == 0.0
        assert data["disposition_rates"]["flagged_for_review"] == 0.0
        assert data["disposition_rates"]["priority_review"] == 0.0
        assert data["mean_authenticity_score"] is None

    async def test_empty_org_series_is_empty_list(self, client: httpx.AsyncClient) -> None:
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_EMPTY_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity")).json()

        assert data["flag_rate_series"] == []
        assert data["signal_frequency"] == []

    def test_safe_rate_zero_denominator(self) -> None:
        """_safe_rate returns 0.0 without raising ZeroDivisionError."""
        assert _safe_rate(5, 0) == 0.0

    def test_safe_rate_normal_case(self) -> None:
        assert abs(_safe_rate(1, 4) - 0.25) < 1e-9


# ---------------------------------------------------------------------------
# Test 5: Invalid bucket → 422
# ---------------------------------------------------------------------------


class TestInvalidBucket:
    async def test_invalid_bucket_returns_422(self, client: httpx.AsyncClient) -> None:
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_EMPTY_SUMMARY,
        ):
            resp = await client.get("/v2/insights/authenticity?bucket=biweekly")

        assert resp.status_code == 422

    async def test_invalid_bucket_error_message(self, client: httpx.AsyncClient) -> None:
        with patch(
            "app.api.v2.insights.authenticity_audit_summary_pg",
            return_value=_EMPTY_SUMMARY,
        ):
            data = (await client.get("/v2/insights/authenticity?bucket=invalid")).json()

        detail = data["detail"].lower()
        # Must mention 'bucket' so the caller knows which param is wrong
        assert "bucket" in detail

    async def test_valid_buckets_accepted(self, client: httpx.AsyncClient) -> None:
        """day, week, and month are all accepted without 422."""
        for valid_bucket in ("day", "week", "month"):
            with patch(
                "app.api.v2.insights.authenticity_audit_summary_pg",
                return_value=_EMPTY_SUMMARY,
            ):
                resp = await client.get(f"/v2/insights/authenticity?bucket={valid_bucket}")
            assert resp.status_code == 200, (
                f"bucket={valid_bucket!r} unexpectedly returned {resp.status_code}"
            )
