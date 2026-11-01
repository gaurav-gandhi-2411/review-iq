# Source before any training run. Model/dataset caches go to D: because C: runs short of space.
# Scoped to this process tree on purpose: a user-level setting would also redirect other projects.
export HF_HOME=/d/ml-cache/hf
export HF_DATASETS_CACHE=/d/ml-cache/hf-datasets
export TORCH_HOME=/d/ml-cache/torch
