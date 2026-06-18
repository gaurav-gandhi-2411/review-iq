"""Unit tests for GET /v2/insights/health-score.

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
    _BAND_HEALTHY,
    _BAND_NEEDS_ATTENTION,
    _FORMULA_VERSION,
    _W_A,
    _W_S,
    _W_U,
    _assign_band,
    _assign_confidence,
)
from app.auth.api_key import ApiKeyContext, require_api_key

# ---------------------------------------------------------------------------
# Shared fixtures
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

# Raw dict that mirrors what health_score_pg returns for a well-populated org.
# Values chosen so computed scores are easy to verify by hand:
#   S = 8/20 = 0.40, U = 1 - 4/20 = 0.80, A = 1 - 2/10 = 0.80
#   score = 0.50*0.40 + 0.20*0.80 + 0.30*0.80 = 0.20 + 0.16 + 0.24 = 0.60
_RAW_FULL: dict[str, Any] = {
    "total_extractions": 20,
    "positive_count": 8,
    "negative_count": 7,
    "neutral_count": 3,
    "mixed_count": 2,
    "high_urgency_count": 4,
    "medium_urgency_count": 6,
    "low_urgency_count": 10,
    "total_audited": 10,
    "likely_fake_count": 2,
}

# Empty org — no extractions, no audits.
_RAW_EMPTY: dict[str, Any] = {
    "total_extractions": 0,
    "positive_count": 0,
    "negative_count": 0,
    "neutral_count": 0,
    "mixed_count": 0,
    "high_urgency_count": 0,
    "medium_urgency_count": 0,
    "low_urgency_count": 0,
    "total_audited": 0,
    "likely_fake_count": 0,
}

# No audits yet — authenticity score must be 1.0.
_RAW_NO_AUDITS: dict[str, Any] = {
    "total_extractions": 15,
    "positive_count": 10,
    "negative_count": 3,
    "neutral_count": 1,
    "mixed_count": 1,
    "high_urgency_count": 2,
    "medium_urgency_count": 4,
    "low_urgency_count": 9,
    "total_audited": 0,
    "likely_fake_count": 0,
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
# Golden-input formula pin — any weight or formula change breaks this test.
# ---------------------------------------------------------------------------


class TestHealthScoreFormulaGolden:
    def test_formula_golden_input(self) -> None:
        """Pin the health-score formula — weights (0.50/0.20/0.30) and computation."""
        # S=0.50, U=0.80, A=0.90 → 0.50*0.50 + 0.20*0.80 + 0.30*0.90
        s, u, a = 0.50, 0.80, 0.90
        expected = round(_W_S * s + _W_U * u + _W_A * a, 4)
        assert expected == 0.6800  # 0.25 + 0.16 + 0.27

    def test_weights_sum_to_one(self) -> None:
        assert round(_W_S + _W_U + _W_A, 10) == 1.0

    def test_band_thresholds_ordered(self) -> None:
        assert _BAND_HEALTHY > _BAND_NEEDS_ATTENTION > 0.0


# ---------------------------------------------------------------------------
# Band assignment
# ---------------------------------------------------------------------------


class TestBandAssignment:
    def test_healthy(self) -> None:
        assert _assign_band(1.00) == "healthy"
        assert _assign_band(0.75) == "healthy"
        assert _assign_band(0.80) == "healthy"

    def test_needs_attention(self) -> None:
        assert _assign_band(0.74) == "needs_attention"
        assert _assign_band(0.63) == "needs_attention"
        assert _assign_band(0.50) == "needs_attention"

    def test_at_risk(self) -> None:
        assert _assign_band(0.49) == "at_risk"
        assert _assign_band(0.44) == "at_risk"
        assert _assign_band(0.00) == "at_risk"


# ---------------------------------------------------------------------------
# Confidence assignment
# ---------------------------------------------------------------------------


class TestConfidenceAssignment:
    def test_low(self) -> None:
        assert _assign_confidence(0) == "low"
        assert _assign_confidence(9) == "low"

    def test_medium(self) -> None:
        assert _assign_confidence(10) == "medium"
        assert _assign_confidence(49) == "medium"

    def test_high(self) -> None:
        assert _assign_confidence(50) == "high"
        assert _assign_confidence(200) == "high"


# ---------------------------------------------------------------------------
# Happy path — full data
# ---------------------------------------------------------------------------


class TestHealthScoreHappyPath:
    async def test_returns_200(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            resp = await client.get("/v2/insights/health-score")
        assert resp.status_code == 200

    async def test_org_id_echoed(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["org_id"] == _ORG_ID

    async def test_response_shape_complete(self, client: httpx.AsyncClient) -> None:
        """All required top-level keys present."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()

        required = {
            "org_id",
            "window",
            "total_extractions",
            "components",
            "authenticity_coverage",
            "score",
            "band",
            "confidence",
            "formula_version",
            "moderation_note",
        }
        assert required.issubset(data.keys())

    async def test_component_keys(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()

        c = data["components"]
        assert set(c.keys()) == {"sentiment", "urgency", "authenticity"}
        assert set(c["sentiment"].keys()) == {"score", "positive_count", "total", "weight"}
        assert set(c["urgency"].keys()) == {"score", "high_urgency_count", "total", "weight"}
        assert set(c["authenticity"].keys()) == {
            "score",
            "priority_review_count",
            "total_audited",
            "weight",
        }

    async def test_score_computed_correctly(self, client: httpx.AsyncClient) -> None:
        """Verify numeric computation against hand-calculated values."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()

        # S=8/20=0.40, U=1-4/20=0.80, A=1-2/10=0.80
        assert data["components"]["sentiment"]["score"] == pytest.approx(0.40, abs=1e-4)
        assert data["components"]["urgency"]["score"] == pytest.approx(0.80, abs=1e-4)
        assert data["components"]["authenticity"]["score"] == pytest.approx(0.80, abs=1e-4)
        # 0.50*0.40 + 0.20*0.80 + 0.30*0.80 = 0.20 + 0.16 + 0.24 = 0.60
        assert data["score"] == pytest.approx(0.60, abs=1e-4)
        assert data["band"] == "needs_attention"

    async def test_raw_counts_reported(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()

        assert data["total_extractions"] == 20
        assert data["components"]["sentiment"]["positive_count"] == 8
        assert data["components"]["urgency"]["high_urgency_count"] == 4
        assert data["components"]["authenticity"]["priority_review_count"] == 2
        assert data["components"]["authenticity"]["total_audited"] == 10

    async def test_authenticity_coverage(self, client: httpx.AsyncClient) -> None:
        # 10 audited / 20 extractions = 0.50
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["authenticity_coverage"] == pytest.approx(0.50, abs=1e-6)

    async def test_formula_version_present(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["formula_version"] == _FORMULA_VERSION

    async def test_moderation_note_present(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert "individual review" in data["moderation_note"]

    async def test_window_contains_days(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["window"]["days"] == 30
        assert data["window"]["since"] is not None

    async def test_confidence_medium(self, client: httpx.AsyncClient) -> None:
        # 20 extractions → medium
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["confidence"] == "medium"


# ---------------------------------------------------------------------------
# A = 1.0 when total_audited = 0
# ---------------------------------------------------------------------------


class TestAuthenticityScoreWhenUnaudited:
    async def test_a_score_is_one_when_no_audits(self, client: httpx.AsyncClient) -> None:
        """Spec: A = 1.0 when total_audited = 0 (no audits yet)."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_NO_AUDITS):
            data = (await client.get("/v2/insights/health-score")).json()

        assert data["components"]["authenticity"]["score"] == pytest.approx(1.0, abs=1e-6)
        assert data["components"]["authenticity"]["total_audited"] == 0
        assert data["components"]["authenticity"]["priority_review_count"] == 0

    async def test_coverage_zero_when_no_audits(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_NO_AUDITS):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["authenticity_coverage"] == pytest.approx(0.0, abs=1e-6)

    async def test_score_uses_a_one_correctly(self, client: httpx.AsyncClient) -> None:
        """S=10/15≈0.667, U=1-2/15≈0.867, A=1.0 → score≈0.693."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_NO_AUDITS):
            data = (await client.get("/v2/insights/health-score")).json()

        s = 10 / 15
        u = 1 - 2 / 15
        a = 1.0
        expected = round(0.50 * s + 0.20 * u + 0.30 * a, 4)
        assert data["score"] == pytest.approx(expected, abs=1e-4)


# ---------------------------------------------------------------------------
# Empty org (zero extractions)
# ---------------------------------------------------------------------------


class TestEmptyOrg:
    async def test_returns_200_for_empty_org(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_EMPTY):
            resp = await client.get("/v2/insights/health-score")
        assert resp.status_code == 200

    async def test_score_is_point_five_for_empty_org(self, client: httpx.AsyncClient) -> None:
        """Empty org: S=0.0, U=1.0, A=1.0 → score = 0.0+0.20+0.30 = 0.50."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_EMPTY):
            data = (await client.get("/v2/insights/health-score")).json()

        assert data["score"] == pytest.approx(0.50, abs=1e-6)
        assert data["band"] == "needs_attention"

    async def test_confidence_low_for_empty_org(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_EMPTY):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["confidence"] == "low"

    async def test_coverage_zero_for_empty_org(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_EMPTY):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["authenticity_coverage"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# No raw stored labels in response (precision-first guard)
# ---------------------------------------------------------------------------


class TestPrecisionFirstGuard:
    async def test_raw_labels_absent_from_response(self, client: httpx.AsyncClient) -> None:
        """Stored labels (genuine, suspicious, likely_fake) must not appear in the response."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()

        response_str = str(data)
        for forbidden in ("likely_fake", "suspicious", "genuine"):
            assert forbidden not in response_str, f"Raw label {forbidden!r} leaked into response"


# ---------------------------------------------------------------------------
# Window / query-param tests
# ---------------------------------------------------------------------------


class TestWindowParams:
    async def test_default_days_30(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["window"]["days"] == 30

    async def test_custom_days_param(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score?days=60")).json()
        assert data["window"]["days"] == 60

    async def test_since_override_passed_to_storage(self, client: httpx.AsyncClient) -> None:
        """When since is provided, storage is called with that exact value."""
        since_str = "2024-01-01T00:00:00"
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL) as mock_fn:
            await client.get(f"/v2/insights/health-score?since={since_str}")

        called_since = mock_fn.call_args.args[1]
        assert called_since == datetime(2024, 1, 1, 0, 0, 0)

    async def test_until_in_response_window(self, client: httpx.AsyncClient) -> None:
        until_str = "2024-06-01T00:00:00"
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get(f"/v2/insights/health-score?until={until_str}")).json()
        assert data["window"]["until"] == "2024-06-01T00:00:00"

    async def test_until_none_when_not_provided(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["window"]["until"] is None

    async def test_days_out_of_range_returns_422(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/v2/insights/health-score?days=0")
        assert resp.status_code == 422

        resp = await client.get("/v2/insights/health-score?days=366")
        assert resp.status_code == 422
