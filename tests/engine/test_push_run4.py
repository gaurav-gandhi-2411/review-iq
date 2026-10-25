"""S31: the run-4 kernel template must parse every registered arm (the smoke run used one arm, which hid a 2+ arm crash)."""

from __future__ import annotations

import ast

from engine.kaggle import push_run4


def _arms_value(src: str) -> list[list[str]]:
    # run only the top-level ARMS_SPEC / ARMS assignments of the built kernel (no imports, no side effects)
    tree = ast.parse(src)
    keep = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("ARMS_SPEC", "ARMS")
    ]
    ns: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<arms>", "exec"), ns)  # noqa: S102 (two literal assignments)
    return ns["ARMS"]


def test_two_arms_parse_into_name_length_truncation_triples() -> None:
    src = push_run4.build("main", "H128:128:head,HT128:128:head_tail", "full", 30, 3)
    assert _arms_value(src) == [["H128", "128", "head"], ["HT128", "128", "head_tail"]]


def test_one_arm_still_parses_and_no_placeholder_is_left() -> None:
    src = push_run4.build("main", "H128:128:head", "smoke", 1, 3)
    assert _arms_value(src) == [["H128", "128", "head"]]
    assert not any(
        p in src for p in ("__BRANCH__", "__ARMS__", "__MODE__", "__EPOCHS__", "__PATIENCE__")
    )
