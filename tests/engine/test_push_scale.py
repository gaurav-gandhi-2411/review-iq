from __future__ import annotations

import ast

from engine.kaggle import push_scale


def test_pushed_kernel_is_valid_python_with_no_placeholders_left_and_no_secrets() -> None:
    src = push_scale.build("A", "scale_A.jsonl", "main", 8.0)
    ast.parse(src)
    assert not any(k in src for k in ("__BRANCH__", "__STAGE__", "__ITEMS__", "__BUDGET_H__"))
    assert "scale_A.jsonl" in src and 'BUDGET_S = float("8.0")' in src
    assert "KAGGLE_KEY" not in src and "API_KEY" not in src
    meta = push_scale.metadata("s", ["a/b"])
    assert meta["is_private"] is True and meta["dataset_sources"] == ["a/b"]


def test_every_stage_builds() -> None:
    for stage in ("A", "S", "B"):
        ast.parse(push_scale.build(stage, f"scale_{stage}.jsonl", "main", 11.0))
