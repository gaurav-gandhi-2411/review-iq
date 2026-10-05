from __future__ import annotations

from datetime import date

from scripts.npm_audit_gate import evaluate

OK_DAY = date(2026, 10, 5)
BRACES = {
    "severity": "high",
    "via": [{"url": "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm", "title": "braces"}],
}
TRANSITIVE = {"severity": "high", "via": ["braces"]}


def test_allowlisted_advisory_and_its_transitives_pass() -> None:
    audit = {"vulnerabilities": {"braces": BRACES, "micromatch": TRANSITIVE}}
    assert evaluate(audit, OK_DAY) == []


def test_unknown_high_advisory_fails() -> None:
    other = {
        "severity": "high",
        "via": [{"url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"}],
    }
    problems = evaluate({"vulnerabilities": {"x": other}}, OK_DAY)
    assert problems and "GHSA-aaaa-bbbb-cccc" in problems[0]


def test_allowlist_expires() -> None:
    problems = evaluate({"vulnerabilities": {"braces": BRACES}}, date(2027, 1, 1))
    assert problems and "expired" in problems[0]


def test_moderate_ignored_and_garbage_fails_closed() -> None:
    assert evaluate({"vulnerabilities": {"m": {"severity": "moderate", "via": []}}}, OK_DAY) == []
    assert evaluate({"error": {"code": "ENOAUDIT"}}, OK_DAY)
    assert evaluate({"vulnerabilities": {"z": {"severity": "critical", "via": []}}}, OK_DAY)
