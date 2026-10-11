from __future__ import annotations

import ast

from engine.kaggle import push_intent


def test_kaggle_kernel_builds_clean() -> None:
    src = push_intent.build("feat/x")
    ast.parse(src)
    assert "__BRANCH__" not in src and "feat/x" in src
    assert "API_KEY" not in src and "KAGGLE_KEY" not in src
