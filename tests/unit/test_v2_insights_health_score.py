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
    _NO_DATA_SCORE,
    _W_S,
    _W_U,
    _assign_band,
    _assign_confidence,
    compute_health_score,
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
#   S = 8/20 = 0.40, U = 1 - 4/20 = 0.80
#   score = 5/7*0.40 + 2/7*0.80 = 0.2857 + 0.2286 = 0.5143
_RAW_FULL: dict[str, Any] = {
    "total_extractions": 20,
    "positive_count": 8,
    "negative_count": 7,
    "neutral_count": 3,
    "mixed_count": 2,
    "high_urgency_count": 4,
    "medium_urgency_count": 6,
    "low_urgency_count": 10,
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
}

# Sentiment/urgency-only counts, 15 extractions.
_RAW_MIXED: dict[str, Any] = {
    "total_extractions": 15,
    "positive_count": 10,
    "negative_count": 3,
    "neutral_count": 1,
    "mixed_count": 1,
    "high_urgency_count": 2,
    "medium_urgency_count": 4,
    "low_urgency_count": 9,
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
        """Pin the health-score formula — weights (5/7, 2/7) and computation."""
        # S=0.50, U=0.80 → 5/7*0.50 + 2/7*0.80 = 0.35714 + 0.22857
        s, u = 0.50, 0.80
        expected = round(_W_S * s + _W_U * u, 4)
        assert expected == 0.5857

    def test_weights_sum_to_one(self) -> None:
        assert round(_W_S + _W_U, 10) == 1.0

    def test_score_spans_zero_to_one(self) -> None:
        """Re-normalisation guard: worst input scores 0, best input scores 1."""
        worst = {"total_extractions": 10, "positive_count": 0, "high_urgency_count": 10}
        best = {"total_extractions": 10, "positive_count": 10, "high_urgency_count": 0}
        assert compute_health_score(worst)[2] == 0.0
        assert compute_health_score(best)[2] == 1.0

    def test_score_ignores_authenticity_inputs(self) -> None:
        """The score no longer depends on any authenticity count (W6): extra audit keys in the
        raw dict change nothing, and the formula version records the removal."""
        base = {"total_extractions": 20, "positive_count": 8, "high_urgency_count": 4}
        noisy = {**base, "total_audited": 20, "likely_fake_count": 20}
        assert compute_health_score(noisy) == compute_health_score(base)
        assert _FORMULA_VERSION == "2.0"

    def test_band_semantics_after_renormalisation(self) -> None:
        """needs_attention = mid sentiment (30-67% positive at no high urgency); healthy needs
        >= 2/3 positive at no high urgency; at_risk is reachable (<30% positive)."""

        def band(pos: int, high: int) -> str:
            raw = {"total_extractions": 100, "positive_count": pos, "high_urgency_count": high}
            return _assign_band(compute_health_score(raw)[2])

        assert band(pos=80, high=0) == "healthy"
        assert band(pos=50, high=0) == "needs_attention"
        assert band(pos=20, high=0) == "at_risk"
        assert band(pos=0, high=100) == "at_risk"

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
        assert set(c.keys()) == {"sentiment", "urgency"}
        assert set(c["sentiment"].keys()) == {"score", "positive_count", "total", "weight"}
        assert set(c["urgency"].keys()) == {"score", "high_urgency_count", "total", "weight"}

    async def test_score_computed_correctly(self, client: httpx.AsyncClient) -> None:
        """Verify numeric computation against hand-calculated values."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()

        # S=8/20=0.40, U=1-4/20=0.80
        assert data["components"]["sentiment"]["score"] == pytest.approx(0.40, abs=1e-4)
        assert data["components"]["urgency"]["score"] == pytest.approx(0.80, abs=1e-4)
        # 5/7*0.40 + 2/7*0.80 = 0.5143
        assert data["score"] == pytest.approx(0.5143, abs=1e-4)
        assert data["band"] == "needs_attention"
        assert data["components"]["sentiment"]["weight"] == pytest.approx(0.7143, abs=1e-4)
        assert data["components"]["urgency"]["weight"] == pytest.approx(0.2857, abs=1e-4)

    async def test_raw_counts_reported(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_FULL):
            data = (await client.get("/v2/insights/health-score")).json()

        assert data["total_extractions"] == 20
        assert data["components"]["sentiment"]["positive_count"] == 8
        assert data["components"]["urgency"]["high_urgency_count"] == 4

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
# Authenticity is gone from the health-score response (W6)
# ---------------------------------------------------------------------------


class TestNoAuthenticityInResponse:
    async def test_no_authenticity_keys_or_words(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_MIXED):
            data = (await client.get("/v2/insights/health-score")).json()
        assert "authenticity_coverage" not in data
        assert "authenticity" not in data["components"]
        assert "authenticity" not in str(data).lower()

    async def test_score_from_sentiment_and_urgency_only(self, client: httpx.AsyncClient) -> None:
        """S=10/15, U=1-2/15 -> 5/7*S + 2/7*U."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_MIXED):
            data = (await client.get("/v2/insights/health-score")).json()
        expected = round(5 / 7 * (10 / 15) + 2 / 7 * (1 - 2 / 15), 4)
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
        """Empty org: explicit neutral midpoint (not S=0 -> at_risk), confidence low."""
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_EMPTY):
            data = (await client.get("/v2/insights/health-score")).json()

        assert data["score"] == pytest.approx(_NO_DATA_SCORE, abs=1e-6)
        assert data["band"] == "needs_attention"

    async def test_confidence_low_for_empty_org(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_EMPTY):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["confidence"] == "low"


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


# ---------------------------------------------------------------------------
# Band must be assigned from the UNROUNDED score (S17 X5b). 466 positive of 717, zero high
# urgency -> exact score 0.749950 (< 0.75, needs_attention); round(..., 4) gives 0.7500 and the
# band used to flip to healthy while the exact score said otherwise.
# ---------------------------------------------------------------------------

_RAW_JUST_BELOW_HEALTHY: dict[str, Any] = {
    "total_extractions": 717,
    "positive_count": 466,
    "high_urgency_count": 0,
}


class TestBandUsesUnroundedScore:
    async def test_v2_band_not_flipped_by_display_rounding(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.health_score_pg", return_value=_RAW_JUST_BELOW_HEALTHY):
            data = (await client.get("/v2/insights/health-score")).json()
        assert data["score"] == 0.75  # display value is rounded...
        assert data["band"] == "needs_attention"  # ...the band is not derived from it

    def test_health_band_exact_at_extremes_and_near_miss(self) -> None:
        from app.api.v2.insights import health_band

        assert health_band(_RAW_JUST_BELOW_HEALTHY) == "needs_attention"
        assert health_band({"total_extractions": 0}) == "needs_attention"
        at_top = {"total_extractions": 10, "positive_count": 10, "high_urgency_count": 0}
        assert health_band(at_top) == "healthy"
        at_bottom = {"total_extractions": 10, "positive_count": 0, "high_urgency_count": 10}
        assert health_band(at_bottom) == "at_risk"
