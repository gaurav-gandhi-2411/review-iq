# Scale labelling runbook (spec Amendment 5)

Nothing here has been launched. Code: `engine/labelling/{scale_sample,two_judge,scale_plan}.py`,
`engine/kaggle/{kernel_scale,push_scale}.py`. Review text never enters the repository: item files and Stage B
text go to PRIVATE Kaggle datasets; only ids, hashes and judge labels are committed.

## Plan

| Stage | Judges | Items | Layout |
|---|---|---|---|
| A | llama3.1 + qwen3 | all 5,000 | both T4s, items sharded by file index parity, judges alternate per 200-item chunk |
| S | gemma2 (GPU 0), mistral (GPU 1) | the sealed `test` split | one judge per T4 |
| B | mistral | train/val items where any task (or a two-judge aspect conflict) has no two agreeing answers | both T4s, sharded |

Stage B's id list is computed from merged Stage A outputs by `scale_plan stage-b`; it refuses to run if any
train/val id lacks a Stage A record. Sealed items never go to Stage B (Stage S already runs mistral on them).

## Resumable by construction

Work is cut into chunks of 200 items. No chunk starts after `--budget-hours` (default 8.0, for the 9h session limit;
use 11 for 12h). Each session copies the previous session's `judges/*.jsonl` (from `--prior`) into its output first and
skips finished ids, so output is cumulative and a killed session loses at most one chunk. Download the output after
every session, even a partial one.

## Commands (run from the repo root, venv active)

```
python -m engine.labelling.scale_sample --corpus D:/ml-cache/review-corpus --flipkart data/processed/vernacular_subset.jsonl --out D:/ml-cache/review-intent/scale.jsonl --manifest reports/labelling/scale/manifest.json --exclude reports/labelling/pilot1/manifest.json,reports/labelling/pilot2/manifest.json,reports/labelling/pilot3/manifest.json
python -m engine.labelling.scale_plan items --scale D:/ml-cache/review-intent/scale.jsonl --stage A --out D:/ml-cache/s22/ds_scale_a/scale_A.jsonl
python -m engine.labelling.scale_plan items --scale D:/ml-cache/review-intent/scale.jsonl --stage S --out D:/ml-cache/s22/ds_scale_s/scale_S.jsonl
```

Create one private dataset per item file (same layout as `D:\ml-cache\s22\ds3`: the jsonl plus a
`dataset-metadata.json` with `"id": "gauravgandhi2411/review-intent-scale-a"`, `kaggle datasets create -p DIR`).
The kernel clones `--branch` from GitHub, so that branch must be pushed (merged to `main` for `--branch main`).

```
python -m engine.kaggle.push_scale --stage A --items scale_A.jsonl --dataset gauravgandhi2411/review-intent-scale-a --slug review-intent-scale-a --branch main
kaggle kernels status gauravgandhi2411/review-intent-scale-a
kaggle kernels output gauravgandhi2411/review-intent-scale-a -p D:/ml-cache/s22/out_A
```

If `out_A/status.json` shows `budget_hit: true` or fewer done than `of`, upload `out_A/judges` as a private dataset
and push again with `--prior gauravgandhi2411/review-intent-scale-a-out`. When complete:

```
python -m engine.labelling.scale_plan merge --src D:/ml-cache/s22/out_A/judges --dst reports/labelling/scale/judges_A
python -m engine.labelling.scale_plan stage-b --scale D:/ml-cache/review-intent/scale.jsonl --judges reports/labelling/scale/judges_A --ids-out reports/labelling/scale/stageB_ids.json --items-out D:/ml-cache/s22/ds_scale_b/scale_B.jsonl
python -m engine.kaggle.push_scale --stage S --items scale_S.jsonl --dataset gauravgandhi2411/review-intent-scale-s --slug review-intent-scale-s
python -m engine.kaggle.push_scale --stage B --items scale_B.jsonl --dataset gauravgandhi2411/review-intent-scale-b --slug review-intent-scale-b
```

S does not depend on A and can be pushed first. Merge each stage's output with `scale_plan merge` into
`reports/labelling/scale/judges_{A,S,B}` (judge files carry ids and labels only).

## GPU-hour estimate

Measured seconds per item (pilot 3, 360 items, one T4 per judge, `reports/labelling/pilot3/kernel_status.json`):
llama 3.292, gemma 4.392, qwen 2.783, mistral 3.939. Mistral rate: the two-judge rule's tiebreak trigger fired on
88 of the 300 random pilot-3 items (0.293; 112 of 360 including the mined 60) when computed with
`two_judge.tiebreak_ids` on `reports/labelling/pilot3/judges`. Plan at 0.30.

Output of `python -m engine.labelling.scale_plan estimate --n-total 5000 --n-sealed 763 --mistral-rate 0.30`:
A 8.44, S 1.77, B 1.39 GPU-hours, total 11.59 GPU-hours, 5.8 hours wall if both T4s stay busy (excludes about 10
minutes of model pulls per session). Every stage fits one 9h session; the budget guard exists for slower days.
Unverified: whether two 5 GB models stay co-resident on one T4 in Stage A (`OLLAMA_MAX_LOADED_MODELS=2`); if not,
Ollama reloads per chunk, a cost of roughly one model load per 200 items.
