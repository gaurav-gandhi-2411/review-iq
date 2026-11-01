"""Input handling shared by training, evaluation and quantisation calibration (Amendment 16): a token budget and how a longer text is cut.

    head       first (max_len - 2) content tokens, then the special tokens (what `truncation=True, max_length=max_len` has always done)
    head_tail  the first and the last (max_len - 2) / 2 content tokens (the head gets the extra one when the budget is odd), then the
               special tokens; a text that already fits is untouched

Both return at most `max_len` ids INCLUDING the special tokens, so the two modes cost the same at inference. Works with a HuggingFace
tokenizer (`tok(text, add_special_tokens=False)["input_ids"]`, `tok.cls_token_id`, `tok.sep_token_id`, `tok.pad_token_id`).
"""

from __future__ import annotations

from typing import Any

import numpy as np

HEAD = "head"
HEAD_TAIL = "head_tail"
MODES = (HEAD, HEAD_TAIL)
SPECIALS = 2  # <s> and </s>


def cut(content: list[int], max_len: int, mode: str) -> list[int]:
    """The content token ids kept under the budget `max_len - SPECIALS`."""
    if mode not in MODES:
        raise ValueError(f"unknown truncation mode {mode!r}; expected one of {MODES}")
    budget = max_len - SPECIALS
    if budget <= 0:
        raise ValueError("max_len must leave room for the special tokens")
    if len(content) <= budget:
        return list(content)
    if mode == HEAD:
        return list(content[:budget])
    tail = budget // 2
    return list(content[: budget - tail]) + list(content[len(content) - tail :])


def encode_ids(tok: Any, text: str, max_len: int, mode: str = HEAD) -> list[int]:
    content = tok(text, add_special_tokens=False, truncation=False)["input_ids"]
    # <s> ... </s>, the single-sequence layout of the RoBERTa / XLM-R family (transformers 5 dropped build_inputs_with_special_tokens)
    return [tok.cls_token_id, *cut(content, max_len, mode), tok.sep_token_id]


def encode_batch(
    tok: Any, texts: list[str], max_len: int, mode: str = HEAD
) -> dict[str, np.ndarray]:
    """input_ids and attention_mask (int64), padded to the longest text in the batch."""
    rows = [encode_ids(tok, t, max_len, mode) for t in texts]
    width = max(len(r) for r in rows)
    pad = tok.pad_token_id
    ids = np.full((len(rows), width), pad, dtype=np.int64)
    mask = np.zeros((len(rows), width), dtype=np.int64)
    for i, r in enumerate(rows):
        ids[i, : len(r)] = r
        mask[i, : len(r)] = 1
    return {"input_ids": ids, "attention_mask": mask}
