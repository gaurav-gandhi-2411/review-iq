# R1+ on the live classifier: routing parity (S35)

**Question.** The served router is now R1+ (`app/core/classify/encoder_first.py::route_r1_plus`, #655): R1 OR p(needs_action = yes)
>= 0.07. #655 shows served R1+ equals the evaluated R1+ bit for bit on the stored A17/A18 logits (pinned numerics). The LIVE
classifier does not reproduce pinned numerics exactly: S31 measured served vs offline probabilities bit-identical on about 87 percent
of the 757 sealed reviews, max difference 0.19 to 0.31 (`reports/serving/classify_redeploy_s31.md`). A drift across tau moves a
route without changing any label, so the S31 label check could not see it. This measures it.

**Method.** `scripts/classify_live_parity.py` now also reports `routes`: for R1 and R1+, the items whose route differs between the
served answer and the offline reference (the same pred files and decision rule as before), with both p_yes values. Run once,
2026-11-02, against the live revision `review-iq-classify-00002-zh5` (unchanged since S31), one review per request, project Owner
identity token (no binding change), 757 sealed reviews.

**Result** (`reports/serving/classify_live_parity_s35.json`):

| | Value |
|---|---|
| Requests OK / errors | 757 / 0 |
| Thresholds hash served = committed | yes |
| Decision mismatches (of 3,028) | 1 (`broad_intent`, which no router reads; S31: 5) |
| **R1 route flips** | **0** (214 routed offline and served) |
| **R1+ route flips** | **0** (238 routed offline and served) |
| Client latency p50 / p95 | 372.5 / 852.9 ms (max 15.7 s = one cold start) |

**Reading.** On this revision the live service routes the 757 sealed reviews exactly as the offline reference does, under both R1 and
R1+. This is one run on one revision. 757 reviews cannot rule out a rare flip: with 0 of 757 observed, the 95 percent upper bound on
the flip rate is about 0.4 percent (rule of three). Re-run after any classifier redeploy, before the router is enabled.

Re-run:

```
CLASSIFY_ID_TOKEN=$(gcloud auth print-identity-token --account=gaurav.gandhi1129@gmail.com) \
PYTHONPATH=. uv run --with scikit-learn python scripts/classify_live_parity.py --url <review-iq-classify URL> \
    --texts <private scale.jsonl> --out reports/serving/classify_live_parity_<session>.json
```
