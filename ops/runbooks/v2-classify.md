# POST /v2/classify runbook (S24 A6, rewritten S26, private-service design S27, DEPLOYED S29) -- OFF on `review-iq`; the private service is live and nothing calls it

## What is served

Run 2 (`reports/intent/model_report.md`), static-int8 ONNX (`onnx_static_int8`, mixed precision `m1`): multilingual-e5-base fine-tuned
multi-task; tasks `needs_action`, `broad_intent`, `sentiment`, `urgency`. Run 3 is a registered negative result and is not served.
Why static (and not torch dynamic int8): dynamic int8 picks activation scales per batch, so scoring one review at a time flipped 166 of
3028 sealed decisions against the batch-128 offline evaluation. Static scales come from TRAINING-text calibration, so a result does not
depend on batch-mates. Method, pre-registered criteria and Addendum 1: `docs/specs/review-intent-serving.md`. Numbers:
`reports/serving/v2_classify_onnx.{md,json}`. Labels are SILVER (four-judge consensus), never human truth.

## Decisions (GG, S26)

- A SEPARATE, PRIVATE Cloud Run service `review-iq-classify` (S27): 1 vCPU, 2 GiB, `min-instances 0` (scale to zero), concurrency 4,
  `--no-allow-unauthenticated`, invoked only by the review-iq service account (`roles/run.invoker`) with a Google identity token.
  It runs `app.classify_service` (no API key, no database, no secrets); Cloud Run IAM is the only authentication. It is not added to
  the 1 GiB `review-iq` service (the container peaks near 0.85 to 0.9 GB).
- DEPLOYED S29 (GG approved, about 4 cents a month of registry storage): service `review-iq-classify`, region asia-south1, revision
  `review-iq-classify-00001-4rt`, max-instances 2. Nothing in production calls it: `ENABLE_ENCODER_FIRST` is OFF and
  `CLASSIFY_SERVICE_URL` is unset on `review-iq`. Do not enable the router before the numerics item below is resolved.
- `model.onnx` (415 MB) and `hf/tokenizer.json` are BAKED into the image from a build context; no bucket download. The deploy script pins
  both sha256 values, so a wrong or truncated weight file stops the deploy.
- `requirements-classify.txt` is hash-pinned (`uv pip compile --generate-hashes`); the Docker install uses `--require-hashes`.

## Decision rule (what a client receives)

Per review and task: `label` is a class or `abstained`. (1) calibrated probabilities = softmax(logits / T) on a 6-decimal grid;
(2) `candidate_label` from `engine.intent.decide.decide` in `threshold` mode: `urgency=high` when p(high) >= its validation-fitted
threshold, otherwise argmax over the other classes, never argmax on a rare class; (3) `label` = `abstained` when max probability <
`abstain_below` (lowest validation confidence whose Wilson lower bound for precision reaches 0.95). A review flagged `urgency=high` by
the threshold with low overall confidence has `label: abstained` and `candidate_label: high`; safety consumers must read
`candidate_label`. Thresholds: `app/core/classify/thresholds_run2_onnx_static_int8.json` (validation only; `scripts/build_classify_thresholds.py`);
every response carries their sha256. Each review runs alone in its forward pass.

## Build and deploy (deliberate, reviewed; not run by CI; NO local Docker build)

```
CONFIRM_REGISTRY_COST=yes bash ops/deploy/deploy-classify.sh D:/ml-cache/s25/classify_artifacts_onnx
```

Guards in the script: clean tree, HEAD an ancestor of `origin/main`, both weight hashes match, explicit cost confirmation. It builds in
Cloud Build (a `git archive` of HEAD plus the two pinned weight files; about 2 minutes on the default pool, inside the free 2,500
build-minutes a month), pushes to `cloud-run-source-deploy`, DELETES the uploaded weights from the Cloud Build bucket, creates the
service account `review-iq-classify` (no roles, no secrets) and deploys privately (`--no-allow-unauthenticated`) by image digest.
The S29 deploy ran these same steps by hand (the script was rewritten from a local-Docker build afterwards); the script itself has
not been executed end to end.

ROLLBACK: `gcloud run services delete review-iq-classify --region=asia-south1 --project=reviewiq-prod-260813 --account=gaurav.gandhi1129@gmail.com`.
Nothing else depends on it. The image can stay in the registry (about 4 cents a month) or be deleted with
`gcloud artifacts docker images delete <image>@<digest>`. To stop spend without deleting, nothing is needed: min-instances is 0.

Access model: the deploy script grants only `roles/run.invoker` on this service to `review-iq-runner@`, then asserts the IAM
policy has exactly one service-account invoker and no allUsers / allAuthenticatedUsers, and that an unauthenticated request is
refused (401/403). There is no DB secret and no API key on this service.

