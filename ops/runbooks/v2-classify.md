# POST /v2/classify runbook (S24 A6, rewritten S26, private-service design S27) -- built, OFF on `review-iq`, service not deployed (DEFERRED)

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
- DEPLOY IS DEFERRED (S27 addendum): no product path calls the classifier yet, so deploying adds registry cost (about 5 cents a month,
  BELIEVED) for no benefit. It returns once the encoder-first pipeline has a measured capacity gain.
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

## Build and deploy (deliberate, reviewed; not run by CI)

```
bash ops/deploy/deploy-classify.sh D:/ml-cache/s25/classify_artifacts_onnx     # prints the image size, stops until CONFIRM_REGISTRY_COST=yes
```

Guards in the script: clean tree, HEAD an ancestor of `origin/main`, both weight hashes match, explicit cost confirmation. It builds,
pushes to `cloud-run-source-deploy`, creates the service account `review-iq-classify` (no roles, no secrets) and
deploys privately (`--no-allow-unauthenticated`). Rollback: `gcloud run services delete review-iq-classify`.

Access model: the deploy script grants only `roles/run.invoker` on this service to `review-iq-runner@`, then asserts the IAM
policy has exactly one service-account invoker and no allUsers / allAuthenticatedUsers, and that an unauthenticated request is
refused (401/403). There is no DB secret and no API key on this service.

Cost: the push adds the image to Artifact Registry. After the S27 cleanup the project holds about 0.79 GB (0.5 GB free tier), so the new
image is billed storage (BELIEVED about $0.10 per GB-month; price not verified against Google's page).

## Post-deploy check (re-runs batch-1 sealed parity against the LIVE URL)

```
CLASSIFY_ID_TOKEN=$(gcloud auth print-identity-token --account=<invoker> --audiences=<url>) \
PYTHONPATH=. python scripts/classify_live_parity.py --url <url> --texts <scale.jsonl> --out reports/serving/classify_live_parity.json
```

Sends each of the 757 sealed reviews alone, compares every served decision with the offline reference, checks `thresholds_sha256`,
reports client latency p50/p95. Exit 1 on any mismatch, error or sha mismatch. The token is the only credential (an account used for
the check needs `run.invoker` on the service). Rehearsed in S27 against a local container of the same image (see STATUS).

## Measured (local Docker, NOT Cloud Run)

1 vCPU, 1 GiB: p50/p95 137.2 / 245.8 ms per review, cold start 7.5 s, peak RSS 902 MB (`reports/serving/v2_classify_onnx.md`). S26,
1 vCPU, 2 GiB, hash-pinned image, over HTTP, 757 reviews one per request: 0 mismatches of 3028 decisions, client p50 161.5 ms, p95 302.6 ms,
cgroup peak 846 MB, not OOM-killed.

## NOT verified

Cloud Run behaviour (image pull and cold start, CPU throttling, startup probe, concurrency, scale-to-zero cost); the int8 kernels on the Cloud
Run CPU generation; any quality claim on real production traffic (silver sealed set only).
