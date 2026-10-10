"""CI check: every surface that states a price or quota must agree with docs/pricing.json.

Surfaces covered (and nothing else -- a price typed anywhere not listed here is NOT checked):
  1. site/index.html pricing section: each `<div class="plan ...">` block's tier name, quota,
     USD price and INR price (parsed, so the site needs no markers and no edits).
  2. docs/cost-model.md: the generated `PRICING` blocks must equal what scripts/cost_model.py
     renders from the JSON (so a hand edit of a block, or a JSON change without re-rendering,
     fails).
  3. docs/payments-readiness.md: the one sentence quoting the site prices.
  4. app/api/bff/router.py PLAN_QUOTA_LIMITS: pinned to the JSON's placeholder quotas (keyed by
     DB plan names, not the public tiers; see the JSON note), and its "free" entry must equal
     the public Free quota (tiers.free.quota).
  5. app/core/pricing.py USD_TO_INR_RATE == JSON fx_inr_per_usd.
  6. eval/results/token_cost_measurement_n106.json blended cost, rounded to 6 dp, == JSON.

Any surface that cannot be read or parsed is a FAILURE, never a silent pass.
Usage: python scripts/check_pricing_consistency.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cost_model  # noqa: E402  # sibling script, imported after the sys.path tweak above

PLAN_RE = re.compile(r'<div class="plan(?:\s[^"]*)?">(.*?)</div>', re.DOTALL)


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def parse_site_plans(html: str) -> dict[str, dict[str, int | None]]:
    plans: dict[str, dict[str, int | None]] = {}
    for body in PLAN_RE.findall(html):
        name = re.search(r"<h3>([^<]+)</h3>", body)
        vol = re.search(r'<p class="vol">([\d,]+)(\+?) reviews/mo</p>', body)
        if not (name and vol):
            continue
        usd = re.search(r'<p class="price">\$([\d,]+)<small', body)
        inr = re.search(r'<p class="inr">&#8377;([\d,]+)/mo</p>', body)
        plans[name.group(1).strip().lower()] = {
            "quota": _num(vol.group(1)),
            "usd": _num(usd.group(1)) if usd else None,
            "inr": _num(inr.group(1)) if inr else None,
        }
    return plans


def check_site(p: dict, html: str) -> list[str]:
    errs: list[str] = []
    plans = parse_site_plans(html)
    if set(plans) != set(p["tiers"]):
        errs.append(f"site tiers {sorted(plans)} != pricing.json tiers {sorted(p['tiers'])}")
    for key, t in p["tiers"].items():
        s = plans.get(key)
        if s is None:
            continue
        for field in ("quota", "usd", "inr"):
            if s[field] != t[field]:
                errs.append(f"site {key}.{field}={s[field]} but pricing.json says {t[field]}")
    return errs


def check_doc(p: dict, text: str) -> list[str]:
    if cost_model.render_doc(text, p) != text:
        return ["docs/cost-model.md PRICING blocks are stale: run scripts/cost_model.py --write"]
    names = {m.group("name") for m in cost_model.BLOCK_RE.finditer(text)}
    missing = set(cost_model.BLOCKS) - names
    return [f"docs/cost-model.md is missing PRICING block(s): {sorted(missing)}"] if missing else []


def check_payments_doc(p: dict, text: str) -> list[str]:
    errs = []
    for key in ("starter", "growth", "scale"):
        t = p["tiers"][key]
        want = f"{t['label']}\n${t['usd']}/INR {t['inr']:,}"
        flat = re.sub(r"\s+", " ", text)
        if re.sub(r"\s+", " ", want) not in flat:
            errs.append(
                f"docs/payments-readiness.md does not quote {t['label']} ${t['usd']}/INR {t['inr']:,}"
            )
    return errs


def parse_plan_quota_limits(src: str) -> dict[str, int]:
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "PLAN_QUOTA_LIMITS"
        ):
            return ast.literal_eval(node.value)
    raise ValueError("PLAN_QUOTA_LIMITS not found")


def check_free_quota(p: dict, limits: dict[str, int]) -> list[str]:
    """The enforced free ceiling must equal the public Free plan's quota."""
    if "free" not in limits:
        return ["PLAN_QUOTA_LIMITS has no 'free' entry"]
    want = p["tiers"]["free"]["quota"]
    if limits["free"] != want:
        return [f"PLAN_QUOTA_LIMITS['free']={limits['free']} != pricing.json Free quota {want}"]
    return []


def parse_fx(src: str) -> float:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "USD_TO_INR_RATE" for t in node.targets
        ):
            return float(ast.literal_eval(node.value))
    raise ValueError("USD_TO_INR_RATE not found")


def run(root: Path = REPO_ROOT) -> list[str]:
    errs: list[str] = []
    try:
        p = cost_model.load(root / "docs" / "pricing.json")
        errs += check_site(p, (root / "site" / "index.html").read_text(encoding="utf-8"))
        errs += check_doc(p, (root / "docs" / "cost-model.md").read_text(encoding="utf-8"))
        errs += check_payments_doc(
            p, (root / "docs" / "payments-readiness.md").read_text(encoding="utf-8")
        )
        limits = parse_plan_quota_limits(
            (root / "app" / "api" / "bff" / "router.py").read_text(encoding="utf-8")
        )
        errs += check_free_quota(p, limits)
        if limits != p["billing_code_placeholder_quotas"]:
            errs.append(
                f"PLAN_QUOTA_LIMITS {limits} != pricing.json billing_code_placeholder_quotas "
                f"{p['billing_code_placeholder_quotas']}"
            )
        fx = parse_fx((root / "app" / "core" / "pricing.py").read_text(encoding="utf-8"))
        if fx != p["fx_inr_per_usd"]:
            errs.append(
                f"app/core/pricing.py USD_TO_INR_RATE {fx} != pricing.json {p['fx_inr_per_usd']}"
            )
        meas = json.loads(
            (root / "eval" / "results" / "token_cost_measurement_n106.json").read_text(
                encoding="utf-8"
            )
        )["blended_cost_per_extraction"]["usd"]
        if round(meas, 6) != p["blended_cost_per_extraction_usd"]:
            errs.append(
                f"measured blended cost {round(meas, 6)} != pricing.json "
                f"{p['blended_cost_per_extraction_usd']}"
            )
    except (OSError, ValueError, KeyError, SyntaxError) as exc:  # fail closed (rule 98a)
        errs.append(f"could not verify a surface: {type(exc).__name__}: {exc}")
    return errs


def main() -> int:
    errs = run()
    if errs:
        print("FAIL: pricing surfaces disagree with docs/pricing.json:")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(
        "OK: site, cost-model doc, payments doc, billing code and pricing.py agree with pricing.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
