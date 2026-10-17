# POST /v2/classify runbook (S24 A6, updated S25) -- built, OFF, not deployed

## S25 update: the served model is now static-int8 on ONNX Runtime (supersedes the torch sections below)

Why: torch dynamic int8 picks activation scales per batch, so scoring one review at a time flipped 166 of 3028 sealed decisions
(answer versus abstain) relative to the batch-128 offline evaluation. Static quantisation fixes the scales from TRAINING-text
calibration, so a review's result no longer depends on its batch-mates. Method, the pre-registered criteria and the post-hoc
Addendum 1 (the three registered candidates missed their 0.90 validation threshold; the second feed-forward MatMul was the cause;
mixed precision m1 chosen on VALIDATION only): `docs/specs/review-intent-serving.md`. Numbers: `reports/serving/v2_classify_onnx.{md,json}`.

- Artifacts under `CLASSIFY_ARTIFACT_DIR`: `model.onnx` (415 MB) and `hf/tokenizer.json`. `model.onnx` present selects the ONNX backend and
  `app/core/classify/thresholds_run2_onnx_static_int8.json` (refit on validation for this model); otherwise the torch backend as before.
- Image: `Dockerfile.classify` (onnxruntime 1.31.0, tokenizers 0.22.2, no torch/transformers). Build with
  `--build-context artifacts=D:/ml-cache/s25/classify_artifacts_onnx`. Rebuild the model with `scripts/onnx_static_quantize.py`, score with
  `scripts/onnx_eval.py`, write pred files with `scripts/onnx_pred_files.py`, thresholds with `scripts/build_classify_thresholds.py --variant onnx_static_int8`.
- Threads: ONNX Runtime sizes its pool from the host's cores, not the CPU quota; the backend reads the cgroup quota
  (`cgroup_cpus`). Without it a 1 vCPU container measured over a second per review. `CLASSIFY_TORCH_THREADS` (name kept) overrides.
- Measured locally (Docker, 1 vCPU, 1 GiB, loaded workstation, NOT Cloud Run): fits without OOM, peak RSS about 0.9 GB of 1.07 GB, so
  co-hosting inside the existing 1Gi service leaves almost no headroom for that service's own work; a separate 1Gi service is the
  minimum, 2Gi is the comfortable choice. Cloud Run behaviour remains unmeasured (BELIEVED).
- The 1 GiB question is answered for the container, not for the shared `review-iq` service.

Status: the endpoint exists on `main` behind `ENABLE_V2_CLASSIFY` (default false: the route answers 404 `Not found.` to
everyone, before auth, like the other flagged v2 endpoints). Nothing in this change deploys it, edits a workflow, or sets the
flag anywhere. The default `Dockerfile` image does not contain the model, torch or `engine/`; with the flag off the app never
imports them (tested: `test_default_app_import_needs_neither_torch_nor_the_runtime`).

## What it serves

Run 2 (`reports/intent/model_report.md`), CPU variant `int8_ffn_full`: multilingual-e5-base fine-tuned multi-task, torch dynamic
int8 on the encoder feed-forward Linears and the word-embedding table (attention and heads stay fp32), exactly the export of
`engine/intent/quantize.py`. Tasks: `needs_action`, `broad_intent`, `sentiment`, `urgency`. Run 3 is a registered negative result and is not served.

Why this variant: no document designates a serving variant (the report compares six and recommends none). The rule applied was
the one given in the task: best sealed-set agreement with `fp32_cpu` under the SERVED decision rule (per-class threshold plus
abstention), computed from the committed pred files with `engine.intent.decide`
(`scripts/classify_variant_agreement.py`; per-variant values in `variant_selection` of `reports/serving/v2_classify_bench.json`).
`int8_ffn_full` had the best mean agreement of the five int8 variants; the margin over the next
(`int8_perchannel_full`) is small and noise-sized. Its cost is a 369 MB state dict instead of 283 MB for `int8_perchannel_full`. Switching variant means a new
thresholds file and a new parity run, not a flag.

## Decision rule (what a client receives)

Per review and task: `label` is a class or `abstained`. Computed exactly as the offline evaluation does:
1. calibrated probabilities = softmax(logits / T) rounded to 6 decimals (T and everything below fitted on VALIDATION only);
2. `candidate_label` = `engine.intent.decide.decide` in `threshold` mode: `urgency=high` is predicted when p(high) >= its
   validation-fitted threshold, otherwise argmax over the other classes. Never argmax on a rare class;
3. `label` = `abstained` when max probability < `abstain_below` (the conservative p0.95 rule: lowest validation confidence whose
   Wilson lower bound for precision reaches 0.95), else `candidate_label`.

