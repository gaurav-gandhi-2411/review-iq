"""Unit tests for GET /v2/insights/trends.

All storage and auth calls are mocked — no live DB connection, no real LLM calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from app.api.v2.insights import _VALID_TREND_OF, _compute_delta
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

# Representative raw return value from theme_trends_pg with two themes and
# three periods so we can verify ordering, series, delta, and by_language.
_DT_W1 = datetime(2024, 1, 1)
_DT_W2 = datetime(2024, 1, 8)
_DT_W3 = datetime(2024, 1, 15)

_RAW_TRENDS: dict[str, Any] = {
    "themes": [
        {
            "theme": "delivery",
            "total": 12,
            "sorted_periods": [_DT_W1, _DT_W2, _DT_W3],
            "by_period": {
                _DT_W1: {"en": 3, "hi-en": 2},  # 5 total
                _DT_W2: {"en": 4, "hi": 1},  # 5 total
                _DT_W3: {"en": 1, "hi-en": 1},  # 2 total  (latest)
            },
            "by_language": {"en": 8, "hi-en": 3, "hi": 1},
        },
        {
            "theme": "packaging",
            "total": 6,
            "sorted_periods": [_DT_W1, _DT_W2],
            "by_period": {
                _DT_W1: {"en": 2},  # 2
                _DT_W2: {"en": 4},  # 4 (latest)
            },
            "by_language": {"en": 6},
        },
    ]
}

# Empty org — no data at all.
_RAW_EMPTY: dict[str, Any] = {"themes": []}

# Single-bucket org — only one period present (delta must be 0, pct_change None).
_DT_ONLY = datetime(2024, 3, 1)
_RAW_SINGLE_BUCKET: dict[str, Any] = {
    "themes": [
        {
            "theme": "returns",
            "total": 5,
            "sorted_periods": [_DT_ONLY],
            "by_period": {_DT_ONLY: {"en": 5}},
            "by_language": {"en": 5},
        }
    ]
}

# Two-bucket case where the prior bucket count is 0 → pct_change must be None.
_DT_P1 = datetime(2024, 4, 1)
_DT_P2 = datetime(2024, 4, 8)
_RAW_PRIOR_ZERO: dict[str, Any] = {
    "themes": [
        {
            "theme": "noise",
            "total": 3,
            "sorted_periods": [_DT_P1, _DT_P2],
            "by_period": {
                _DT_P1: {},  # 0 counts in this period
                _DT_P2: {"en": 3},  # 3
            },
            "by_language": {"en": 3},
        }
    ]
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
# Test 1: Happy-path — 200, org_id echoed, full response shape
# ---------------------------------------------------------------------------


class TestTrendsHappyPath:
    async def test_returns_200(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            resp = await client.get("/v2/insights/trends")
        assert resp.status_code == 200

    async def test_org_id_echoed(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()
        assert data["org_id"] == _ORG_ID

    async def test_full_response_shape(self, client: httpx.AsyncClient) -> None:
        """Required top-level keys are all present."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        assert {"org_id", "window", "filters", "themes"}.issubset(data.keys())
        assert "since" in data["window"]
        assert "until" in data["window"]
        assert "bucket" in data["window"]
        assert "trend_of" in data["window"]
        assert "product" in data["filters"]
        assert "language" in data["filters"]

    async def test_themes_ordered_by_total_desc(self, client: httpx.AsyncClient) -> None:
        """Themes must be returned in descending order of total count."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        totals = [t["total"] for t in data["themes"]]
        assert totals == sorted(totals, reverse=True)

    async def test_theme_shape_complete(self, client: httpx.AsyncClient) -> None:
        """Each theme object has theme, total, series, delta_last, pct_change, by_language."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        for theme_obj in data["themes"]:
            assert "theme" in theme_obj
            assert "total" in theme_obj
            assert "series" in theme_obj
            assert "delta_last" in theme_obj
            assert "pct_change" in theme_obj
            assert "by_language" in theme_obj

    async def test_by_language_present(self, client: httpx.AsyncClient) -> None:
        """by_language is a non-empty dict for the top theme."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        delivery = data["themes"][0]
        assert delivery["by_language"] == {"en": 8, "hi-en": 3, "hi": 1}

    async def test_series_is_chronological(self, client: httpx.AsyncClient) -> None:
        """Series entries are ordered chronologically (ascending period)."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        periods = [e["period"] for e in data["themes"][0]["series"]]
        assert periods == sorted(periods)

    async def test_series_count_is_sum_across_languages(self, client: httpx.AsyncClient) -> None:
        """series[i].count == sum of all language counts in that period."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        # "delivery" theme:
        # W1: en=3, hi-en=2 → 5;  W2: en=4, hi=1 → 5;  W3: en=1, hi-en=1 → 2
        delivery_series = data["themes"][0]["series"]
        assert delivery_series[0]["count"] == 5
        assert delivery_series[1]["count"] == 5
        assert delivery_series[2]["count"] == 2

    async def test_delta_last_correct(self, client: httpx.AsyncClient) -> None:
        """delta_last = latest bucket count - prior bucket count."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        # delivery: latest=2, prior=5 → delta=-3
        delivery = data["themes"][0]
        assert delivery["delta_last"] == -3

        # packaging: latest=4, prior=2 → delta=2
        packaging = data["themes"][1]
        assert packaging["delta_last"] == 2

    async def test_pct_change_correct(self, client: httpx.AsyncClient) -> None:
        """pct_change = (latest - prior) / prior, rounded to 6dp."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            data = (await client.get("/v2/insights/trends")).json()

        # packaging: (4 - 2) / 2 = 1.0
        packaging = data["themes"][1]
        assert abs(packaging["pct_change"] - 1.0) < 1e-6

    async def test_window_echoed(self, client: httpx.AsyncClient) -> None:
        """Window params since/until/bucket/trend_of are echoed accurately."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_TRENDS):
            resp = await client.get(
                "/v2/insights/trends",
                params={
                    "since": "2024-01-01T00:00:00",
                    "until": "2024-02-01T00:00:00",
                    "bucket": "day",
                    "trend_of": "cons",
                },
            )
        data = resp.json()
        assert data["window"]["bucket"] == "day"
        assert data["window"]["trend_of"] == "cons"
        assert data["window"]["since"] is not None
        assert data["window"]["until"] is not None


