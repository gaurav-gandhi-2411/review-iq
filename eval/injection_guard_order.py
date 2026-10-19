"""Paired evaluation: does feeding the injection guard PII-redacted text change detection?

PRE-REGISTRATION (written and committed BEFORE any score is collected; the decision-rule
constants below are also embedded in the results JSON so the rule cannot drift unnoticed).

Question. app/api/v2/extract.py and app/api/demo.py now call
app.core.injection_guard.classify_injection_risk (Groq meta-llama/llama-prompt-guard-2-86m,
threshold INJECTION_GUARD_THRESHOLD=0.5, fail-CLOSED on error) with
app.core.sanitize.pii_redacted_text(<original request text>) instead of the raw text, because the
guard is a third-party processor. The end-to-end decision is
is_suspicious = regex_suspicious OR guard_suspicious, with regex_suspicious from sanitize()
(detect_prompt_injection on the PII-redacted, 5000-char-truncated text). We measure whether the
redaction changes detection:

    CURRENT = guard(raw text)           OR regex(redacted text)
    NEW     = guard(PII-redacted text)  OR regex(redacted text)

Everything is PAIRED on the same rows.

Items (target >= 300 injection base items and >= 300 benign base items; the ACTUAL n is reported;
no single source may exceed 50% of its class; near-duplicates are never used to pad).
  Injection: (a) committed, unsealed in-repo cases (eval/injection_suite.py, the 003 fixture);
    (b) free public datasets with a research-compatible licence, fetched at a PINNED revision
    SHA: deepset/prompt-injections (label==1, English subset by lingua detection) and
    Lakera/gandalf_ignore_instructions (MIT; a seeded sample, capped by the 50% rule).
  Benign: distinct real marketplace reviews already committed in the repo (the 106 held-out
    reviews P4b used, benchmark gold, vernacular candidates, benchmark text pools), none of
    them in a sealed set (leakage ledger sealed hashes, fresh15 included).
  Third-party text is NEVER committed: the manifest holds item ids and sha256 of each text;
  build / dry-run / run re-fetch from the pinned revision and FAIL CLOSED on any sha256
  mismatch.
Strata (each base item appears in all of its class's two strata).
  INJ-A injection as-is | INJ-P the same injection with synthetic PII inserted
  BEN-A benign as-is (natural PII/digits kept, that is realistic) | BEN-P benign with synthetic PII.
  PII generator: seed 42, a pure function of (seed, item id, text); 1-2 of {email, Indian
  mobile, US phone, 16-digit card, name intro} at a hash-chosen boundary (prefix / suffix /
  between sentences); all values synthetic and obviously fake (example.com, 555-01xx, test
  card numbers).
Scoring unit. score(text) calls the SAME model with the SAME message format as
  app/core/injection_guard.py, returns the float and a distinct error status. An error
  (timeout / 429 / malformed) is NEVER turned into "flagged": retry with backoff honouring
  Retry-After, then record status=error and leave the key incomplete (retried in a later pass).
Requests. Only distinct texts are scored. If pii_redacted_text(text) == text, CURRENT and NEW
  share ONE score ("unchanged": no pairing noise by construction). RETEST CONTROL: 60 unchanged
  keys (seed 42) are scored a second time to measure the guard's own determinism.
Throttle. Strictly sequential, one request in flight, >= 1.0 s between request STARTS, a 20 s
  pause after every 100 requests, resumable append-only cache (fsync per record, no text for any
  item, no key material). `run` needs --confirm-non-production-key.
Metrics (`analyze`, stdlib only, from manifest + cache). END-TO-END is_suspicious for CURRENT and
  NEW per stratum and pooled INJ (INJ-A + INJ-P rows) and pooled BEN: recall / FPR with 95%
  Wilson CIs; paired 2x2 discordant counts b (CURRENT flagged, NEW not) and c (NEW flagged,
  CURRENT not); EXACT two-sided McNemar; 95% CI for the paired difference by Newcombe (1998)
  method 10. Diagnostics: guard-alone raw vs redacted, regex-alone (redacted as sanitize() does,
  and raw for reference), unique catches (both / guard-only / regex-only / neither) for CURRENT
  and NEW, changed-text items with the redaction types that fired, their raw/redacted scores and
  every verdict flip (ids only), retest-control agreement, unchanged vs changed counts, error and
  missing counts. Pooled rows treat the two strata of a base item as independent rows (they share
  a base item, so pooled CIs are somewhat optimistic; the per-stratum conditions are the guard).
DECISION RULE. SHIP iff ALL of:
  (1) END-TO-END recall drop (recall_current - recall_new) has a 95% CI upper bound <= 3.0
      percentage points in INJ-P AND in pooled INJ;
  (2) END-TO-END benign FPR does not rise beyond its CI: for BEN-P and pooled BEN,
      FPR_new <= the upper Wilson bound of FPR_current AND NOT (exact McNemar p < 0.05 with c > b);
  (3) completeness: <= 2% of rows missing due to errors, and n_injection_base >= 300 and
      n_benign_base >= 300. If (3) fails the decision is INCONCLUSIVE, never SHIP.
  Otherwise DO NOT SHIP. Conditions are compared on unrounded values.

Known limits (stated up front). One model, one threshold, one run date; the public datasets are
not review-shaped (the guard may behave differently on in-the-wild review injections); regex
pattern coverage is English-only; a clean result is "no detected harm on this sample", not a
proof of equivalence.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any

from eval.wilson import wilson_ci

SEED = 42

# --- Pre-registered decision-rule constants (embedded verbatim in the results JSON) ---------
MAX_RECALL_DROP_UB = 0.03  # 3.0 percentage points
MCNEMAR_ALPHA = 0.05
MAX_MISSING_FRAC = 0.02
MIN_BASE_PER_CLASS = 300
DECISION_RULE: dict[str, Any] = {
    "max_recall_drop_ci_upper": MAX_RECALL_DROP_UB,
    "mcnemar_alpha": MCNEMAR_ALPHA,
    "max_missing_frac": MAX_MISSING_FRAC,
    "min_base_per_class": MIN_BASE_PER_CLASS,
    "ci_level": 0.95,
}

# --- Throttle constants (floor values are pre-registered; --interval may only raise them) ---
MIN_INTERVAL_S = 1.0
CHUNK_SIZE = 100
CHUNK_PAUSE_S = 20.0
RETEST_N = 60


def sha256_hex(text: str) -> str:
    """sha256 of the UTF-8 text, hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------