Consequence to know: a review flagged `urgency=high` by the threshold but with low overall confidence has `label: abstained`
and `candidate_label: high`. Consumers that care about safety must read `candidate_label`, not only `label`. This is the rule
the sealed-set coverage and precision were measured under; changing it changes those numbers.

The thresholds are in `app/core/classify/thresholds_run2_int8_ffn_full.json` (small, validation-derived, committed;
regenerate with `scripts/build_classify_thresholds.py`, which cross-checks against the evaluation JSON). Every response
carries its sha256. The run-2 evaluation was argmax-era, so the rare-class threshold is not recorded offline: it is the
evaluator's `fit_thresholds` on the same validation probabilities.

## One review per forward (important)

Dynamic int8 quantises activations per tensor, so a logit depends on which other items share its padded batch. The offline
evaluation used batches of 128. Served logits therefore cannot be bit-identical to the evaluation unless batched the same way,
which an API cannot do. The endpoint runs each review alone (no padding, no co-batching), so a review's result never depends on
the rest of the request, and CPU throughput is not worse than batching. The measured consequence is in
`reports/serving/v2_classify_bench.md`: batched exactly like the offline run the served code reproduces every sealed decision;
per review, a minority of decisions flip, all between answering and abstaining, none between two classes.

## Artifacts (not in git)

Layout expected under `CLASSIFY_ARTIFACT_DIR` (default `/app/artifacts/classify`):

```
model_int8.pt     copy of D:/ml-cache/s23/quant/int8_ffn_full.pt (run 2; produced by engine/intent/quantize.py)
hf/               config.json tokenizer.json tokenizer_config.json special_tokens_map.json from intfloat/multilingual-e5-base
```

Local copy used for the benchmark: `D:/ml-cache/s24/classify_artifacts`. How the files reach a real service (baked into the
image from a build context, as `Dockerfile.classify` does, or fetched from a bucket at start) is NOT decided here: shipping
369 MB of weights through CI needs a storage decision and a checksum pin that belong to the deploy change.

## Build and run locally

```
docker build -f Dockerfile.classify -t review-iq-classify:local --build-context artifacts=D:/ml-cache/s24/classify_artifacts .
```

`requirements-classify.txt` (torch 2.6.0 CPU, transformers 4.57.6, numpy 2.4.4) is installed on top of the locked app
dependencies; it is not in `uv.lock`, so a deploy change should decide whether to lock it. Benchmark and parity:
`scripts/bench_classify.py`, `scripts/classify_parity.py` (pytest: `tests/engine/test_classify_parity.py`, skips without
artifacts and the private texts).

## Enabling it later (a deliberate, reviewed change)

1. Deploy `Dockerfile.classify` as a SEPARATE Cloud Run service (see resources). 2. Set `ENABLE_V2_CLASSIFY=true` on that service
only. 3. Smoke: `POST /v2/classify` with one review; expect 200 and `thresholds_sha256` equal to the committed file's sha256.
4. Watch `review_iq_classify_requests_total{outcome}` and `review_iq_classify_latency_ms`. Missing artifacts or torch return 503
with `code` `classify_artifacts_missing` / `classify_runtime_missing` and `Retry-After: 60`; they never crash the app.
Rollback: unset the flag (404 again) or route no traffic to the service.

## Resources

See `reports/serving/v2_classify_bench.md` for measured image size, cold start, memory and latency (local Docker, loaded
machine). The existing public service is configured at 1 vCPU / 1Gi / concurrency 80 (`ops/runbooks/cloud-run-deploy.md`).
Measured: at a 1 GiB limit with no swap this container is OOM-killed on the first request (the steady-state anonymous memory
after load is the binding number), at 2 GiB it runs. It therefore cannot be added to the existing service as configured and must
be its own service with at least 2Gi; the exact figures are in the bench report. Concurrency for this service should be low (the model forward holds a
lock and uses all CPU threads); `CLASSIFY_TORCH_THREADS` sets the torch thread count (0 = torch default).

## NOT verified

- Any Cloud Run behaviour: cold start with a real image pull, CPU throttling, startup probe timing, memory accounting on gen1 vs gen2,
  concurrency, scale-to-zero cost. All numbers are from one local container.
- The torch CPU wheel on the Cloud Run CPU generation (AVX2/AVX512 kernels for the int8 ops can differ from this machine's).
- Hashes/pinning of the image's Python dependencies beyond the pins in `requirements-classify.txt`.
- Any quality claim on real production traffic: the silver sealed set is the only measurement, and the per-item serving mode
  has its own (measured, similar) coverage and precision on it, not the batch-128 numbers in `model_report.md`.
