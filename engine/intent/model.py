"""Multi-task encoder for review intent: one linear head per task on a shared mean-pooled encoder.

Reuses `engine.train.Classifier` (the E2 model: encoder + mean pooling + linear head) unchanged. One head per task is
a single `nn.Linear(hidden, sum(sizes))` whose output is sliced per task, which is mathematically the same as separate
linear heads (each task's rows of the weight matrix only see that task's loss) and keeps E2's forward pass as is.
"""

from __future__ import annotations

import torch
from torch import nn
from transformers import AutoModel, AutoTokenizer

from engine.train import Classifier

E5 = "intfloat/multilingual-e5-base"  # the E2 checkpoint


class MultiHeadClassifier(Classifier):
    """`Classifier` with the head sized for several tasks. `enc` is any module with `.config.hidden_size`
    returning `.last_hidden_state`, which is how tests inject a tiny stub encoder."""

    def __init__(self, enc: nn.Module, sizes: dict[str, int]) -> None:
        nn.Module.__init__(
            self
        )  # deliberately skip Classifier.__init__ (it downloads a checkpoint)
        self.enc = enc
        self.sizes = dict(sizes)
        self.head = nn.Linear(enc.config.hidden_size, sum(sizes.values()))

    def split(self, logits: torch.Tensor) -> dict[str, torch.Tensor]:
        """Per-task logits from the concatenated head output."""
        out, i = {}, 0
        for task, n in self.sizes.items():
            out[task] = logits[:, i : i + n]
            i += n
        return out


def load_pretrained(name: str, sizes: dict[str, int]) -> tuple[MultiHeadClassifier, object]:
    """-> (model, tokenizer) for a Hugging Face checkpoint."""
    model = MultiHeadClassifier(AutoModel.from_pretrained(name), sizes)
    return model, AutoTokenizer.from_pretrained(name)
