"""Idempotent create-or-update of the Cloud Monitoring uptime check and alert policies.

Stdlib only; talks to the Monitoring v3 REST API with a token from `gcloud auth
print-access-token`. Resources are matched by displayName, so re-running converges instead
of duplicating. The notification channel is the project's existing enabled email channel
(looked up, never created, never printed). See ops/monitoring/README.md.

    python ops/monitoring/apply_monitoring.py --account <acct> plan
    python ops/monitoring/apply_monitoring.py --account <acct> apply --only checks
    python ops/monitoring/apply_monitoring.py --account <acct> apply --only policies
    python ops/monitoring/apply_monitoring.py --account <acct> test-alert up|down [--confirm]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

PROJECT = "reviewiq-prod-260813"
HOST = "api.samidhareviews.xyz"
API = "https://monitoring.googleapis.com/v3"
CHECK_NAME = "api-health-https"
TEST_NAME = "s23-alert-test"  # temporary resources for the email-path test; name is the guard
POLICY_AVAIL = "Uptime failure - Samidha Reviews"
POLICY_RATE = "Uptime failure rate - Samidha Reviews"
POLICY_CERT = "Uptime TLS certificate expiry - Samidha Reviews"
UPTIME_METRIC = "monitoring.googleapis.com/uptime_check/"


def check_body(display: str, path: str, matcher: str | None, ssl: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "displayName": display,
        "monitoredResource": {
            "type": "uptime_url",
            "labels": {"project_id": PROJECT, "host": HOST},
        },
        "httpCheck": {
            "requestMethod": "GET",
            "path": path,
            "port": 443 if ssl else 80,
            "useSsl": ssl,
            "validateSsl": ssl,
            "acceptedResponseStatusCodes": [{"statusClass": "STATUS_CLASS_2XX"}],
        },
        "period": "60s",
        "timeout": "10s",
        "checkerType": "STATIC_IP_CHECKERS",
        "logCheckFailures": True,
    }
    if matcher:
        body["contentMatchers"] = [{"content": matcher, "matcher": "CONTAINS_STRING"}]
    return body


def _flt(metric: str, check_id: str) -> str:
    return (
        f'metric.type="{UPTIME_METRIC}{metric}" AND metric.label.check_id="{check_id}"'
        ' AND resource.type="uptime_url"'
    )


def policy_body(kind: str, check_id: str, channel: str, display: str | None = None) -> dict:
    # Thresholds and their evidence: ops/monitoring/README.md ("Why these thresholds").
    if kind == "avail":  # unchanged semantics: > 1 region failing for 120 s
        cond = {
            "displayName": f"Failure of uptime check_id {check_id}",
            "conditionThreshold": {
                "filter": _flt("check_passed", check_id),
                "aggregations": [
                    {
                        "alignmentPeriod": "1200s",
                        "perSeriesAligner": "ALIGN_NEXT_OLDER",
                        "crossSeriesReducer": "REDUCE_COUNT_FALSE",
                        "groupByFields": ["resource.label.*"],
                    }
                ],
                "comparison": "COMPARISON_GT",
                "thresholdValue": 1,
                "duration": "120s",
                "trigger": {"count": 1},
            },
        }
        name = POLICY_AVAIL
    elif kind == "rate":  # ~3+ failed checks (any regions) in 15 min
        cond = {
            "displayName": "Pooled check pass-fraction < 0.97 over 15 min",
            "conditionThreshold": {
                "filter": _flt("check_passed", check_id),
                "aggregations": [
                    {
                        "alignmentPeriod": "900s",
                        "perSeriesAligner": "ALIGN_FRACTION_TRUE",
                        "crossSeriesReducer": "REDUCE_MEAN",
                        "groupByFields": ["resource.label.*"],
                    }
                ],
                "comparison": "COMPARISON_LT",
                "thresholdValue": 0.97,
                "duration": "0s",
                "trigger": {"count": 1},
            },
        }
        name = POLICY_RATE
    elif kind == "cert":
        cond = {
            "displayName": "TLS certificate expires in < 14 days",
            "conditionThreshold": {
                "filter": _flt("time_until_ssl_cert_expires", check_id),
                "aggregations": [
                    {
                        "alignmentPeriod": "600s",
                        "perSeriesAligner": "ALIGN_NEXT_OLDER",
                        "crossSeriesReducer": "REDUCE_MIN",
                        "groupByFields": ["resource.label.*"],
                    }
                ],
                "comparison": "COMPARISON_LT",
                "thresholdValue": 14,  # metric unit is days
                "duration": "0s",
                "trigger": {"count": 1},
            },
        }
        name = POLICY_CERT
    else:  # "test": fires on the first failed check of the deliberately failing test check
        cond = {
            "displayName": "s23 test: deliberately failing check",
            "conditionThreshold": {
                "filter": _flt("check_passed", check_id),
                "aggregations": [
                    {
                        "alignmentPeriod": "60s",
                        "perSeriesAligner": "ALIGN_NEXT_OLDER",
                        "crossSeriesReducer": "REDUCE_COUNT_FALSE",
                        "groupByFields": ["resource.label.*"],
                    }
                ],
                "comparison": "COMPARISON_GT",
                "thresholdValue": 0,
                "duration": "0s",
                "trigger": {"count": 1},
            },
        }
        name = TEST_NAME
    return {
        "displayName": display or name,
        "combiner": "OR",
        "enabled": True,
        "conditions": [cond],
        "notificationChannels": [channel],
        "alertStrategy": {"autoClose": "1800s"},
    }


class Api:
    def __init__(self, account: str) -> None:
        self.token = subprocess.run(  # noqa: S603 - fixed argv, account is an explicit CLI arg
            ["gcloud", "auth", "print-access-token", f"--account={account}"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            shell=sys.platform == "win32",
        ).stdout.strip()

    def call(self, method: str, url: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(  # noqa: S310 - https URL built from API constant
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "x-goog-user-project": PROJECT,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # Body of a Monitoring error is a status message, never the token.
            raise SystemExit(
                f"{method} {url.split('?')[0]} -> {exc.code}: {exc.read()[:400]!r}"
            ) from exc
        return json.loads(raw) if raw else {}

    def list(self, kind: str, key: str) -> list[dict]:
        return self.call("GET", f"{API}/projects/{PROJECT}/{kind}").get(key, [])


def email_channel(api: Api) -> str:
    chans = [
        c["name"]
        for c in api.list("notificationChannels", "notificationChannels")
        if c.get("type") == "email" and c.get("enabled", True)
    ]
    if len(chans) != 1:
        raise SystemExit(
            f"expected exactly 1 enabled email channel, found {len(chans)}; not guessing"
        )
    return chans[0]


def upsert(api: Api, kind: str, key: str, body: dict) -> str:
    """Create-or-update by displayName. Returns the resource name."""
    existing = [r for r in api.list(kind, key) if r["displayName"] == body["displayName"]]
    if len(existing) > 1:
        raise SystemExit(f"{len(existing)} {kind} named {body['displayName']!r}; resolve by hand")
    base = f"{API}/projects/{PROJECT}/{kind}"
    if not existing:
        return api.call("POST", base, body)["name"]
    name = existing[0]["name"]
    # PATCH with an explicit mask of managed fields; server-owned fields stay untouched.
    # monitoredResource is immutable on an uptime check (API: "Cannot update the resource").
    mask = ",".join(k for k in body if k not in ("displayName", "monitoredResource"))
    api.call("PATCH", f"{API}/{name}?updateMask={mask}", body)
    return name


def check_id_of(name: str) -> str:
    return name.rsplit("/", 1)[1]


def find(api: Api, kind: str, key: str, display: str) -> dict | None:
    hits = [r for r in api.list(kind, key) if r["displayName"] == display]
    return hits[0] if hits else None


def cmd_plan(api: Api, _: argparse.Namespace) -> None:
    print("channel:", "found" if email_channel(api) else "?")  # address is never fetched/printed
    for kind, key, disp in [
        ("uptimeCheckConfigs", "uptimeCheckConfigs", CHECK_NAME),
        ("alertPolicies", "alertPolicies", POLICY_AVAIL),
        ("alertPolicies", "alertPolicies", POLICY_RATE),
        ("alertPolicies", "alertPolicies", POLICY_CERT),
    ]:
        print(f"{disp!r}: {'exists' if find(api, kind, key, disp) else 'would create'}")


def cmd_apply(api: Api, args: argparse.Namespace) -> None:
    if args.only in ("checks", "all"):
        name = upsert(
            api,
            "uptimeCheckConfigs",
            "uptimeCheckConfigs",
            check_body(CHECK_NAME, "/health", '"status":"ok"', ssl=True),
        )
        print("check ok:", check_id_of(name))
    if args.only in ("policies", "all"):
        chk = find(api, "uptimeCheckConfigs", "uptimeCheckConfigs", CHECK_NAME)
        if chk is None:
            raise SystemExit(f"check {CHECK_NAME!r} missing; run apply --only checks first")
        chan = email_channel(api)
        for kind in ("avail", "rate", "cert"):
            n = upsert(
                api,
                "alertPolicies",
                "alertPolicies",
                policy_body(kind, check_id_of(chk["name"]), chan),
            )
            print(f"policy {kind} ok:", n.rsplit("/", 1)[1])


def cmd_test_alert(api: Api, args: argparse.Namespace) -> None:
    kinds = [("alertPolicies", "alertPolicies"), ("uptimeCheckConfigs", "uptimeCheckConfigs")]
    if args.action == "up":
        # A 404 path makes every probe fail against a real, valid endpoint.
        name = upsert(
            api,
            "uptimeCheckConfigs",
            "uptimeCheckConfigs",
            check_body(TEST_NAME, "/s23-alert-test-404", None, ssl=True),
        )
        pol = upsert(
            api,
            "alertPolicies",
            "alertPolicies",
            policy_body("test", check_id_of(name), email_channel(api)),
        )
        print("test check:", name, "\ntest policy:", pol)
        return
    victims = [
        r["name"] for k, key in kinds for r in api.list(k, key) if r["displayName"] == TEST_NAME
    ]
    print("would delete:" if not args.confirm else "deleting:", *victims, sep="\n  ")
    if not victims:
        raise SystemExit("nothing matched: an empty list is a failed check, not a success")
    if args.confirm:
        for v in victims:
            api.call("DELETE", f"{API}/{v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", required=True, help="gcloud account (explicit, never ambient)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    a = sub.add_parser("apply")
    a.add_argument("--only", choices=["checks", "policies", "all"], default="all")
    t = sub.add_parser("test-alert")
    t.add_argument("action", choices=["up", "down"])
    t.add_argument(
        "--confirm", action="store_true", help="'down': actually delete (otherwise list only)"
    )
    args = ap.parse_args()
    api = Api(args.account)
    {"plan": cmd_plan, "apply": cmd_apply, "test-alert": cmd_test_alert}[args.cmd](api, args)


if __name__ == "__main__":
    main()
