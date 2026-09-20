"""Failure-mode tests for scripts/check_model_generation.py (Session 15d).

The regression these pin: a model advertised by the provider's list endpoint but rejected by
generateContent (HTTP 404 "no longer available to new users") must FAIL, not pass.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest
from scripts import check_model_generation as cmg


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode())
    return urllib.error.HTTPError("https://x", code, "err", {}, body)  # type: ignore[arg-type]


def _opener_returning(status: int):
    return lambda req, timeout: _Resp(status)


def _opener_raising(exc: BaseException):
    def _o(req: Any, timeout: float) -> Any:
        raise exc

    return _o


@pytest.mark.parametrize("provider", ["groq", "gemini"])
def test_200_is_ok(provider: str) -> None:
    v = cmg.check(provider, "m", "k", opener=_opener_returning(200))
    assert v.status == "ok"


def test_404_no_longer_available_fails_even_though_a_list_would_advertise_it() -> None:
    v = cmg.check(
        "gemini",
        "gemini-2.5-flash-lite",
        "k",
        opener=_opener_raising(_http_error(404, "This model is no longer available to new users")),
    )
    assert v.status == "fail" and v.http_status == 404
    assert "no longer available" in v.detail


@pytest.mark.parametrize("code", [400, 401, 402, 403, 500, 503])
def test_other_http_errors_fail(code: int) -> None:
    v = cmg.check("groq", "m", "k", opener=_opener_raising(_http_error(code, "nope")))
    assert v.status == "fail" and v.http_status == code


def test_429_is_a_warning_not_a_deprecation_failure() -> None:
    v = cmg.check("groq", "m", "k", opener=_opener_raising(_http_error(429, "rate limit")))
    assert v.status == "warn"


def test_network_error_fails_closed() -> None:
    v = cmg.check("gemini", "m", "k", opener=_opener_raising(urllib.error.URLError("dns")))
    assert v.status == "fail" and v.http_status is None
    assert cmg.check("gemini", "m", "k", opener=_opener_raising(TimeoutError())).status == "fail"


def test_missing_key_fails_closed_never_passes_silently() -> None:
    assert cmg.check("groq", "m", "", opener=_opener_returning(200)).status == "fail"


def test_unknown_provider_is_a_usage_error() -> None:
    assert cmg.main(["prog", "openai", "gpt"]) == 2
    assert cmg.main(["prog"]) == 2


def test_key_is_never_in_the_error_output(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_KEY", "SECRET-VALUE-123")
    monkeypatch.setattr(cmg, "check", lambda *a, **k: cmg.Verdict("fail", 404, "gone"))
    assert cmg.main(["prog", "gemini", "m"]) == 1
    out = capsys.readouterr().out
    assert "SECRET-VALUE-123" not in out and "::error::" in out
