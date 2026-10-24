# Classifier redeploy on pinned numerics, live parity, cold starts, and the Amendment 16 capacity probe (S31 P3/P4)

Evidence: `reports/serving/classify_redeploy_s31.json` (ids and numbers only). All VERIFIED in S31 on 2026-10-22 (UTC).

## P3 — redeploy

- Rollback first: tag `keep-classify-prev` on the S29 image `sha256:b6d3ef10…2885e`.
- `CONFIRM_REGISTRY_COST=yes bash ops/deploy/deploy-classify.sh D:/ml-cache/s25/classify_artifacts_onnx` from a clean checkout of
  `origin/main` `b3cabc3` (the merged #584: ORT `session.x64quantprecision=1`, x64 thresholds). First end-to-end run of the script:
  guards passed, Cloud Build `c00a15cd` SUCCESS in 2 min 6 s, build source deleted, revision **`review-iq-classify-00002-zh5`** at 100%.
- Access: one IAM binding (`roles/run.invoker` → `review-iq-runner@`), no public principal, unauthenticated POST → 403.

### Live parity (757 sealed reviews, one per request, identity token)

| | S29 (unpinned image) | S31 (pinned image) |
|---|---|---|
| decision mismatches of 3,028 | 81 | **5** |
| errors | 0 | 0 |
| thresholds sha served = committed | yes | yes |

All 5 are flips across the abstention threshold (served label vs abstained, or the reverse), never one class for another:
scale-apparel-0537 broad_intent, scale-apparel-0988 sentiment, scale-food-0660 broad_intent, scale-food-1357 needs_action and
sentiment. Served vs offline max-probability for these: 0.8471 vs 0.8486 (threshold 0.8472), 0.772 vs 0.836, 0.869 vs 0.830,
0.934 vs 0.867, 0.875 vs 0.767.

Per-task |served − offline| probability over the 757: **bit-identical on 658–662 reviews (87%)**, p95 0.0008–0.0016, max 0.19–0.31.
Conclusion: the pin makes the laptop and Kaggle CPUs agree exactly (S30), but Cloud Run's CPU still takes a different path for about
13% of inputs. The target (0 mismatches) is not met; nothing calls the service (router OFF), so no rollback. Parked for GG.

### Latency, memory, cold starts

- Server side (Cloud Monitoring, parity window, per minute): p50 150–208 ms, p95 311–456 ms, p99 ≤ 627 ms; memory p99 0.62 of 2 GiB.
- Client side (laptop → asia-south1, includes network): p50 278 ms, p95 546 ms.
- Cold starts after ≥ 15 min idle (service at zero instances): **12.0 s, 17.3 s, 13.8 s**; warm p50 after each 211, 124, 117 ms.
  All inside the 60 s ingest timeout (#585); the 10 s user-facing default would time out on every one.

## P4 — Amendment 16 capacity probe (registered budget: server p95 < 1,000 ms and memory p99 < 0.85 of 2 GiB)

Throwaway private service `review-iq-classify-probe` (same image digest, 1 vCPU, 2 GiB, min 0, max 1, `CLASSIFY_MAX_LEN` per length),
50 requests per length, each text filled to exactly the length from real reviews; deleted afterwards and verified gone.

| Length | service-reported max_tokens | server p95 (max of minutes) | server p50 | memory p99 | Budget |
|---|---|---|---|---|---|
| 128 | 128 | 355 ms | 323 ms | 0.61 | **pass** |
| 256 | 256 | **1,107 ms** (minutes: 1,107 and 874) | 802 ms | 0.62 | **fail** |
| 512 | 512 | **2,137 ms** | 1,836 ms | 0.65 | **fail** |

Surviving arms: **H128 and HT128**. H256, HT256 and H512 are dropped before training, as registered. The pre-registration expected
H256 to pass (local single-thread estimate 0.87 s at 256); on Cloud Run's 1 vCPU it is about 0.8 s median and over 1 s at p95.

Protocol note (logged as a deviation in the S31 progress file): the probe sends three unmeasured warm-up requests and starts its
measured window at the next whole minute, so the scale-from-zero cold start (12–17 s) cannot become a minute's p95. The criterion,
the statistic (max over minutes of Cloud Monitoring's per-minute p95) and the 50 measured requests are as registered.
