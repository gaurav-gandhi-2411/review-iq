from __future__ import annotations

from engine.intent.llm_effort_check import evaluate, sign_test_p


def _block(low: list[int], high: list[int]) -> list[dict]:
    def mk(name: str, v: list[int]) -> dict:
        return {
            "effort": name,
            "empty": 0,
            "finish_length": 0,
            "completion_tokens": {f"i{k}": x for k, x in enumerate(v)},
        }

    return [mk("none", low), mk("low", low), mk("medium", low), mk("high", high)]


def test_sign_test_known_values() -> None:
    assert sign_test_p(0, 0) == 1.0
    assert abs(sign_test_p(20, 0) - 2 / 2**20) < 1e-12
    assert sign_test_p(10, 10) == 1.0


def test_honoured_when_high_is_clearly_longer() -> None:
    low = [100 + i for i in range(20)]
    res = evaluate(_block(low, [x * 3 for x in low]))
    assert res["honoured"] is True
    assert res["high_vs_low"]["high_greater"] == 20


def test_not_honoured_when_identical() -> None:
    low = [100 + i for i in range(20)]
    res = evaluate(_block(low, list(low)))
    assert res["honoured"] is False
    assert res["high_vs_low"]["ties"] == 20


def test_significant_but_small_effect_is_not_honoured() -> None:
    low = [1000 + i for i in range(20)]
    res = evaluate(_block(low, [x + 50 for x in low]))  # always greater, ratio ~1.05
    assert res["high_vs_low"]["sign_test_p"] < 0.05
    assert res["honoured"] is False
