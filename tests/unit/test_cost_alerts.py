"""Per-customer inference cost alert (S29, flag OFF): the pure evaluation, the sweep, and the token-protected endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from app.core import cost_alerts as CA
from fastapi.testclient import TestClient

ORG_A, ORG_B, ORG_C = "org-a", "org-b", "org-c"


def test_alert_only_strictly_above_the_fraction_of_the_plan_price():
    # starter price 29: 20% = 5.80 exactly -> not above; one cent more -> above
    plans = {ORG_A: "starter", ORG_B: "starter"}
    out = CA.evaluate({ORG_A: 5.80, ORG_B: 5.81}, plans)
    assert [a.org_id for a in out] == [ORG_B]
    assert out[0].ratio == pytest.approx(5.81 / 29, abs=1e-4)


def test_a_ratio_exactly_at_the_fraction_is_not_an_alert():
    # S31 control audit: 5.80 / 29 is a hair BELOW 0.2 in floating point, so the test above never exercises equality and a
    # `>=` mutation survived. 19.75 / 79 is exactly 0.25 in binary floating point.
    plans = {ORG_A: "growth", ORG_B: "growth"}
    assert [a.org_id for a in CA.evaluate({ORG_A: 19.75, ORG_B: 19.76}, plans, fraction=0.25)] == [
        ORG_B
    ]


def test_the_sweep_uses_the_configured_fraction_not_the_default():
    # 19 / 79 = 24%: above the 20% default, below a configured 25%
    res = CA.run_sweep({ORG_A: "growth"}, lambda org_id, since: 19.0, fraction=0.25)
    assert res["threshold"] == 0.25 and res["alerts"] == []


def test_the_default_line_is_20_percent_in_code_and_settings():
    from app.core.config import Settings

    assert CA.DEFAULT_FRACTION == 0.20
    assert Settings.model_fields["cost_alert_fraction"].default == 0.20  # GG decision, S29


def test_an_unknown_plan_is_logged_as_an_error():
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        CA.evaluate({ORG_A: 1.0}, {ORG_A: "agency"})
    assert [e["event"] for e in logs if e["log_level"] == "error"] == ["cost_alert.unknown_plan"]


def test_a_full_quota_starter_or_growth_customer_is_below_the_line_but_india_growth_and_scale_are_above_it():
    # measured: Starter 5,000 reviews = $2.67 of $29 (9.2%); Growth 25,000 = $13.35 of $79 (16.9%); Scale 100,000 = $53.40 of $199
    # (26.8%); India Growth = $13.35 of $52.24 (25.6%). docs/pricing.json prices; the line is 20% by GG decision, so full quota on the
    # larger plans alerts and 40% utilisation (the pricing file's typical figure) does not.
    plans = {ORG_A: "starter", ORG_B: "growth", ORG_C: "growth_in", "org-d": "scale"}
    assert [a.org_id for a in CA.evaluate({ORG_A: 2.67, ORG_B: 13.35}, plans)] == []
    full = CA.evaluate({ORG_C: 13.35, "org-d": 53.40}, plans)
    assert {a.org_id for a in full} == {ORG_C, "org-d"}
    assert CA.evaluate({ORG_C: 0.4 * 13.35, "org-d": 0.4 * 53.40}, plans) == []


def test_unknown_or_missing_plan_is_skipped_not_alerted_and_not_crashing():
    assert CA.evaluate({ORG_A: 999.0, ORG_B: 999.0}, {ORG_A: "agency"}) == []


def test_alerts_are_sorted_worst_first_and_the_india_plans_use_their_own_price():
    out = CA.evaluate(
        {ORG_A: 7.0, ORG_B: 30.0},
        {ORG_A: "starter_in", ORG_B: "growth"},  # 7/15.66 = 44.7%, 30/79 = 38.0%
    )
    assert [a.org_id for a in out] == [ORG_A, ORG_B]


def test_parse_org_plans_empty_valid_and_malformed():
    assert CA.parse_org_plans("") == {}
    assert CA.parse_org_plans('{"x": "Starter"}') == {"x": "starter"}
    with pytest.raises(ValueError):
        CA.parse_org_plans("[1, 2]")
    with pytest.raises(ValueError):
        CA.parse_org_plans("{not json")


def test_month_start_is_the_first_of_the_utc_month():
    assert CA.month_start_utc(datetime(2026, 11, 17, 13, 5, tzinfo=UTC)) == datetime(
        2026, 11, 1, tzinfo=UTC
    )


def test_sweep_isolates_one_orgs_failure_and_reports_the_rest():
    def cost(org_id: str, since: datetime) -> float:
        if org_id == ORG_C:
            raise RuntimeError("db down")
        return {ORG_A: 1.0, ORG_B: 40.0}[org_id]

    res = CA.run_sweep(
        {ORG_A: "starter", ORG_B: "growth", ORG_C: "starter"},
        cost,
        now=datetime(2026, 11, 17, tzinfo=UTC),
    )
    assert res["orgs_checked"] == 2
    assert res["orgs_failed"] == [ORG_C]
    assert [a["org_id"] for a in res["alerts"]] == [ORG_B]  # 40/79 = 50.6%
    assert res["month_start"].startswith("2026-11-01")


def _settings(**kw: object) -> MagicMock:
    s = MagicMock()
    s.cost_alert_trigger_token = kw.get("token", "tok")
    s.enable_cost_alerts = kw.get("enabled", False)
    s.org_plans_json = kw.get("plans", "")
    s.cost_alert_fraction = 0.20
    return s


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def test_endpoint_503_when_no_token_is_configured_and_401_on_a_wrong_or_missing_token(
    client: TestClient,
):
    with patch("app.api.internal.cost_alerts.get_settings", return_value=_settings(token=None)):
        assert client.post("/internal/cost-alerts/run").status_code == 503
    with patch("app.api.internal.cost_alerts.get_settings", return_value=_settings()):
        assert client.post("/internal/cost-alerts/run").status_code == 401
        r = client.post("/internal/cost-alerts/run", headers={"X-Cost-Alert-Trigger-Token": "nope"})
        assert r.status_code == 401


def test_endpoint_with_the_flag_off_does_nothing_and_never_touches_the_database(client: TestClient):
    with (
        patch("app.api.internal.cost_alerts.get_settings", return_value=_settings(enabled=False)),
        patch("app.api.internal.cost_alerts.org_month_cost_usd_pg") as db,
    ):
        r = client.post("/internal/cost-alerts/run", headers={"X-Cost-Alert-Trigger-Token": "tok"})
    assert r.status_code == 200 and r.json() == {"ok": True, "enabled": False}
    db.assert_not_called()


def test_endpoint_with_the_flag_on_reports_the_alert(client: TestClient):
    s = _settings(enabled=True, plans='{"org-a": "starter"}')
    with (
        patch("app.api.internal.cost_alerts.get_settings", return_value=s),
        patch("app.api.internal.cost_alerts.org_month_cost_usd_pg", return_value=15.0),
    ):
        r = client.post("/internal/cost-alerts/run", headers={"X-Cost-Alert-Trigger-Token": "tok"})
    body = r.json()
    assert r.status_code == 200 and body["enabled"] is True and body["orgs_checked"] == 1
    assert body["alerts"][0]["org_id"] == "org-a" and body["alerts"][0]["ratio"] == pytest.approx(
        15 / 29, abs=1e-3
    )


def test_endpoint_fails_loudly_on_a_broken_plan_map(client: TestClient):
    with patch(
        "app.api.internal.cost_alerts.get_settings",
        return_value=_settings(enabled=True, plans="{oops"),
    ):
        r = client.post("/internal/cost-alerts/run", headers={"X-Cost-Alert-Trigger-Token": "tok"})
    assert r.status_code == 500
