"""EXPLORATORY (not pre-registered), TUNING products only: how often do real short/generic reviews
look like template matches if the eligibility gate is removed?"""

import json
from pathlib import Path

import numpy as np
from app.core.detectors.campaign_signals import CampaignParams, _shingles, jaccard

from benchmark.campaign_eval.evaluate import load_pickle

split = json.loads(Path("reports/campaign_eval/split.json").read_text())["products"]["tuning"]
streams = {}
for c in ("amazon", "sephora"):
    streams.update(load_pickle(f"streams_{c}.pkl"))
UNG = CampaignParams(min_words_template=1, min_chars_template=1)
rng = np.random.default_rng(42)
n = hit4 = hit6 = hit9 = 0
n_short_reviews = n_reviews = 0
for k in split:
    st = streams[k]
    short = [i for i, t in enumerate(st.text) if len(t.split()) < 6 or len(t) < 30]
    n_short_reviews += len(short)
    n_reviews += len(st)
    if len(short) < 2:
        continue
    for _ in range(min(300, len(short) * 3)):
        i, j = rng.choice(short, size=2, replace=False)
        a, b = _shingles(st.text[int(i)], UNG), _shingles(st.text[int(j)], UNG)
        if a is None or b is None:
            continue
        s = jaccard(a, b)
        n += 1
        hit4 += s >= 0.4
        hit6 += s >= 0.6
        hit9 += s >= 0.99
print(
    json.dumps(
        {
            "tuning_streams": len(split),
            "reviews": n_reviews,
            "short_reviews": n_short_reviews,
            "short_share": round(n_short_reviews / n_reviews, 4),
            "random_same_product_short_pairs": n,
            "ungated_pair_sim_ge_0.4": hit4 / n,
            "ungated_pair_sim_ge_0.6": hit6 / n,
            "ungated_pair_identical": hit9 / n,
        }
    )
)