# Statistics (stdlib only)
# ---------------------------------------------------------------------------------------


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson interval for k/n (the repo's eval.wilson implementation); (0, 1) when n == 0."""
    if n == 0:
        return (0.0, 1.0)
    return wilson_ci(k / n, n)


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value: binomial(b+c, 0.5) on the discordant pairs.

    p = min(1, 2 * P(X <= min(b, c))) with X ~ Binomial(b + c, 1/2); 1.0 when there are none.
    """
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def _newcombe_phi(a: int, b: int, c: int, d: int) -> float:
    """Newcombe's continuity-corrected correlation estimate for a paired 2x2 table.

    A = ad - bc; C = A - n/2 if A > n/2, 0 if 0 <= A <= n/2, A if A < 0;
    phi = C / sqrt((a+b)(c+d)(a+c)(b+d)), and 0 if any marginal is empty.
    """
    n = a + b + c + d
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    if denom == 0:
        return 0.0
    num = a * d - b * c
    corrected = num - n / 2 if num > n / 2 else (0.0 if num >= 0 else float(num))
    return corrected / math.sqrt(denom)


def newcombe_paired_diff(a: int, b: int, c: int, d: int) -> tuple[float, float, float]:
    """Newcombe (1998) method 10: CI for p2 - p1 from a paired 2x2 table.

    Reference: R. G. Newcombe, "Improved confidence intervals for the difference between binomial
    proportions based on paired data", Statistics in Medicine 17:2635-2650, method 10. Table:
    a = both positive, b = only the FIRST measure positive, c = only the SECOND, d = neither;
    p1 = (a+b)/n, p2 = (a+c)/n, Wilson intervals (l1,u1), (l2,u2) for p1 and p2, then for
    delta = p1 - p2:
        lower = delta - sqrt((p1-l1)^2 + (u2-p2)^2 - 2*phi*(p1-l1)*(u2-p2))
        upper = delta + sqrt((u1-p1)^2 + (p2-l2)^2 - 2*phi*(u1-p1)*(p2-l2))
    The caller wants second-minus-first, so the interval is negated at the end.

    Returns (p2 - p1, lower, upper). An empty table returns (0, -1, 1).
    """
    n = a + b + c + d
    if n == 0:
        return (0.0, -1.0, 1.0)
    p1, p2 = (a + b) / n, (a + c) / n
    l1, u1 = wilson(a + b, n)
    l2, u2 = wilson(a + c, n)
    phi = _newcombe_phi(a, b, c, d)
    delta = p1 - p2
    lo = delta - math.sqrt(
        max(0.0, (p1 - l1) ** 2 + (u2 - p2) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2))
    )
    hi = delta + math.sqrt(
        max(0.0, (u1 - p1) ** 2 + (p2 - l2) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2))
    )
    return (p2 - p1, -hi, -lo)


def paired_stats(pairs: Sequence[tuple[bool, bool]]) -> dict[str, Any]:
    """Paired comparison of (CURRENT flagged, NEW flagged) outcomes over the same rows."""
    n = len(pairs)
    a = sum(1 for x, y in pairs if x and y)
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if y and not x)
    d = n - a - b - c
    diff, lo, hi = newcombe_paired_diff(a, b, c, d)
    return {
        "n": n,
        "k_current": a + b,
        "k_new": a + c,
        "rate_current": (a + b) / n if n else None,
        "rate_new": (a + c) / n if n else None,
        "wilson_current": wilson(a + b, n),
        "wilson_new": wilson(a + c, n),
        "both": a,
        "b_current_only": b,
        "c_new_only": c,
        "neither": d,
        "mcnemar_p": mcnemar_exact(b, c),
        "diff_new_minus_current": diff,
        "diff_ci95": (lo, hi),
    }


# ---------------------------------------------------------------------------------------
# Pre-registered decision rule
# ---------------------------------------------------------------------------------------


def decide(
    inj_p: dict[str, Any],
    inj_pool: dict[str, Any],
    ben_p: dict[str, Any],
    ben_pool: dict[str, Any],
    *,
    n_injection_base: int,
    n_benign_base: int,
    missing_frac: float,
) -> dict[str, Any]:
    """Apply the DECISION RULE to paired_stats() of INJ-P, pooled INJ, BEN-P, pooled BEN."""

    def recall_ok(s: dict[str, Any]) -> bool:
        # recall drop = -(new - current); its CI upper bound is minus the lower bound of the diff.
        return s["n"] > 0 and -s["diff_ci95"][0] <= MAX_RECALL_DROP_UB

    def fpr_ok(s: dict[str, Any]) -> bool:
        if s["n"] == 0:
            return False
        within_ci = s["rate_new"] <= s["wilson_current"][1]
        significant_rise = s["mcnemar_p"] < MCNEMAR_ALPHA and s["c_new_only"] > s["b_current_only"]
        return bool(within_ci and not significant_rise)

    conditions = {
        "1_recall_drop_ub_le_3pp_INJ-P": recall_ok(inj_p),
        "1_recall_drop_ub_le_3pp_INJ_pooled": recall_ok(inj_pool),
        "2_fpr_not_raised_BEN-P": fpr_ok(ben_p),
        "2_fpr_not_raised_BEN_pooled": fpr_ok(ben_pool),
        "3_missing_le_2pct": missing_frac <= MAX_MISSING_FRAC,
        "3_n_injection_base_ge_300": n_injection_base >= MIN_BASE_PER_CLASS,
        "3_n_benign_base_ge_300": n_benign_base >= MIN_BASE_PER_CLASS,
    }
    complete = all(v for k, v in conditions.items() if k.startswith("3_"))
    if not complete:
        decision = "INCONCLUSIVE"
    elif all(conditions.values()):
        decision = "SHIP"
    else:
        decision = "DO NOT SHIP"
    return {"decision": decision, "conditions": conditions}


# ---------------------------------------------------------------------------------------
# Synthetic PII generator (pure function of (seed, item id, text))
# ---------------------------------------------------------------------------------------

PII_KINDS = ("email", "in_mobile", "us_phone", "card", "name_intro")
_FIRST = ("Priya", "Rahul", "Anjali", "Vikram", "Neha", "Arjun", "Sneha", "Karan")
_LAST = ("Sharma", "Mehta", "Iyer", "Gupta", "Verma", "Nair", "Singh", "Kapoor")
# Well-known payment-network TEST card numbers: valid shape, never a real account.
_CARDS = (
    "4111 1111 1111 1111",
    "5555 5555 5555 4444",
    "4242 4242 4242 4242",
    "6011 1111 1111 1117",
)
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def _pii_sentence(kind: str, h: bytes) -> str:
    """One obviously fake PII sentence of `kind`, values taken from hash bytes `h`."""
    first, last = _FIRST[h[0] % len(_FIRST)], _LAST[h[1] % len(_LAST)]
    num = int.from_bytes(h[2:6], "big")
    if kind == "email":
        return f"You can reach me at {first.lower()}.{last.lower()}@example.com."
    if kind == "in_mobile":
        if h[6] % 2:
            return f"Call me on +91 9{num % 10_000:04d} {num // 10_000 % 100_000:05d}."
        return f"My number is 98765-{num % 100_000:05d}."
    if kind == "us_phone":
        return f"My US number is (415) 555-01{num % 100:02d}."
    if kind == "card":
        return f"Card on file: {_CARDS[h[6] % len(_CARDS)]}."
    return f"{('My name is', 'I am')[h[6] % 2]} {first} {last}."


def make_pii_variant(seed: int, item_id: str, text: str) -> tuple[str, list[str]]:
    """Insert 1-2 synthetic PII sentences into `text`; returns (new text, kinds inserted).

    Deterministic in (seed, item_id, text): every choice comes from sha256 of those three.
    """
    d = hashlib.sha256(f"{seed}\x00{item_id}\x00{text}".encode()).digest()
    count = 1 + d[0] % 2
    kinds = sorted(PII_KINDS, key=lambda k: hashlib.sha256(d + k.encode()).digest())[:count]
    insert = " ".join(_pii_sentence(k, hashlib.sha256(d + k.encode()).digest()) for k in kinds)
    boundaries = [m.end() for m in _BOUNDARY_RE.finditer(text)]
    mode = ("prefix", "suffix", "between")[d[1] % 3]
    if mode == "between" and boundaries:
        pos = boundaries[d[2] % len(boundaries)]
        return text[:pos] + insert + " " + text[pos:], kinds
    if mode == "prefix":
        return insert + " " + text, kinds
    return text.rstrip() + " " + insert, kinds
