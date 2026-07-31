"""Unit tests for scripts.pip_audit_gate.

No live network calls: `_fetch_osv_severities` (the only function that hits OSV) is
monkeypatched everywhere `evaluate()` is exercised, per the repo's standing "zero
live API calls in CI" discipline (see eval/README.md).
"""

from __future__ import annotations

from typing import Any

import pytest
from scripts.pip_audit_gate import (
    _bucket_from_score,
    _cvss_v3_base_score,
    _roundup,
    evaluate,
)


class TestCvssV3BaseScore:
    def test_log4shell_vector_scores_critical(self) -> None:
        # CVE-2021-44228 (Log4Shell) published CVSS v3.1 base score is 10.0 — a
        # well-known, independently-verifiable reference vector for this formula.
        vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
        assert _cvss_v3_base_score(vector) == 10.0

    def test_no_impact_scores_zero(self) -> None:
        vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N"
        assert _cvss_v3_base_score(vector) == 0.0

    def test_malformed_vector_returns_none(self) -> None:
        assert _cvss_v3_base_score("CVSS:3.1/AV:Z/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N") is None

    def test_missing_metric_returns_none(self) -> None:
        assert _cvss_v3_base_score("CVSS:3.1/AV:N/AC:L") is None


class TestRoundup:
    def test_exact_tenth_unchanged(self) -> None:
        assert _roundup(7.2) == 7.2

    def test_rounds_up_not_nearest(self) -> None:
        # 7.21 must round UP to 7.3, not to the nearer 7.2 — this is what
        # distinguishes CVSS roundup() from ordinary rounding.
        assert _roundup(7.21) == 7.3

    def test_zero(self) -> None:
        assert _roundup(0.0) == 0.0


class TestBucketFromScore:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (10.0, "CRITICAL"),
            (9.0, "CRITICAL"),
            (8.9, "HIGH"),
            (7.0, "HIGH"),
            (6.9, "MODERATE"),
            (4.0, "MODERATE"),
            (3.9, "LOW"),
            (0.1, "LOW"),
            (0.0, "UNKNOWN"),
        ],
    )
    def test_boundaries(self, score: float, expected: str) -> None:
        assert _bucket_from_score(score) == expected


class TestEvaluate:
    """Reproduces the exact bug found & fixed against this project's real findings:
    a GHSA-reviewed severity label must win over this script's own CVSS computation
    from an unreviewed, disagreeing vector for the same underlying CVE — otherwise a
    GHSA-labeled MODERATE finding gets silently escalated to a false HIGH and blocks
    CI on noise. See aiohttp/PYSEC-2026-2104 vs. GHSA-jg22-mg44-37j8 in the PR report.
    """

    def test_ghsa_label_wins_over_disagreeing_computed_score(self, monkeypatch: Any) -> None:
        # The PYSEC-sourced vector alone would compute to HIGH (score 7.2); the
        # GHSA-reviewed alias is labeled MODERATE. Labeled must win.
        def fake_fetch(name: str, version: str) -> dict[str, tuple[str, bool]]:
            return {
                "PYSEC-2026-2104": ("HIGH", False),
                "GHSA-jg22-mg44-37j8": ("MODERATE", True),
            }

        monkeypatch.setattr("scripts.pip_audit_gate._fetch_osv_severities", fake_fetch)

        audit_json = {
            "dependencies": [
                {
                    "name": "aiohttp",
                    "version": "3.13.5",
                    "vulns": [
                        {
                            "id": "PYSEC-2026-2104",
                            "aliases": ["CVE-2026-47265", "GHSA-jg22-mg44-37j8"],
                            "fix_versions": ["3.14.0"],
                        }
                    ],
                }
            ]
        }

        findings, should_block = evaluate(audit_json)

        assert len(findings) == 1
        assert findings[0]["severity"] == "MODERATE"
        assert should_block is False

    def test_genuine_high_finding_blocks(self, monkeypatch: Any) -> None:
        def fake_fetch(name: str, version: str) -> dict[str, tuple[str, bool]]:
            return {"GHSA-8ppf-4f7h-5ppj": ("HIGH", True)}

        monkeypatch.setattr("scripts.pip_audit_gate._fetch_osv_severities", fake_fetch)

        audit_json = {
            "dependencies": [
                {
                    "name": "pyasn1",
                    "version": "0.6.3",
                    "vulns": [
                        {
                            "id": "PYSEC-2026-3456",
                            "aliases": ["CVE-2026-59885", "GHSA-8ppf-4f7h-5ppj"],
                            "fix_versions": ["0.6.4"],
                        }
                    ],
                }
            ]
        }

        findings, should_block = evaluate(audit_json)

        assert findings[0]["severity"] == "HIGH"
        assert should_block is True

    def test_no_vulns_does_not_block(self, monkeypatch: Any) -> None:
        called = False

        def fake_fetch(name: str, version: str) -> dict[str, tuple[str, bool]]:
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr("scripts.pip_audit_gate._fetch_osv_severities", fake_fetch)

        audit_json = {"dependencies": [{"name": "fastapi", "version": "0.115.0", "vulns": []}]}
        findings, should_block = evaluate(audit_json)

        assert findings == []
        assert should_block is False
        # No findings means no OSV lookup needed at all — don't spend the network call.
        assert called is False

    def test_unresolvable_severity_is_unknown_and_nonblocking(self, monkeypatch: Any) -> None:
        def fake_fetch(name: str, version: str) -> dict[str, tuple[str, bool]]:
            return {}  # OSV had nothing under this id or any alias

        monkeypatch.setattr("scripts.pip_audit_gate._fetch_osv_severities", fake_fetch)

        audit_json = {
            "dependencies": [
                {
                    "name": "somepkg",
                    "version": "1.0.0",
                    "vulns": [{"id": "PYSEC-9999-0001", "aliases": [], "fix_versions": []}],
                }
            ]
        }
        findings, should_block = evaluate(audit_json)

        assert findings[0]["severity"] == "UNKNOWN"
        assert should_block is False
