"""Blocking severity gate for `pip-audit` JSON output.

`pip-audit` deliberately does not surface CVSS/severity data in its own JSON model
(verified by reading its `_service/osv.py` — it keeps only id/aliases/description/
fix_versions), so a plain `pip-audit` exit code can't distinguish "23 low-severity
PYSEC advisories on a transitive dependency" from "1 critical RCE in a direct
dependency." This script closes that gap without depending on `pip-audit` internals:

1. Read `pip-audit -f json` output from stdin (or --input-file for local testing).
2. For each unique (package, version) with findings, query OSV's `/v1/query`
   endpoint directly (same endpoint `pip-audit -s osv` uses) to recover the fields
   `pip-audit` discards: `database_specific.severity` (GHSA-authored label:
   LOW/MODERATE/HIGH/CRITICAL) and, as a fallback when no label exists, the raw
   CVSS v3.x vector string, from which this script computes the CVSS v3.1 base
   score itself (no `cvss` package dependency) and buckets it per the standard
   CVSS ranges (0.1-3.9 LOW, 4.0-6.9 MEDIUM, 7.0-8.9 HIGH, 9.0-10.0 CRITICAL).
3. Exit 1 if any finding is HIGH or CRITICAL; exit 0 otherwise. MEDIUM/LOW/UNKNOWN
   findings are printed as informational only — they never fail the build.

Known scope limit: CVSS v4.0 base-score computation (MacroVector lookup tables) is
not implemented. A finding whose only CVSS vector is v4.0 AND has no GHSA severity
label is reported as UNKNOWN (informational, non-blocking) rather than silently
treated as safe. In the current dependency set every CVSS_V4-vectored finding also
carries a `database_specific.severity` label, so this gap has zero practical effect
today — flagged here so it's a known, documented limitation, not a silent gap.

Network calls (OSV lookups) get a 10s timeout and 2 retries with backoff; a lookup
that still fails after retries fails the gate closed (exit 1) rather than silently
treating an unresolvable finding as safe — this is a security gate, "unreachable"
must not mean "pass."

Usage (as wired into CI):
    uvx pip-audit -r requirements-audit.txt -f json | uv run python scripts/pip_audit_gate.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from typing import Any

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
REQUEST_TIMEOUT_S = 10
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0

SEVERITY_ORDER = ["UNKNOWN", "LOW", "MODERATE", "HIGH", "CRITICAL"]
BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}

# CVSS v3.1 base-metric weights, per the official spec section 7.1-7.4.
_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
_PR_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}
_PR_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.5}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}


def _cvss_v3_base_score(vector: str) -> float | None:
    """Compute the CVSS v3.0/3.1 base score from a vector string.

    Implements the official roundup() algorithm from the CVSS v3.1 spec (integer
    arithmetic on the score * 100000 to avoid float rounding artifacts). Returns
    None if the vector is malformed or uses metrics this function doesn't recognize.
    """
    metrics: dict[str, str] = {}
    for part in vector.split("/"):
        if ":" not in part or part.startswith("CVSS"):
            continue
        key, _, value = part.partition(":")
        metrics[key] = value

    try:
        av = _AV[metrics["AV"]]
        ac = _AC[metrics["AC"]]
        scope = metrics["S"]
        pr_table = _PR_CHANGED if scope == "C" else _PR_UNCHANGED
        pr = pr_table[metrics["PR"]]
        ui = _UI[metrics["UI"]]
        c = _CIA[metrics["C"]]
        i = _CIA[metrics["I"]]
        a = _CIA[metrics["A"]]
    except KeyError:
        return None

    isc_base = 1 - ((1 - c) * (1 - i) * (1 - a))
    if scope == "C":
        isc = 7.52 * (isc_base - 0.029) - 3.25 * (isc_base - 0.02) ** 15
    else:
        isc = 6.42 * isc_base

    exploitability = 8.22 * av * ac * pr * ui

    if isc <= 0:
        return 0.0

    if scope == "C":
        base = min(1.08 * (isc + exploitability), 10.0)
    else:
        base = min(isc + exploitability, 10.0)

    return _roundup(base)


def _roundup(value: float) -> float:
    """Official CVSS roundup(): round up to the nearest 0.1 using integer math."""
    int_value = int(round(value * 100000))
    if int_value % 10000 == 0:
        return int_value / 100000.0
    return (math.floor(int_value / 10000) + 1) / 10.0


def _bucket_from_score(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MODERATE"
    if score > 0.0:
        return "LOW"
    return "UNKNOWN"


def _fetch_osv_severities(name: str, version: str) -> dict[str, tuple[str, bool]]:
    """Query OSV for (name, version) and return {vuln_id: (severity_bucket, is_labeled)}.

    `is_labeled` is True when the bucket came from a GHSA-reviewed
    `database_specific.severity` field, False when it's this script's own CVSS v3.1
    base-score computation. OSV aggregates the same underlying vulnerability from
    multiple sources (e.g. a PyPA/PYSEC advisory-db entry AND a separate
    GitHub-reviewed GHSA entry) that can carry materially different, disagreeing raw
    CVSS vectors for the same CVE — observed directly on this project's own findings:
    aiohttp's PYSEC vector for PYSEC-2026-2104 computes to a HIGH base score, while
    the human-reviewed GHSA-jg22-mg44-37j8 record for the *same* CVE is labeled
    MODERATE. `evaluate()` uses `is_labeled` to prefer the reviewed label over an
    unreviewed self-computed score — see the resolution logic there.

    Raises RuntimeError after exhausting retries — callers must treat this as a
    gate failure (fail closed), not as "no vulnerabilities."
    """
    payload = json.dumps({"package": {"name": name, "ecosystem": "PyPI"}, "version": version})
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                OSV_QUERY_URL,
                data=payload.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_S * (attempt + 1))
    else:
        raise RuntimeError(f"OSV lookup failed for {name}=={version}: {last_error}")

    severities: dict[str, tuple[str, bool]] = {}
    for vuln in body.get("vulns", []):
        vuln_id = vuln["id"]
        db_severity = (vuln.get("database_specific") or {}).get("severity")
        if db_severity:
            severities[vuln_id] = (db_severity.upper(), True)
            continue

        bucket = "UNKNOWN"
        for entry in vuln.get("severity") or []:
            if entry.get("type") == "CVSS_V3":
                score = _cvss_v3_base_score(entry["score"])
                if score is not None:
                    bucket = _bucket_from_score(score)
                    break
        severities[vuln_id] = (bucket, False)
    return severities


def evaluate(audit_json: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    """Classify every finding in `pip-audit -f json` output.

    Returns (findings, should_block) where each finding dict has keys:
    package, version, id, severity, fix_versions.
    """
    findings: list[dict[str, str]] = []
    should_block = False

    for dep in audit_json.get("dependencies", []):
        vulns = dep.get("vulns") or []
        if not vulns:
            continue

        severities = _fetch_osv_severities(dep["name"], dep["version"])

        for vuln in vulns:
            # A finding may carry multiple aliases (PYSEC-..., CVE-..., GHSA-...).
            # Prefer a GHSA-reviewed label over this script's own CVSS computation —
            # see the _fetch_osv_severities docstring for why raw, unreviewed CVSS
            # vectors on non-GHSA entries can overstate severity relative to the
            # human-reviewed rating for the same underlying CVE. Within each tier
            # (labeled vs. computed), take the most severe rating found.
            candidate_ids = [vuln["id"], *vuln.get("aliases", [])]
            labeled_bucket = "UNKNOWN"
            computed_bucket = "UNKNOWN"
            for cid in candidate_ids:
                result = severities.get(cid)
                if result is None:
                    continue
                sev, is_labeled = result
                target = "labeled_bucket" if is_labeled else "computed_bucket"
                current = labeled_bucket if is_labeled else computed_bucket
                if SEVERITY_ORDER.index(sev) > SEVERITY_ORDER.index(current):
                    if target == "labeled_bucket":
                        labeled_bucket = sev
                    else:
                        computed_bucket = sev
            bucket = labeled_bucket if labeled_bucket != "UNKNOWN" else computed_bucket

            findings.append(
                {
                    "package": dep["name"],
                    "version": dep["version"],
                    "id": vuln["id"],
                    "severity": bucket,
                    "fix_versions": ", ".join(vuln.get("fix_versions") or []) or "none",
                }
            )
            if bucket in BLOCKING_SEVERITIES:
                should_block = True

    return findings, should_block


def _print_report(findings: list[dict[str, str]]) -> None:
    if not findings:
        print("pip-audit gate: 0 known vulnerabilities.")
        return

    by_severity: dict[str, int] = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    print(f"pip-audit gate: {len(findings)} finding(s) across dependencies.")
    print("Severity breakdown:", ", ".join(f"{k}={v}" for k, v in sorted(by_severity.items())))
    print()
    header = f"{'PACKAGE':<20} {'VERSION':<12} {'ID':<22} {'SEVERITY':<10} FIX"
    print(header)
    print("-" * len(header))
    for f in sorted(findings, key=lambda x: (-SEVERITY_ORDER.index(x["severity"]), x["package"])):
        print(
            f"{f['package']:<20} {f['version']:<12} {f['id']:<22} "
            f"{f['severity']:<10} {f['fix_versions']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        help="Read pip-audit JSON from this file instead of stdin (for local testing)",
    )
    args = parser.parse_args()

    if args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()
    try:
        audit_json = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"pip-audit gate: could not parse pip-audit JSON input: {exc}", file=sys.stderr)
        return 1

    try:
        findings, should_block = evaluate(audit_json)
    except RuntimeError as exc:
        # Fail closed: an unresolvable severity lookup is not the same as "clean."
        print(f"pip-audit gate: {exc}", file=sys.stderr)
        return 1

    _print_report(findings)

    if should_block:
        print(
            "\npip-audit gate: FAILING — one or more HIGH/CRITICAL severity "
            "vulnerabilities found. See table above.",
            file=sys.stderr,
        )
        return 1

    print("\npip-audit gate: PASS — 0 HIGH/CRITICAL severity vulnerabilities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
