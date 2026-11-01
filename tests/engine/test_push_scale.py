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


def test_a_resume_push_makes_the_kernel_fail_closed_without_prior_files() -> None:
    # S31: a prior dataset mounted flat (no judges/ folder) was silently ignored and 10.25 GPU-hours were redone
    resume = push_scale.build("P", "fresh16_all.jsonl", "main", 10.0, expect_prior=True)
    fresh = push_scale.build("P", "fresh16_all.jsonl", "main", 10.0)
    ast.parse(resume)
    assert "__EXPECT_PRIOR__" not in resume and 'EXPECT_PRIOR = "1" == "1"' in resume
    assert 'EXPECT_PRIOR = "0" == "1"' in fresh
    assert (
        "refusing to redo finished work" in resume
        and 'PRIOR_GLOB = "/kaggle/input/**/*.jsonl"' in resume
    )


def test_prior_judge_files_are_recognised_by_name_not_folder() -> None:
    from engine.labelling.scale_plan import is_judge_output

    for ok in (
        "gemma2_9b.jsonl",
        "qwen3_8b__shard1.jsonl",
        "mistral_7b.jsonl",
        "llama31_8b__shard0.jsonl",
    ):
        assert is_judge_output(ok)
    for bad in (
        "fresh16_all.jsonl",
        "scale_A.jsonl",
        "gemma2_9b.json",
        "qwen3_8b__shardx.jsonl",
        "gpt_oss.jsonl",
    ):
        assert not is_judge_output(bad)


def test_every_stage_builds() -> None:
    for stage in ("A", "S", "B"):
        ast.parse(push_scale.build(stage, f"scale_{stage}.jsonl", "main", 11.0))
