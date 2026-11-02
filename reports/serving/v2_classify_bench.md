# POST /v2/classify: local Docker benchmark and parity (S24 A6)

Provenance: commit `8953ecf4aa3e`; artifacts `reports/serving/v2_classify_bench.json` (assembled from the bench and parity runs named in it). **local Docker, loaded machine, 1 vCPU container, not Cloud Run.** Served model: run 2, int8_ffn_full (torch dynamic int8), tasks needs_action / broad_intent / sentiment / urgency.

## Image

<!-- METRICS:START:image -->
- image size on disk (docker images): 2.21GB (docker images, uncompressed on disk)
- compressed content size (docker image inspect .Size): 654 MB
- of which model file: 369 MB; torch (CPU wheel) in the venv: 686 MB
- build wall time (cold cache): 257 s
<!-- METRICS:END -->

## Container latency, cold start and memory

<!-- METRICS:START:container -->
| Config | Cold start s | of which model load s | Batch 1 p50 / p95 ms (n) | Batch 8 p50 / p95 ms (n) | Peak RSS MB | cgroup peak incl. page cache MB | Limit MB | OOM |
|---|---|---|---|---|---|---|---|---|
| 1 cpu / 2g | 21.34 | 16.33 | 802.9 / 1199.6 (757) | 6192.5 / 7395.8 (200) | 1559 | 1087 | 2147 | false |

Machine: AMD Ryzen 7 6800H (8 cores / 16 threads), Windows 11, Docker Desktop; shared with other running jobs (system CPU load 34 percent when sampled after the run)
<!-- METRICS:END -->

## Does it fit? Memory probes (1 vCPU container, no swap) against the existing service

<!-- METRICS:START:memory -->
| Probe | Memory limit | Result | Idle RSS MB | Peak RSS MB | cgroup peak incl. page cache MB |
|---|---|---|---|---|---|
| first loader (deep-copy quantise, no mmap), first request | 1 GiB | OOM-killed (exit 137) before the first response | n/a | n/a | n/a |
| first loader, full bench | 2 GiB | fits | n/a | 1411 | 1308 |
| final loader (in-place quantise + mmap), first request | 1 GiB | OOM-killed (exit 137) before the first response | 91 | n/a | n/a |
| final loader, first request | 2 GiB | fits | 91 | 1559 | n/a |
| final loader, full bench (958 requests) | 2 GiB | fits, no OOM | 91 | 1559 | 1087 |

Existing public service (ops/runbooks/cloud-run-deploy.md): 1 vCPU, 1Gi, concurrency 80, min 0, max 3 (from the runbook; the live revision was not queried)
<!-- METRICS:END -->

## Parity against the offline evaluation (sealed set)

<!-- METRICS:START:parity -->
- thresholds file equals the offline recomputation bit-for-bit: True (sha256 `25b28d2d8c590cb1`)
- sealed ids: 757; pred-file test items: 763

| Mode | Items | Task decisions | Mismatches | Abstain-vs-answer | Different class | Max abs prob diff |
|---|---|---|---|---|---|---|
| offline_batches | 757 | 3028 | 0 | 0 | 0 | 0.000000 |
| served_per_item | 757 | 3028 | 166 | 166 | 0 | 0.612713 |
| batch_8 | 757 | 3028 | 140 | 140 | 0 | 0.480027 |

In the container (batch 1 over HTTP): 757 items, 164 mismatches vs offline, mismatch set equals the host served_per_item set: False.
<!-- METRICS:END -->

## Coverage and precision against panel-consensus labels (silver, sealed set)

<!-- METRICS:START:coverage -->
| Task | Labelled | Offline (batch 128) coverage / precision | Served per item coverage / precision |
|---|---|---|---|
| needs_action | 717 | 0.8006 / 0.9878 | 0.8298 / 0.9866 |
| broad_intent | 700 | 0.7057 / 0.9757 | 0.7371 / 0.9767 |
| sentiment | 673 | 0.8351 / 0.9804 | 0.8499 / 0.9843 |
| urgency | 703 | 0.7568 / 0.9962 | 0.7681 / 0.9944 |
<!-- METRICS:END -->

## Reading these numbers

- **Fit verdict (measured, local Docker, no swap):** with a 1 GiB limit the container was OOM-killed on the first request, with the
  original loader and again after the load-peak reductions (in-place quantisation, mmap of the weights). With a 2 GiB limit it
  runs. The existing public service is configured at 1 GiB, so this model **cannot share that service as configured**; it must be a
  separate service with at least 2 GiB (BELIEVED, not verified here: Cloud Run offers 2 GiB and more with 1 vCPU; check the
  current limits table before choosing). Steady-state anonymous memory after load is the binding number, not the request size.
- **Latency is pessimistic.** One vCPU on a shared dev machine whose other jobs were running; the model report's CPU table
  (8 threads, unloaded) is a different configuration and must not be compared with these rows. Per-review latency here is
  dominated by the single vCPU, and batch 8 costs about 8 times batch 1 because each review is its own forward by design.
- **Cold start** is container start to the first 200 from /v2/classify, including the lazy model load (separate column). It excludes
  any image pull and every Cloud Run effect; it is NOT a Cloud Run cold start.
- **Parity.** The thresholds file equals a fresh recomputation from the offline artifacts exactly. Run through the offline
  batching (128 per forward, pred-file order) the served code reproduces every sealed decision and every probability exactly
  (zero mismatches, zero probability difference): the code path is the evaluated function. Served per review it differs from
  the batch-128 evaluation on a minority of decisions, **all between answering and abstaining, none between two classes**,
  because dynamic int8 quantises activations per tensor and a logit depends on its batch-mates. Coverage and precision against the
  silver consensus labels are in the table above for both. Every mismatch (id, task, both labels) is listed in the JSON; no review text is.
- **In the container** (torch CPU wheel, over HTTP, batch 1) the decisions differ from the host run (torch 2.6.0+cu124 on the same
  CPU) on 14 task decisions (6 only in the container, 8 only on the host): borderline
  confidences moved across a threshold between two builds of the same torch version. So bit-identity holds only for the same build
  and batching; across builds the contract is statistical (coverage and precision), not per item.
- **Served variant selection** (agreement of each int8 variant with fp32_cpu under the served decision rule, sealed test split of
  the pred files): see `variant_selection` in the JSON; `int8_ffn_full` has the highest mean of the five int8 variants.
- The image was built from a working tree with no uncommitted code changes relative to the commit named above (it was rebuilt just before the final run). Cloud Run behaviour is NOT
  verified.
