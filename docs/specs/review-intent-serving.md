# Serving the review-intent encoder: static int8 on ONNX Runtime (S25, registered before any sealed item is scored with it)

Context. The first `/v2/classify` build (#533) served the torch dynamic-int8 variant `int8_ffn_full`. Torch dynamic quantisation
picks each activation scale from the tensor it is given, so a logit depends on which other reviews share its batch: scored one review
at a time (the way the endpoint serves), 166 of 3028 sealed task decisions differ from the offline evaluation, which ran batches of 128
(all 166 are answer-versus-abstain; 0 class flips). Static quantisation fixes the activation scales once, from calibration data, so a
review's logits no longer depend on its batch-mates.

## Method (fixed now)

- Model: the run-2 fp32 checkpoint (`D:/ml-cache/s23/quant/fp32_cpu.pt`, the same weights every quantised variant came from), exported
  with `torch.onnx.export` (opset 17) as `input_ids, attention_mask -> concatenated head logits` (mean pooling and the 14-output head
  inside the graph). The serving image has no PyTorch: ONNX Runtime plus the `tokenizers` library reading the checkpoint's own
  `tokenizer.json`.
- Quantisation: ONNX Runtime `quantize_static`, QDQ format, int8 per-channel weights, int8 activations with scales from a calibration
  pass. Calibration data: 256 TRAINING texts (`random.Random(42).sample` of the sorted train ids in `labels.json`), one review per
  forward with its real length, `max_len` 128. No validation, test, sealed or safety-stratum text is used for calibration.
- Candidates, all FFN-first because the dynamic study found attention to be where quantisation error concentrates:
  - `s1_ffn_minmax`: feed-forward MatMuls (intermediate and output dense) and the word-embedding table, MinMax calibration;
  - `s2_ffn_percentile`: the same nodes, Percentile calibration (99.999);
  - `s3_all_minmax`: every encoder MatMul and the embedding table, MinMax.
- Selection, on VALIDATION only: the mean over the four served tasks of argmax agreement with the fp32 model on the validation items;
  a candidate within 0.005 of the best prefers the smaller file. The sealed test items are scored once, for the chosen candidate only.
  If no candidate reaches a mean validation agreement of at least 0.90 the static route is reported as failed with the three numbers
  and the torch route stays; that is not repaired by tuning on the test items.
- The per-task temperature and the rare-class and abstention thresholds are refit on VALIDATION for the chosen ONNX model, with the same
  procedure as for every other variant (`engine.intent.quantize` / `engine.intent.evaluate`); the dynamic model's thresholds are not
  reused, because the static model is a different function.

## What "parity" means here, and what it does not

The static model is a different function from the dynamic one, so "0 mismatches against the dynamic offline evaluation" is not a
meaningful target and is not claimed. The targets are:

1. Batch independence (the property that failed before): ONNX decisions for the sealed items scored in batches of 128 in pred-file order
   versus one review per forward. Target 0 mismatches of 3028 task decisions. Padding can still perturb floating-point results, so a
   non-zero count is reported with its kinds, not hidden.
2. Implementation parity: the container's HTTP path (the `tokenizers` library, one review per request) versus the offline pipeline
   (`transformers` tokenizer, ONNX Runtime on the same file). Target 0 mismatches; tokenisation is also compared id by id.
3. Quality: coverage and precision at the conservative p0.95 validation threshold on the sealed set against the silver consensus labels,
   at batch 1, beside the torch-dynamic numbers from #533 (which were measured on a different model). Any change is reported with its
   direction; a drop is not explained away.

Silver labels (four-judge panel consensus), not human gold. Provenance for every number: commit SHA and the artifact under
`reports/serving/`.

## Resource targets reported (not gates)

Image size, resident memory, cold start and batch-1 p50/p95 in a local Docker container limited to 1 vCPU and 1 GiB (`--memory 1g
--memory-swap 1g`), whether it is OOM-killed, and whether it fits the existing 1 GiB `review-iq` service. Local Docker on a shared
workstation is not Cloud Run; Cloud Run numbers remain BELIEVED until measured there.