# ---------------------------------------------------------------------------
# Test 2: trend_of and bucket validation → 422
# ---------------------------------------------------------------------------


class TestValidation:
    async def test_invalid_trend_of_returns_422(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/v2/insights/trends?trend_of=titles")
        assert resp.status_code == 422

    async def test_invalid_trend_of_error_mentions_param(self, client: httpx.AsyncClient) -> None:
        data = (await client.get("/v2/insights/trends?trend_of=titles")).json()
        assert "trend_of" in data["detail"].lower()

    async def test_invalid_bucket_returns_422(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/v2/insights/trends?bucket=hour")
        assert resp.status_code == 422

    async def test_invalid_bucket_error_mentions_param(self, client: httpx.AsyncClient) -> None:
        data = (await client.get("/v2/insights/trends?bucket=hour")).json()
        assert "bucket" in data["detail"].lower()

    async def test_valid_trend_of_topics_accepted(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY):
            resp = await client.get("/v2/insights/trends?trend_of=topics")
        assert resp.status_code == 200

    async def test_valid_trend_of_cons_accepted(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY):
            resp = await client.get("/v2/insights/trends?trend_of=cons")
        assert resp.status_code == 200

    async def test_valid_buckets_all_accepted(self, client: httpx.AsyncClient) -> None:
        for b in ("day", "week", "month"):
            with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY):
                resp = await client.get(f"/v2/insights/trends?bucket={b}")
            assert resp.status_code == 200, f"bucket={b!r} returned {resp.status_code}"

    def test_valid_trend_of_constant_covers_topics_and_cons(self) -> None:
        """_VALID_TREND_OF must contain exactly 'topics' and 'cons'."""
        assert frozenset({"topics", "cons"}) == _VALID_TREND_OF


# ---------------------------------------------------------------------------
# Test 3: Filter params reach the storage call
# ---------------------------------------------------------------------------


class TestFiltersPassedThrough:
    async def test_product_and_language_reach_storage(self, client: httpx.AsyncClient) -> None:
        """product and language query params are forwarded to theme_trends_pg."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY) as mock_fn:
            await client.get(
                "/v2/insights/trends",
                params={"product": "widget", "language": "hi-en"},
            )

        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs["product"] == "widget"
        assert kwargs["language"] == "hi-en"

    async def test_since_until_bucket_reach_storage(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY) as mock_fn:
            await client.get(
                "/v2/insights/trends",
                params={
                    "since": "2024-01-01T00:00:00",
                    "until": "2024-03-01T00:00:00",
                    "bucket": "month",
                },
            )

        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs["bucket"] == "month"
        assert kwargs["since"] is not None
        assert kwargs["until"] is not None

    async def test_limit_reaches_storage(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY) as mock_fn:
            await client.get("/v2/insights/trends?limit=25")

        _, kwargs = mock_fn.call_args
        assert kwargs["limit"] == 25

    async def test_filters_echoed_in_response(self, client: httpx.AsyncClient) -> None:
        """product and language appear in the response filters block."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY):
            data = (
                await client.get(
                    "/v2/insights/trends",
                    params={"product": "gadget", "language": "hi"},
                )
            ).json()

        assert data["filters"]["product"] == "gadget"
        assert data["filters"]["language"] == "hi"


# ---------------------------------------------------------------------------
# Test 4: Empty org → 200, themes []
# ---------------------------------------------------------------------------


class TestEmptyOrg:
    async def test_empty_org_returns_200(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY):
            resp = await client.get("/v2/insights/trends")
        assert resp.status_code == 200

    async def test_empty_org_themes_is_empty_list(self, client: httpx.AsyncClient) -> None:
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_EMPTY):
            data = (await client.get("/v2/insights/trends")).json()
        assert data["themes"] == []


