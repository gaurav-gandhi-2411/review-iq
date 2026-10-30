"""Deterministic margin model driven by docs/pricing.json (the pricing single source of truth).

Formulas (all USD; INR prices are converted at fx_inr_per_usd):
    fixed per customer = F / N                F = sum(fixed_monthly_usd) [+ Resend Pro]
    variable           = quota * utilisation * blended_cost_per_extraction
    payment            = payment_fee_fraction * price      (3% fee + 18% GST on the fee)
    margin             = (price - variable - F/N - payment) / price
    customers for target margin m: N >= F / (price * (1 - fee - m) - variable)

Worst case = every customer uses 100% of quota. Typical = typical_utilisation_fraction
(an ASSUMPTION, not a measurement). All customers of a table row share one tier unless the row
is a mix. The free tier and Agency ("Talk to us", no list price) carry no price here and are
excluded from the margin tables.

Usage: python scripts/cost_model.py            # print the tables
       python scripts/cost_model.py --write    # regenerate PRICING blocks in docs/cost-model.md
       python scripts/cost_model.py --check    # exit 1 if those blocks are stale
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = REPO_ROOT / "docs" / "pricing.json"
DOC_PATH = REPO_ROOT / "docs" / "cost-model.md"
PAID = ("starter", "growth", "scale")

BLOCK_RE = re.compile(
    r"(?P<start><!--\s*PRICING:START:(?P<name>[\w.-]+)\s*-->)(?P<body>.*?)"
    r"(?P<end><!--\s*PRICING:END\s*-->)",
    re.DOTALL,
)


def load(path: Path = PRICING_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fixed_total(p: dict[str, Any], resend_pro: bool = False) -> float:
    total = sum(p["fixed_monthly_usd"].values())
    return round(total + (p["resend_pro_monthly_usd"] if resend_pro else 0.0), 2)


def usd_price(p: dict[str, Any], tier: str, currency: str) -> float:
    t = p["tiers"][tier]
    return float(t["usd"]) if currency == "usd" else t["inr"] / p["fx_inr_per_usd"]


def margin(
    p: dict[str, Any], price: float, quota: int, n: float, util: float, fixed: float
) -> float:
    variable = quota * util * p["blended_cost_per_extraction_usd"]
    cost = variable + fixed / n + p["payment_fee_fraction"] * price
    return (price - cost) / price


def customers_for_margin(
    p: dict[str, Any], price: float, quota: int, util: float, fixed: float, target: float
) -> int | None:
    variable = quota * util * p["blended_cost_per_extraction_usd"]
    room = price * (1 - p["payment_fee_fraction"] - target) - variable
    if room <= 0:
        return None  # no customer count reaches the target: per-customer room is non-positive
    return math.ceil(round(fixed / room, 9))


def mix_margin(
    p: dict[str, Any], weights: list[float], currency: str, n: int, util: float, fixed: float
) -> float:
    rev = sum(w * usd_price(p, t, currency) for w, t in zip(weights, PAID, strict=True))
    var = sum(w * p["tiers"][t]["quota"] * util for w, t in zip(weights, PAID, strict=True))
    var *= p["blended_cost_per_extraction_usd"]
    cost = var + fixed / n + p["payment_fee_fraction"] * rev
    return (rev - cost) / rev


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _md(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _n_cell(n: int | None) -> str:
    return "never" if n is None else str(n)


def block_inputs(p: dict[str, Any]) -> str:
    rows = []
    for t in p["tiers"].values():
        usd = "Talk to us" if t["usd"] is None else f"${t['usd']}"
        inr = "custom" if t["inr"] is None else f"Rs {t['inr']:,}"
        plus = "+" if t.get("quota_is_minimum") else ""
        rows.append([t["label"], f"{t['quota']:,}{plus}", usd, inr])
    f0, f1 = fixed_total(p), fixed_total(p, True)
    return (
        _md(["Tier", "Quota (reviews/mo)", "USD/mo", "INR/mo"], rows)
        + f"\n\nBlended cost ${p['blended_cost_per_extraction_usd']}/extraction; FX Rs "
        f"{p['fx_inr_per_usd']}/USD; payment fee {p['payment_fee_fraction'] * 100:.2f}%; fixed cost "
        f"F = ${f0:.2f}/mo (Resend free) or ${f1:.2f}/mo (Resend Pro); typical utilisation "
        f"{p['typical_utilisation_fraction'] * 100:.0f}% (assumption)."
    )


def block_margins(p: dict[str, Any]) -> str:
    ns = p["customer_counts"]
    f = fixed_total(p)
    typ = p["typical_utilisation_fraction"]
    out = []
    for label, util in (
        ("Worst case (100% of quota used)", 1.0),
        (f"Typical case ({typ * 100:.0f}% of quota used, ASSUMPTION)", typ),
    ):
        rows = []
        for cur in ("usd", "inr"):
            for t in PAID:
                price = usd_price(p, t, cur)
                q = p["tiers"][t]["quota"]
                tier = p["tiers"][t]
                tag = f"${tier['usd']}" if cur == "usd" else f"Rs {tier['inr']:,}"
                rows.append(
                    [f"{tier['label']} {cur.upper()} ({tag})"]
                    + [_pct(margin(p, price, q, n, util, f)) for n in ns]
                )
        out.append(
            f"**Gross margin, {label}**, all customers on one tier, F = ${f:.2f}\n\n"
            + _md(["Tier"] + [f"{n} cust" for n in ns], rows)
        )
    return "\n\n".join(out)


def block_mixes(p: dict[str, Any]) -> str:
    ns = p["customer_counts"]
    f = fixed_total(p)
    out = []
    for label, util in (
        ("worst case", 1.0),
        ("typical case (assumption)", p["typical_utilisation_fraction"]),
    ):
        rows = []
        for name, w in p["mixes_starter_growth_scale"].items():
            for cur in ("usd", "inr"):
                rows.append(
                    [f"{name} {cur.upper()}"]
                    + [_pct(mix_margin(p, w, cur, n, util, f)) for n in ns]
                )
        out.append(
            f"**Gross margin for tier mixes (Starter/Growth/Scale), {label}**\n\n"
            + _md(["Mix"] + [f"{n} cust" for n in ns], rows)
        )
    return "\n\n".join(out)


def block_breakeven(p: dict[str, Any]) -> str:
    tgt = p["target_margin_fraction"]
    typ = p["typical_utilisation_fraction"]
    hdr = [
        "Tier",
        "worst, Resend free",
        "worst, Resend Pro",
        "typical, Resend free",
        "typical, Resend Pro",
    ]

    def cells(price: float, q: int) -> list[str]:
        return [
            _n_cell(customers_for_margin(p, price, q, u, fixed_total(p, rp), tgt))
            for u in (1.0, typ)
            for rp in (False, True)
        ]

    rows = []
    for cur in ("usd", "inr"):
        for t in PAID:
            q = p["tiers"][t]["quota"]
            rows.append(
                [f"{p['tiers'][t]['label']} {cur.upper()}"] + cells(usd_price(p, t, cur), q)
            )
    q = p["tiers"]["starter"]["quota"]
    alt = []
    for name, spec in p["alt_starter_prices"].items():
        price = spec["usd"] if "usd" in spec else spec["inr"] / p["fx_inr_per_usd"]
        label = "$15" if name == "usd_15" else "Rs 999"
        alt.append([f"Starter at {label} (${price:.2f})"] + cells(price, q))
    return (
        f"Customers needed (all on one tier, fixed cost split by customer count) to reach "
        f"{tgt * 100:.0f}% gross margin\n\n"
        + _md(hdr, rows)
        + "\n\nStarter price alternatives (5,000 quota)\n\n"
        + _md(hdr, alt)
    )


BLOCKS = {
    "inputs": block_inputs,
    "margins": block_margins,
    "mixes": block_mixes,
    "breakeven": block_breakeven,
}


def render_doc(text: str, p: dict[str, Any]) -> str:
    def _sub(m: re.Match[str]) -> str:
        fn = BLOCKS.get(m.group("name"))
        if fn is None:
            return m.group(0)
        return f"{m.group('start')}\n{fn(p)}\n{m.group('end')}"

    return BLOCK_RE.sub(_sub, text)


def main(argv: list[str]) -> int:
    p = load()
    text = DOC_PATH.read_text(encoding="utf-8")
    new = render_doc(text, p)
    if "--write" in argv:
        DOC_PATH.write_text(new, encoding="utf-8", newline="\n")
        print(f"wrote {DOC_PATH}")
        return 0
    if "--check" in argv:
        if new != text:
            print("FAIL: docs/cost-model.md PRICING blocks are stale; run cost_model.py --write")
            return 1
        print("OK: docs/cost-model.md PRICING blocks match docs/pricing.json")
        return 0
    for name, fn in BLOCKS.items():
        print(f"## {name}\n{fn(p)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