Cost: the push adds the image to Artifact Registry. After the S27 cleanup the project holds about 0.79 GB (0.5 GB free tier), so the new
image is billed storage (BELIEVED about $0.10 per GB-month; price not verified against Google's page).

## Post-deploy check (re-runs batch-1 sealed parity against the LIVE URL)

```
CLASSIFY_ID_TOKEN=$(gcloud auth print-identity-token --account=gaurav.gandhi1129@gmail.com) \
PYTHONPATH=. python scripts/classify_live_parity.py --url <url> --texts <scale.jsonl> --out reports/serving/classify_live_parity.json
```

Sends each of the 757 sealed reviews alone, compares every served decision with the offline reference, checks `thresholds_sha256`,
reports client latency p50/p95. Exit 1 on any mismatch, error or sha mismatch. The token is the only credential. A USER account must
not pass `--audiences` (gcloud refuses it and the token is empty: every request then fails with 403). The project Owner can invoke
the service without a binding (verified S29: with no binding for that account the call still returned 200), so no extra grant is
needed; the service-level policy stays at exactly one invoker, `review-iq-runner@`. Rehearsed in S27 against a local container.

S29 RESULT against the live service: 757 reviews, 0 errors, thresholds hash matched, **81 of 3028 decisions differ (67 items): the
parity target of 0 was NOT met**. See the numerics section.

## Measured (local Docker, NOT Cloud Run)

1 vCPU, 1 GiB: p50/p95 137.2 / 245.8 ms per review, cold start 7.5 s, peak RSS 902 MB (`reports/serving/v2_classify_onnx.md`). S26,
1 vCPU, 2 GiB, hash-pinned image, over HTTP, 757 reviews one per request: 0 mismatches of 3028 decisions, client p50 161.5 ms, p95 302.6 ms,
cgroup peak 846 MB, not OOM-killed.

## Measured on Cloud Run (S29, VERIFIED unless marked)

- Cold start, first request after scale-to-zero: **15.2 s** (one sample, client side, includes TLS and container start, `--cpu-boost`).
- Warm, one review per request: 30 identical short requests p50 124 ms, p95 136 ms (client, Mumbai region from the laptop); 757 real
  sealed reviews p50 308 ms, p95 616 ms client side. Server side (Cloud Monitoring `request_latencies` during the parity runs, 1 vCPU): p50
  about 206 ms, p95 about 476 ms, p99 about 534 ms.
- Peak memory: p99 container memory utilization 0.62 of 2 GiB (about 1.24 GiB) over the parity runs; no OOM.
- Registry: image 528.1 MB of compressed layers, 481.5 MB unique to it; repository total 914.9 MB unique layer bytes, about 0.41 GB
  over the free 0.5 GB, so about 4 cents a month (BELIEVED price $0.10 per GB-month; not checked against Google's page).
- Cost of the service: scale to zero; request cost is covered by the free tier at any plausible volume (BELIEVED Cloud Run prices).
- Access: the IAM policy has exactly one binding, `roles/run.invoker` for `review-iq-runner@`; no allUsers / allAuthenticatedUsers;
  an unauthenticated POST returns 403 (the Google frontend answers 404 on reserved paths such as `/healthz`, so health checks must not use them).

## Numerics: the int8 kernels give different results on the Cloud Run CPU (OPEN, found S29)

Against the offline reference (computed on the laptop) the live service disagrees on 81 of 3028 sealed decisions (2.7 percent, 67 of
757 reviews), all of them at a class/abstained boundary. Per-review maximum probability difference: median 0.008, p95 0.143, max
0.405. The service is deterministic (8 worst items, 3 calls each, identical) and the laptop reproduces the offline reference exactly
(difference 0.0, 0 mismatches), so the difference is the host CPU's INT8 path. On 200 sealed reviews, setting ONNX Runtime's
`session.x64quantprecision=1` on the laptop brings it to the Cloud Run numbers (median difference 0, p95 0.0009, 1 label mismatch of
800, against 21 mismatches with the default). The mechanism (an AVX2 u8s8 saturating kernel on the laptop that the Cloud Run CPU does
not use) is BELIEVED, not verified: the Cloud Run CPU flags were not read.

Effect on the router (silver labels, nothing tuned): on the 757 sealed reviews R1 routes 0.2761 (served) vs 0.2748 (offline); needs_action=yes
recall 104/111 = 0.937 [0.876, 0.969] served vs 106/111 = 0.955 offline; on the 2,000 fresh items criterion A recomputed on served
decisions is 293/306 = 0.9575 [0.9287, 0.975] (met). So the Amendment 15 verdict holds on the deployed numerics, but the offline
reference, the validation-fitted thresholds and every sealed number were produced on the other numerics. Before enabling the router:
decide the reference numerics (set `x64quantprecision=1` everywhere, regenerate the validation predictions and thresholds, re-run the
sealed evaluation) or accept CPU-dependent decisions and gate on the served-numerics evaluation. `scripts/classify_live_parity.py`
will keep reporting mismatches until then.

## NOT verified

How a Cloud Run instance behaves under concurrent load (all measurements are one request at a time); the exact CPU model the
service runs on; Cloud Run prices; any quality claim on real production traffic (silver sealed set only).