# ---------------------------------------------------------------------------
# Test 5: Edge-cases — single bucket, prior==0
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_single_bucket_delta_is_zero(self, client: httpx.AsyncClient) -> None:
        """When there is only one period, delta_last must be 0."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_SINGLE_BUCKET):
            data = (await client.get("/v2/insights/trends")).json()

        assert data["themes"][0]["delta_last"] == 0

    async def test_single_bucket_pct_change_is_none(self, client: httpx.AsyncClient) -> None:
        """When there is only one period, pct_change must be null (no prior bucket)."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_SINGLE_BUCKET):
            data = (await client.get("/v2/insights/trends")).json()

        assert data["themes"][0]["pct_change"] is None

    async def test_prior_zero_pct_change_is_none(self, client: httpx.AsyncClient) -> None:
        """When prior bucket count is 0, pct_change must be null to avoid division by zero."""
        with patch("app.api.v2.insights.theme_trends_pg", return_value=_RAW_PRIOR_ZERO):
            data = (await client.get("/v2/insights/trends")).json()

        theme_obj = data["themes"][0]
        # prior=0, latest=3 → delta=3, pct_change=null
        assert theme_obj["delta_last"] == 3
        assert theme_obj["pct_change"] is None


# ---------------------------------------------------------------------------
# Test 6: _compute_delta unit tests (pure function)
# ---------------------------------------------------------------------------


class TestComputeDelta:
    def test_empty_series_returns_zero_and_none(self) -> None:
        delta, pct = _compute_delta([])
        assert delta == 0
        assert pct is None

    def test_single_entry_returns_zero_and_none(self) -> None:
        delta, pct = _compute_delta([{"period": "2024-01-01", "count": 5}])
        assert delta == 0
        assert pct is None

    def test_two_entries_increase(self) -> None:
        series = [
            {"period": "2024-01-01", "count": 2},
            {"period": "2024-01-08", "count": 6},
        ]
        delta, pct = _compute_delta(series)
        assert delta == 4
        assert pct is not None
        assert abs(pct - 2.0) < 1e-9

    def test_two_entries_decrease(self) -> None:
        series = [
            {"period": "2024-01-01", "count": 10},
            {"period": "2024-01-08", "count": 5},
        ]
        delta, pct = _compute_delta(series)
        assert delta == -5
        assert pct is not None
        assert abs(pct - (-0.5)) < 1e-9

    def test_prior_zero_gives_none_pct(self) -> None:
        series = [
            {"period": "2024-01-01", "count": 0},
            {"period": "2024-01-08", "count": 7},
        ]
        delta, pct = _compute_delta(series)
        assert delta == 7
        assert pct is None

    def test_three_entries_uses_last_two(self) -> None:
        """_compute_delta uses only the last two entries of the series."""
        series = [
            {"period": "2024-01-01", "count": 100},
            {"period": "2024-01-08", "count": 3},
            {"period": "2024-01-15", "count": 6},
        ]
        delta, pct = _compute_delta(series)
        # latest=6, prior=3 → delta=3, pct=1.0
        assert delta == 3
        assert pct is not None
        assert abs(pct - 1.0) < 1e-9
