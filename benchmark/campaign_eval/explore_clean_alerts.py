"""EXPLORATORY (not pre-registered), VALIDATION products only: run the PRODUCTION scan_stream
with the shipped defaults over the clean validation streams and (a) check it reproduces the
harness's false-alert count for the frozen row, (b) tabulate which signals fired on those clean
alerts. Writes reports/campaign_eval/exploratory_clean_alerts_validation.json."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.core.detectors.campaign import scan_stream
from app.core.detectors.campaign_signals import DEFAULT_PARAMS, ProductStream

from benchmark.campaign_eval.evaluate import MONTH_S, clean_points, load_pickle

OUT = Path("reports/campaign_eval")


def main() -> None:
    keys = json.loads((OUT / "split.json").read_text())["products"]["validation"]
    streams = {}
    for corpus in ("amazon", "sephora"):
        streams.update(load_pickle(f"streams_{corpus}.pkl"))
    n_alerts, months = 0, 0.0
    sig = Counter()
    for k in keys:
        st = streams[k]
        ps = ProductStream(st.reviews(), DEFAULT_PARAMS)
        alerts = scan_stream(ps)
        n_alerts += len(alerts)
        months += clean_points(st)[1]
        sig.update("+".join(sorted(a.fired)) for a in alerts)
    res = {
        "production_scan_stream_clean_alert_episodes": n_alerts,
        "product_months": months,
        "false_alerts_per_product_month": n_alerts / months,
        "fired_signal_combinations": dict(sig.most_common()),
        "months_unit_seconds": MONTH_S,
    }
    (OUT / "exploratory_clean_alerts_validation.json").write_text(
        json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
