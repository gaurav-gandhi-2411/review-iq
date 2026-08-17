"""Wave 1 Section H: corpus_pipeline.consensus_validate -- mocked-client tests only.

No live Groq calls. A fake async client (matching the tiny slice of the
groq.AsyncGroq surface this module actually uses:
`client.chat.completions.create(...)`) stands in for the real client.
"""

from __future__ import annotations

import json
from typing import Any

from benchmark.vernacular_v2.corpus_pipeline.consensus_validate import (
    JUDGE_MODELS,
    _consensus_for_field,
    compute_agreement_report,
    consensus_for_item,
    parse_judge_response,
    validate_sample,
)

_EXTRACTION_A = {
    "product": "headset",
    "stars": None,
    "stars_inferred": 5,
    "pros": [],
    "cons": [],
    "buy_again": True,
    "sentiment": "positive",
    "topics": [],
    "competitor_mentions": [],
    "urgency": "low",
    "feature_requests": [],
    "language": "en",
    "confidence": None,
}
_EXTRACTION_B_DISAGREE = {**_EXTRACTION_A, "sentiment": "negative", "urgency": "high"}


def test_parse_judge_response_valid_json() -> None:
    parsed = parse_judge_response(json.dumps(_EXTRACTION_A))
    assert parsed is not None
    assert parsed.sentiment == "positive"


def test_parse_judge_response_strips_code_fence() -> None:
    fenced = "```json\n" + json.dumps(_EXTRACTION_A) + "\n```"
    parsed = parse_judge_response(fenced)
    assert parsed is not None


def test_parse_judge_response_invalid_json_returns_none() -> None:
    assert parse_judge_response("not json") is None


def test_consensus_for_field_unanimous() -> None:
    label, level = _consensus_for_field({"j1": "positive", "j2": "positive"})
    assert label == "positive"
    assert level == "unanimous"


def test_consensus_for_field_split_two_judges_disagree() -> None:
    label, level = _consensus_for_field({"j1": "positive", "j2": "negative"})
    assert label is None
    assert level == "split"


def test_consensus_for_field_insufficient_votes() -> None:
    label, level = _consensus_for_field({"j1": "positive", "j2": None})
    assert label is None
    assert level == "insufficient"


def test_consensus_for_item_agrees_on_all_fields() -> None:
    from app.core.schemas import ReviewExtractionLLMOutput

    a = ReviewExtractionLLMOutput.model_validate(_EXTRACTION_A)
    b = ReviewExtractionLLMOutput.model_validate(_EXTRACTION_A)
    result = consensus_for_item({"j1": a, "j2": b})
    assert result["sentiment"]["silver"] == "positive"
    assert result["sentiment"]["agreement"] == "unanimous"


def test_consensus_for_item_disagreement_on_sentiment() -> None:
    from app.core.schemas import ReviewExtractionLLMOutput

    a = ReviewExtractionLLMOutput.model_validate(_EXTRACTION_A)
    b = ReviewExtractionLLMOutput.model_validate(_EXTRACTION_B_DISAGREE)
    result = consensus_for_item({"j1": a, "j2": b})
    assert result["sentiment"]["silver"] is None
    assert result["sentiment"]["agreement"] == "split"
    assert result["language"]["silver"] == "en"  # both agree here


def test_compute_agreement_report_counts_split_and_unanimous() -> None:
    records = [
        {
            "consensus": {
                "sentiment": {"agreement": "unanimous", "silver": "positive", "votes": {}},
                "urgency": {"agreement": "split", "silver": None, "votes": {}},
                "buy_again": {"agreement": "unanimous", "silver": "True", "votes": {}},
                "language": {"agreement": "unanimous", "silver": "en", "votes": {}},
            }
        },
        {
            "consensus": {
                "sentiment": {"agreement": "split", "silver": None, "votes": {}},
                "urgency": {"agreement": "unanimous", "silver": "low", "votes": {}},
                "buy_again": {"agreement": "unanimous", "silver": "True", "votes": {}},
                "language": {"agreement": "unanimous", "silver": "en", "votes": {}},
            }
        },
    ]
    report = compute_agreement_report(records)
    assert report["sentiment"]["n"] == 2
    assert report["sentiment"]["unanimous"] == 1
    assert report["sentiment"]["split"] == 1
    assert report["sentiment"]["non_split_consensus_rate_pct"] == 50.0
    assert report["buy_again"]["non_split_consensus_rate_pct"] == 100.0


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, response_by_model: dict[str, str]) -> None:
        self._response_by_model = response_by_model

    async def create(self, *, model: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._response_by_model[model])


class _FakeChat:
    def __init__(self, response_by_model: dict[str, str]) -> None:
        self.completions = _FakeCompletions(response_by_model)


class _FakeGroqClient:
    def __init__(self, response_by_model: dict[str, str]) -> None:
        self.chat = _FakeChat(response_by_model)


async def test_validate_sample_both_judges_agree() -> None:
    judge_ids = [m["id"] for m in JUDGE_MODELS]
    client = _FakeGroqClient({jid: json.dumps(_EXTRACTION_A) for jid in judge_ids})
    records = [{"id": "r1", "text": "Great headset"}]
    results = await validate_sample(client, records, delay_seconds=0.0)
    assert len(results) == 1
    assert results[0]["consensus"]["sentiment"]["silver"] == "positive"
    assert results[0]["consensus"]["sentiment"]["agreement"] == "unanimous"
    assert results[0]["judge_errors"] is None


async def test_validate_sample_one_judge_raises_is_recorded_not_fatal() -> None:
    judge_ids = [m["id"] for m in JUDGE_MODELS]
    good_id, bad_id = judge_ids[0], judge_ids[1]

    client = _FakeGroqClient({good_id: json.dumps(_EXTRACTION_A)})

    async def failing_create(*, model: str, **kwargs: Any) -> _FakeResponse:
        if model == bad_id:
            raise RuntimeError("simulated outage")
        return _FakeResponse(json.dumps(_EXTRACTION_A))

    client.chat.completions.create = failing_create  # type: ignore[method-assign]

    records = [{"id": "r1", "text": "Great headset"}]
    results = await validate_sample(client, records, delay_seconds=0.0)
    assert results[0]["judge_errors"] is not None
    assert bad_id in results[0]["judge_errors"]
    # Only 1 usable vote (the good judge) -> insufficient, not a crash.
    assert results[0]["consensus"]["sentiment"]["agreement"] == "insufficient"
