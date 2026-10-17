"""Pin the monitoring-as-code definitions (ops/monitoring/) without calling Google.

These tests cannot prove the live project matches; `apply_monitoring.py plan` and the
runbook's verification steps do that. They pin the *intent* so a refactor cannot silently
weaken a threshold or drop SSL validation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "ops" / "monitoring"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


apply_mod = _load("apply_monitoring")
CHAN = "projects/p/notificationChannels/1"


def _cond(kind: str) -> dict:
    body = apply_mod.policy_body(kind, "chk-1", CHAN)
    assert body["notificationChannels"] == [CHAN]
    return body["conditions"][0]["conditionThreshold"]


def test_check_is_https_with_ssl_validation_and_content_match() -> None:
    http = apply_mod.check_body("x", "/health", '"status":"ok"', ssl=True)
    assert http["httpCheck"]["port"] == 443
    assert http["httpCheck"]["useSsl"] is True
    assert http["httpCheck"]["validateSsl"] is True
    assert http["contentMatchers"][0]["content"] == '"status":"ok"'
    assert http["logCheckFailures"] is True


def test_availability_policy_keeps_two_region_120s_rule() -> None:
    c = _cond("avail")
    assert (c["comparison"], c["thresholdValue"], c["duration"]) == ("COMPARISON_GT", 1, "120s")
    assert c["aggregations"][0]["crossSeriesReducer"] == "REDUCE_COUNT_FALSE"


def test_rate_policy_fires_at_three_failed_checks_not_two() -> None:
    # One failed check = 6 false of ~540 pooled 10 s samples in 15 min (see README).
    c = _cond("rate")
    assert c["aggregations"][0]["alignmentPeriod"] == "900s"
    assert 1 - 3 * 6 / 540 < c["thresholdValue"] < 1 - 2 * 6 / 540


def test_cert_policy_alerts_under_14_days() -> None:
    c = _cond("cert")
    assert "time_until_ssl_cert_expires" in c["filter"]
    assert (c["comparison"], c["thresholdValue"]) == ("COMPARISON_LT", 14)
