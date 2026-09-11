"""Wave 1 Section H: corpus_pipeline.teacher_labeling -- mocked-provider tests only.

No live Groq calls in this suite (matches the standing "no live LLM calls in CI /
tests" convention this repo already follows for eval.runner). GroqProvider.complete
is monkeypatched per-test to return canned JSON, proving the sanitize -> prompt ->
parse pipeline wiring without touching the network or any API key/quota.
"""

from __future__ import annotations

import json

import pytest
from app.core.providers.groq import GroqProvider
from benchmark.vernacular_v2.corpus_pipeline.teacher_labeling import (
    TEACHER_MODEL_ID,
    label_review,
    label_sample,
)

_GOOD_EXTRACTION = {
    "product": "Bluetooth headset",
    "stars": None,
    "stars_inferred": 4,
    "pros": ["good battery"],
    "cons": [],
    "buy_again": True,
    "sentiment": "positive",
    "topics": ["battery"],
    "competitor_mentions": [],
    "urgency": "low",
    "feature_requests": [],
    "language": "en",
    "confidence": 0.9,
}


def _make_provider() -> GroqProvider:
    return GroqProvider(model=TEACHER_MODEL_ID, api_key="fake-key-not-used", timeout=5)


async def test_label_review_success(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _make_provider()

    async def fake_complete(
        self: GroqProvider,
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        return json.dumps(_GOOD_EXTRACTION), 500, 200

    monkeypatch.setattr(GroqProvider, "complete", fake_complete)

    extraction, error = await label_review(provider, "Great headset, battery lasts long.")
    assert error is None
    assert extraction is not None
    assert extraction.sentiment == "positive"
    assert extraction.stars_inferred == 4


async def test_label_review_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _make_provider()

    async def fake_complete_raises(
        self: GroqProvider, *args: object, **kwargs: object
    ) -> tuple[str, int, int]:
        raise RuntimeError("simulated Groq outage")

    monkeypatch.setattr(GroqProvider, "complete", fake_complete_raises)

    extraction, error = await label_review(provider, "Some review text here.")
    assert extraction is None
    assert error is not None
    assert "provider_error" in error


async def test_label_review_unparseable_response(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _make_provider()

    async def fake_complete_garbage(
        self: GroqProvider, *args: object, **kwargs: object
    ) -> tuple[str, int, int]:
        return "not valid json at all", 100, 50

    monkeypatch.setattr(GroqProvider, "complete", fake_complete_garbage)

    extraction, error = await label_review(provider, "Some review text here.")
    assert extraction is None
    assert error is not None
    assert "parse_error" in error


async def test_label_sample_labels_every_record_and_tags_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _make_provider()

    async def fake_complete(
        self: GroqProvider,
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        return json.dumps(_GOOD_EXTRACTION), 500, 200

    monkeypatch.setattr(GroqProvider, "complete", fake_complete)

    records = [
        {"id": "r1", "text": "Great product", "detected_language": "en"},
        {"id": "r2", "text": "Bahut accha hai yeh", "detected_language": "hi-en"},
    ]
    results = await label_sample(provider, records, delay_seconds=0.0)
    assert len(results) == 2
    assert results[0]["id"] == "r1"
    assert results[0]["teacher_model"] == TEACHER_MODEL_ID
    assert results[0]["error"] is None
    assert results[0]["extraction"]["sentiment"] == "positive"
    assert results[1]["language_hint"] == "hi-en"


async def test_label_sample_one_bad_item_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _make_provider()
    calls = {"n": 0}

    async def flaky_complete(
        self: GroqProvider,
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call fails")
        return json.dumps(_GOOD_EXTRACTION), 500, 200

    monkeypatch.setattr(GroqProvider, "complete", flaky_complete)

    records = [{"id": "r1", "text": "a"}, {"id": "r2", "text": "b"}]
    results = await label_sample(provider, records, delay_seconds=0.0)
    assert results[0]["error"] is not None
    assert results[1]["error"] is None
