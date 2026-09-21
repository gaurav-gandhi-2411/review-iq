"""Confirm a configured LLM model actually GENERATES, not merely that its name is advertised.

Why (Session 15d): model-availability-check.yml only tested that the model id appears in the
provider's `models` LIST. Verified against the production Gemini key on 2026-09-20:
`gemini-2.5-flash-lite` is present in models.list with `generateContent` in its
supportedGenerationMethods, yet generateContent returns HTTP 404 "no longer available to new
users". A list-membership check reports that model live while every real call fails -- a control
that passes without verifying what it claims. The only honest test is one real, tiny generation
(~10-30 tokens).

Verdicts (stdlib only, so the workflow needs no dependency install):
  200                      -> ok
  429                      -> reachable but rate-limited: WARN, not a failure (the model exists;
                              quota is a different signal and must not page as "deprecated")
  anything else / network  -> FAIL (404 removed, 402 no billing, 401/403 bad key, 5xx, timeout)

Usage:
    GROQ_KEY=... python3 scripts/check_model_generation.py groq openai/gpt-oss-20b
    GEMINI_KEY=... python3 scripts/check_model_generation.py gemini gemini-2.5-flash
Exit 0 ok/warn, 1 fail, 2 usage error. Keys are read from the environment and never printed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Verdict:
    status: str  # "ok" | "warn" | "fail"
    http_status: int | None
    detail: str


def _request(provider: str, model: str, key: str) -> urllib.request.Request:
    if provider == "groq":
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
            "max_completion_tokens": 24,
        }
        return urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
    if provider == "gemini":
        body = {
            "contents": [{"parts": [{"text": "Reply with the single word: ok"}]}],
            "generationConfig": {"maxOutputTokens": 16},
        }
        return urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode(),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            method="POST",
        )
    raise ValueError(f"unknown provider {provider!r}")


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        err = payload.get("error", payload)
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return str(msg)[:200]
    except Exception:  # noqa: BLE001 -- best-effort detail only
        return exc.reason if isinstance(exc.reason, str) else "no detail"


def check(provider: str, model: str, key: str, *, opener=urllib.request.urlopen) -> Verdict:  # noqa: ANN001
    """One real generation. Never raises for provider/network errors: returns a Verdict."""
    if not key:
        return Verdict("fail", None, "no API key available: cannot verify generation")
    try:
        req = _request(provider, model, key)
        with opener(req, timeout=TIMEOUT_SECONDS) as resp:
            code = resp.status
        return Verdict("ok", code, "generated") if code == 200 else Verdict("fail", code, "non-200")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return Verdict("warn", 429, "rate limited: model reachable, quota exhausted")
        return Verdict("fail", exc.code, _error_text(exc))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Verdict("fail", None, f"network error: {type(exc).__name__}")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"groq", "gemini"}:
        print(__doc__)
        return 2
    provider, model = argv[1], argv[2]
    key = os.environ.get("GROQ_KEY" if provider == "groq" else "GEMINI_KEY", "")
    v = check(provider, model, key)
    if v.status == "ok":
        print(f"  [ok] {provider}/{model} generated (HTTP {v.http_status})")
        return 0
    if v.status == "warn":
        print(f"::warning::{provider}/{model}: {v.detail}")
        return 0
    print(f"::error::{provider}/{model} FAILED to generate (HTTP {v.http_status}): {v.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
