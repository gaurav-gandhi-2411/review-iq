"""Tests for the pre-registered buy_again experiment (ADR 0031): the guards that keep it honest."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.prompts import en as en_prompt
from eval.experiments import buy_again_fewshot as exp

ROOT = Path(__file__).resolve().parents[2]


def _all_fixture_texts() -> list[str]:
    texts = []
    for p in (ROOT / "eval" / "fixtures").rglob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "review_text" in d:
            texts.append(d["review_text"].lower())
    return texts


def test_variant_only_adds_lines_to_the_production_prompt() -> None:
    wrapped = "<review>x</review>"
    prod = set(en_prompt.build_prompt(wrapped).splitlines())
    variant_lines = exp.build_variant_prompt(wrapped).splitlines()
    added = {ln for ln in variant_lines if ln not in prod}
    expected = {ln for ln in exp.EXTRA_EXAMPLES.splitlines() if ln.strip()}
    assert prod <= set(variant_lines), "variant dropped or altered a production line"
    assert added == expected, "variant added something other than the two extra examples"


def test_field_definition_and_system_prompt_untouched() -> None:
    v = exp.build_variant_prompt("<review>x</review>")
    assert en_prompt._FIELD_DESCRIPTIONS in v
    assert "buy_again: true/false/null. Only false if reviewer explicitly says" in v


def test_extra_examples_are_not_drawn_from_any_fixture() -> None:
    """Contamination guard: no sentence of an example review appears in any held-out/dev text."""
    corpus = _all_fixture_texts()
    assert len(corpus) > 100
    reviews = re.findall(r'Review: "(.+?)"\n', exp.EXTRA_EXAMPLES)
    assert len(reviews) == 2
    for rev in reviews:
        for sentence in re.split(r"(?<=[.!?])\s+", rev):
            s = sentence.strip().lower()
            if len(s) > 20:
                assert not any(s in t for t in corpus), f"example sentence found in a fixture: {s}"


def test_examples_show_one_true_and_one_false_commit() -> None:
    assert '"buy_again": true' in exp.EXTRA_EXAMPLES
    assert '"buy_again": false' in exp.EXTRA_EXAMPLES
    assert '"buy_again": null' not in exp.EXTRA_EXAMPLES  # the two existing null examples remain


def test_day_split_is_a_disjoint_partition_of_the_english_routed_ids() -> None:
    ids = [f"hien-{i:04d}" for i in range(1, 57)]
    d1, d2 = exp.day_ids(ids, 1), exp.day_ids(ids, 2)
    assert len(d1) == len(d2) == 28
    assert not set(d1) & set(d2)
    assert sorted(d1 + d2) == ids


def test_verdict_reverts_on_any_rise_in_wrong_committed_count() -> None:
    assert exp.verdict(n_cov=30, n_wrong=exp.B_WRONG + 1, complete=True).startswith("REVERT")
    # even with a large coverage gain, and even before the experiment is complete
    assert exp.verdict(n_cov=30, n_wrong=exp.B_WRONG + 1, complete=False).startswith("REVERT")


def test_verdict_success_null_and_interim() -> None:
    assert exp.verdict(n_cov=exp.B_COV + 1, n_wrong=exp.B_WRONG, complete=True).startswith(
        "SUCCESS"
    )
    assert exp.verdict(n_cov=exp.B_COV, n_wrong=0, complete=True).startswith("NULL")
    assert exp.verdict(n_cov=10, n_wrong=exp.B_WRONG, complete=False).startswith("INTERIM")


def test_rate_flattering_case_is_still_a_revert() -> None:
    """Coverage 2 -> 40 with 2 wrong is a lower wrong RATE than 1 of 2, but a higher COUNT: revert."""
    assert exp.verdict(n_cov=40, n_wrong=2, complete=True).startswith("REVERT")


def test_mcnemar_exact() -> None:
    assert exp.mcnemar_exact_p(0, 0) == 1.0
    assert exp.mcnemar_exact_p(0, 10) < 0.01
    assert exp.mcnemar_exact_p(5, 5) == 1.0
