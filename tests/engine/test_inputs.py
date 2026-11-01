"""Amendment 16 input handling: head and head+tail truncation to a token budget."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from engine.intent import (
    inputs as I,  # noqa: E741 (module alias reads better than a long name in the tests)
)

TOKENIZER = Path("D:/ml-cache/s25/classify_artifacts_onnx/hf")


class Fake:
    """Whitespace 'tokenizer' with xlm-roberta-style specials (<s>=0, </s>=2, pad=1); ids are the word's number."""

    pad_token_id = 1
    cls_token_id = 0
    sep_token_id = 2

    def __call__(
        self, text: str, add_special_tokens: bool = True, truncation: bool = False
    ) -> dict:
        ids = [int(w) for w in text.split()]
        return {"input_ids": ([0, *ids, 2] if add_special_tokens else ids)}


def words(n: int) -> str:
    return " ".join(str(i + 10) for i in range(n))


def test_head_keeps_the_first_budget_tokens() -> None:
    ids = I.encode_ids(Fake(), words(300), 128, I.HEAD)
    assert len(ids) == 128 and ids[0] == 0 and ids[-1] == 2 and ids[1:-1] == list(range(10, 136))


def test_head_tail_keeps_the_beginning_and_the_end_at_the_same_cost() -> None:
    ids = I.encode_ids(Fake(), words(300), 128, I.HEAD_TAIL)
    assert len(ids) == 128  # same cost as head
    body = ids[1:-1]
    assert body[:63] == list(range(10, 73)) and body[63:] == list(range(10 + 300 - 63, 10 + 300))


def test_head_tail_256_is_127_plus_127_and_an_odd_budget_gives_the_extra_token_to_the_head() -> (
    None
):
    body = I.encode_ids(Fake(), words(600), 256, I.HEAD_TAIL)[1:-1]
    assert (
        len(body) == 254
        and body[:127] == list(range(10, 137))
        and body[127:] == list(range(10 + 600 - 127, 610))
    )
    odd = I.cut(list(range(100)), 11, I.HEAD_TAIL)  # budget 9: head 5, tail 4
    assert odd == [0, 1, 2, 3, 4, 96, 97, 98, 99]


@pytest.mark.parametrize("mode", I.MODES)
def test_a_text_that_fits_is_untouched_in_both_modes(mode: str) -> None:
    ids = I.encode_ids(Fake(), words(50), 128, mode)
    assert ids == [0, *range(10, 60), 2]


def test_bad_inputs_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown truncation mode"):
        I.cut([1, 2, 3], 128, "middle")
    with pytest.raises(ValueError, match="room for the special tokens"):
        I.cut([1, 2, 3], 2, I.HEAD)


def test_encode_batch_pads_to_the_longest_and_masks_the_padding() -> None:
    b = I.encode_batch(Fake(), [words(3), words(7)], 128)
    assert b["input_ids"].shape == (2, 9) and b["input_ids"].dtype == np.int64
    assert b["attention_mask"][0].tolist() == [1] * 5 + [0] * 4
    assert b["input_ids"][0, 5:].tolist() == [1, 1, 1, 1]


@pytest.mark.skipif(
    not TOKENIZER.exists(), reason="the served tokenizer files are private (D:/ml-cache)"
)
def test_head_mode_equals_the_huggingface_truncation_it_replaces() -> None:
    transformers = pytest.importorskip("transformers")
    tok = transformers.AutoTokenizer.from_pretrained(TOKENIZER)
    text = ("The zip broke after a week and nobody replied to my emails. " * 40) + "Final sentence."
    for L in (64, 128, 256, 512):
        want = tok(text, truncation=True, max_length=L)["input_ids"]
        assert I.encode_ids(tok, text, L, I.HEAD) == want
