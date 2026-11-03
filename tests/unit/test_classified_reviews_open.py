"""On-open extraction for reviews the encoder-first router skipped (S29, flag OFF): core, mark SQL and BFF routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.auth.api_key import ApiKeyContext
from app.core import classified_reviews as CR
from app.core import storage_pg as SP
from app.core.schemas import ReviewRequest
from fastapi.testclient import TestClient

from tests.unit.test_storage_pg import _make_conn, _make_extraction

ORG = str(uuid.uuid4())
HEX = "b" * 64
STORED = "sha256:" + HEX
CTX = ApiKeyContext(
    org_id=ORG, api_key_id=None, key_name="t", usage_record_id="", retention_mode="retained"
)
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def _row(status: str = "classified_only", text: str | None = "The zip broke.") -> dict:
    return {
        "input_hash": STORED, "review_text": text, "review_date": NOW, "product": "Jacket",
        "classification": {"needs_action": {"label": "no"}, "urgency": {"label": "low"}},
        "status": status, "extraction_id": None, "created_at": NOW,
    }  # fmt: skip


# ----------------------------------------------------------------------------------------------------------------- core


def test_id_round_trip_and_summary_never_carries_the_whole_text() -> None:
    assert (
        CR.stored_hash(HEX) == STORED
        and CR.stored_hash(STORED) == STORED
        and CR.public_id(STORED) == HEX
    )
    long = _row(text="x" * 500)
    s = CR.summarize(long)
    assert s["review_id"] == HEX and s["encoder"] == {"needs_action": "no", "urgency": "low"}
    assert len(s["text_preview"]) == 163 and s["text_preview"].endswith("...")
    assert CR.summarize(_row(text="short"))["text_preview"] == "short"


@pytest.mark.asyncio
async def test_open_unknown_review_is_not_found_and_never_calls_the_model() -> None:
    with (
        patch("app.core.classified_reviews.get_classified_review_pg", return_value=None),
        patch("app.api.v2.extract._run_extraction_v2", new=AsyncMock()) as run,
        pytest.raises(CR.ClassifiedReviewNotFoundError),
    ):
        await CR.open_classified_review(CTX, HEX)
    run.assert_not_called()


@pytest.mark.asyncio
async def test_first_open_extracts_with_the_stored_text_date_and_product_then_marks_it() -> None:
    ext = _make_extraction()
    run = AsyncMock(return_value=ext)
    with (
        patch("app.core.classified_reviews.get_classified_review_pg", return_value=_row()),
        patch("app.api.v2.extract._run_extraction_v2", new=run),
        patch(
            "app.core.classified_reviews.mark_classified_review_extracted_pg", return_value=True
        ) as mark,
    ):
        out = await CR.open_classified_review(CTX, HEX)
    assert out is ext
    req, ctx = run.await_args.args
    assert (
        isinstance(req, ReviewRequest) and req.text == "The zip broke." and req.review_date == NOW
    )
    assert ctx is CTX and run.await_args.kwargs == {"product_override": "Jacket"}
    mark.assert_called_once_with(ORG, STORED)


@pytest.mark.asyncio
async def test_second_open_returns_the_stored_extraction_without_a_model_call() -> None:
    ext = _make_extraction()
    with (
        patch(
            "app.core.classified_reviews.get_classified_review_pg",
            return_value=_row("extracted", None),
        ),
        patch("app.core.classified_reviews.get_by_hash_pg", return_value=ext),
        patch("app.api.v2.extract._run_extraction_v2", new=AsyncMock()) as run,
    ):
        assert await CR.open_classified_review(CTX, HEX) is ext
    run.assert_not_called()


@pytest.mark.asyncio
async def test_open_after_the_extraction_was_purged_is_gone_not_re_extracted() -> None:
    with (
        patch(
            "app.core.classified_reviews.get_classified_review_pg",
            return_value=_row("extracted", None),
        ),
        patch("app.core.classified_reviews.get_by_hash_pg", return_value=None),
        patch("app.api.v2.extract._run_extraction_v2", new=AsyncMock()) as run,
        pytest.raises(CR.ExtractionPurgedError),
    ):
        await CR.open_classified_review(CTX, HEX)
    run.assert_not_called()  # the raw text is gone by design; there is nothing to extract from


def test_mark_extracted_only_moves_a_classified_only_row_and_nulls_the_text() -> None:
    conn, cur = _make_conn()
    cur.rowcount = 1
    with patch("app.core.storage_pg._db_connect", return_value=conn):
        assert SP.mark_classified_review_extracted_pg(ORG, STORED) is True
    sql = cur.execute.call_args_list[-1].args[0]
    assert (
        "review_text = NULL" in sql
        and "status = 'classified_only'" in sql
        and "extraction_id = (SELECT id" in sql
    )


# --------------------------------------------------------------------------------------------------------------- BFF routes


@pytest.fixture
def client():
    from app.api.bff.classified import require_session, require_session_read
    from app.main import app

    app.dependency_overrides[require_session] = lambda: CTX
    app.dependency_overrides[require_session_read] = lambda: CTX
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(require_session, None)
    app.dependency_overrides.pop(require_session_read, None)


def _flag(on: bool):
    return patch(
        "app.api.bff.classified.get_settings", return_value=SimpleNamespace(enable_encoder_first=on)
    )


def test_routes_look_absent_while_the_flag_is_off(client: TestClient) -> None:
    with _flag(False):
        assert client.get("/bff/classified-reviews").status_code == 404
        assert client.post(f"/bff/classified-reviews/{HEX}/open").status_code == 404


def test_list_returns_summaries_for_the_callers_org(client: TestClient) -> None:
    with (
        _flag(True),
        patch("app.api.bff.classified.list_classified_reviews_pg", return_value=[_row()]) as lst,
    ):
        r = client.get("/bff/classified-reviews?limit=5")
    assert r.status_code == 200 and lst.call_args.args == (ORG, 5, 0)
    assert (
        r.json()["results"][0]["review_id"] == HEX and "review_text" not in r.json()["results"][0]
    )


def test_open_validates_the_id_and_maps_not_found_and_gone(client: TestClient) -> None:
    with _flag(True):
        assert client.post("/bff/classified-reviews/not-a-hash/open").status_code == 422
        with patch(
            "app.api.bff.classified.open_classified_review",
            side_effect=CR.ClassifiedReviewNotFoundError("x"),
        ):
            assert client.post(f"/bff/classified-reviews/{HEX}/open").status_code == 404
        with patch(
            "app.api.bff.classified.open_classified_review",
            side_effect=CR.ExtractionPurgedError("x"),
        ):
            assert client.post(f"/bff/classified-reviews/{HEX}/open").status_code == 410
        with patch(
            "app.api.bff.classified.open_classified_review",
            new=AsyncMock(return_value=_make_extraction()),
        ):
            ok = client.post(f"/bff/classified-reviews/{HEX}/open")
    assert ok.status_code == 200 and ok.json()["product"] == "Widget"
