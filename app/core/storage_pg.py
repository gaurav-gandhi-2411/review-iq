"""Postgres extractions repository for v2 multi-tenant endpoints.

Each public function opens a fresh connection from the pooler
(SUPABASE_DATABASE_URL, port 6543, transaction mode) and scopes all
queries to `org_id` at two levels:

  1. App-level: every WHERE clause includes org_id = %s.
  2. Postgres RLS: the transaction sets SET LOCAL "app.current_org_id" = <org_id>
     before issuing data queries, so RLS policies enforce the boundary even if
     a bug drops the WHERE clause.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg2
import structlog

from app.core.config import get_settings
from app.core.schemas import (
    ExtractionMetaV2,
    ReviewExtractionV2,
    Sentiment,
    Urgency,
)

log = structlog.get_logger(__name__)


def _db_connect() -> psycopg2.extensions.connection:
    settings = get_settings()
    return psycopg2.connect(settings.supabase_database_url)


def _set_tenant(cur: Any, org_id: str) -> None:
    """Set RLS context for current transaction."""
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SET LOCAL \"app.current_org_id\" = %s", (org_id,))


def get_by_hash_pg(org_id: str, input_hash: str) -> ReviewExtractionV2 | None:
    """Return cached extraction for this org if input_hash already exists."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT product, stars, stars_inferred, buy_again, sentiment, urgency, "
            "language, review_length_chars, confidence, topics, competitor_mentions, "
            "pros, cons, feature_requests, model, prompt_version, schema_version, "
            "latency_ms, extracted_at, input_hash "
            "FROM public.extractions WHERE org_id = %s AND input_hash = %s",
            (org_id, input_hash),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        return _row_to_extraction_v2(row, org_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_extraction_pg(
    org_id: str,
    api_key_id: str,
    input_hash: str,
    review_text: str,
    extraction: ReviewExtractionV2,
    model: str,
    prompt_version: str,
    schema_version: str,
    latency_ms: int | None,
    is_suspicious: bool,
) -> str:
    """Persist a new extraction. Returns the row id (UUID as str)."""
    meta = extraction.extraction_meta
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            """
            INSERT INTO public.extractions (
                org_id, api_key_id, input_hash, review_text,
                product, stars, stars_inferred, buy_again, sentiment, urgency,
                language, review_length_chars, confidence,
                topics, competitor_mentions, pros, cons, feature_requests,
                model, prompt_version, schema_version, latency_ms, extracted_at,
                is_suspicious
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s
            )
            ON CONFLICT (org_id, input_hash) DO NOTHING
            RETURNING id
            """,
            (
                org_id,
                api_key_id,
                input_hash,
                review_text,
                extraction.product,
                extraction.stars,
                extraction.stars_inferred,
                None if extraction.buy_again is None else bool(extraction.buy_again),
                extraction.sentiment,
                str(extraction.urgency),
                extraction.language,
                extraction.review_length_chars,
                extraction.confidence,
                json.dumps(extraction.topics),
                json.dumps(extraction.competitor_mentions),
                json.dumps(extraction.pros),
                json.dumps(extraction.cons),
                json.dumps(extraction.feature_requests),
                model,
                prompt_version,
                schema_version,
                latency_ms,
                meta.extracted_at if meta else datetime.utcnow(),
                is_suspicious,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return str(row[0]) if row else ""
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_usage_tokens(usage_record_id: str, tokens_in: int, tokens_out: int) -> None:
    """Write tokens_in / tokens_out on a usage_record after a successful LLM call.

    tokens_used is a generated column (tokens_in + tokens_out) — do not write it directly.
    On LLM failure this function is never called; the row stays at 0/0.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.usage_records SET tokens_in = %s, tokens_out = %s WHERE id = %s",
            (tokens_in, tokens_out, usage_record_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_extractions_pg(
    org_id: str,
    *,
    product: str | None = None,
    sentiment: Sentiment | None = None,
    urgency: Urgency | None = None,
    has_competitor_mention: bool | None = None,
    topic: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query stored extractions for this org with optional filters."""
    where: list[str] = ["org_id = %s"]
    params: list[Any] = [org_id]

    if product:
        where.append("product ILIKE %s")
        params.append(f"%{product}%")
    if sentiment:
        where.append("sentiment = %s")
        params.append(str(sentiment))
    if urgency:
        where.append("urgency = %s")
        params.append(str(urgency))
    if has_competitor_mention is True:
        where.append("jsonb_array_length(competitor_mentions) > 0")
    elif has_competitor_mention is False:
        where.append("jsonb_array_length(competitor_mentions) = 0")
    if topic:
        where.append("competitor_mentions @> %s::jsonb")
        params.append(json.dumps([topic]))
    if since:
        where.append("created_at >= %s")
        params.append(since)
    if until:
        where.append("created_at <= %s")
        params.append(until)

    where_clause = "WHERE " + " AND ".join(where)
    params.extend([limit, offset])

    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            f"""
            SELECT id, input_hash, product, stars, stars_inferred, buy_again,
                   sentiment, urgency, language, review_length_chars, confidence,
                   topics, competitor_mentions, pros, cons, feature_requests,
                   model, prompt_version, schema_version, latency_ms,
                   extracted_at, created_at
            FROM public.extractions
            {where_clause}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        rows = cur.fetchall()
        conn.commit()
        cols = [
            "id", "input_hash", "product", "stars", "stars_inferred", "buy_again",
            "sentiment", "urgency", "language", "review_length_chars", "confidence",
            "topics", "competitor_mentions", "pros", "cons", "feature_requests",
            "model", "prompt_version", "schema_version", "latency_ms",
            "extracted_at", "created_at",
        ]
        return [dict(zip(cols, row)) for row in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def aggregate_extractions_pg(org_id: str) -> dict[str, Any]:
    """Return aggregated insights for this org."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)

        cur.execute(
            "SELECT COUNT(*), "
            "COUNT(*) FILTER (WHERE sentiment = 'positive'), "
            "COUNT(*) FILTER (WHERE sentiment = 'negative'), "
            "COUNT(*) FILTER (WHERE sentiment = 'neutral'), "
            "COUNT(*) FILTER (WHERE sentiment = 'mixed') "
            "FROM public.extractions WHERE org_id = %s",
            (org_id,),
        )
        total, pos, neg, neu, mix = cur.fetchone()

        cur.execute(
            "SELECT urgency, COUNT(*) FROM public.extractions "
            "WHERE org_id = %s GROUP BY urgency",
            (org_id,),
        )
        urgency_counts = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            "SELECT topic, COUNT(*) as cnt "
            "FROM public.extractions, "
            "jsonb_array_elements_text(topics) AS topic "
            "WHERE org_id = %s "
            "GROUP BY topic ORDER BY cnt DESC LIMIT 10",
            (org_id,),
        )
        top_topics = [{"topic": r[0], "count": r[1]} for r in cur.fetchall()]

        cur.execute(
            "SELECT comp, COUNT(*) as cnt "
            "FROM public.extractions, "
            "jsonb_array_elements_text(competitor_mentions) AS comp "
            "WHERE org_id = %s "
            "GROUP BY comp ORDER BY cnt DESC LIMIT 10",
            (org_id,),
        )
        top_competitors = [{"competitor": r[0], "count": r[1]} for r in cur.fetchall()]

        conn.commit()
        return {
            "total_extractions": total,
            "sentiment_breakdown": {
                "positive": pos,
                "negative": neg,
                "neutral": neu,
                "mixed": mix,
            },
            "urgency_breakdown": urgency_counts,
            "top_topics": top_topics,
            "top_competitor_mentions": top_competitors,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _row_to_extraction_v2(row: tuple[Any, ...], org_id: str) -> ReviewExtractionV2:
    (
        product, stars, stars_inferred, buy_again, sentiment, urgency,
        language, review_length_chars, confidence, topics, competitor_mentions,
        pros, cons, feature_requests, model, prompt_version, schema_version,
        latency_ms, extracted_at, input_hash,
    ) = row

    def _load(val: Any) -> list[str]:
        if val is None:
            return []
        if isinstance(val, list):
            return val
        return json.loads(val)

    meta = ExtractionMetaV2(
        model=model,
        prompt_version=prompt_version,
        schema_version=schema_version,
        extracted_at=extracted_at if isinstance(extracted_at, datetime) else datetime.fromisoformat(str(extracted_at)),
        latency_ms=latency_ms,
        input_hash=input_hash,
        org_id=org_id,
    )
    return ReviewExtractionV2(
        product=product,
        stars=stars,
        stars_inferred=stars_inferred,
        pros=_load(pros),
        cons=_load(cons),
        buy_again=buy_again,
        sentiment=Sentiment(sentiment) if sentiment else None,
        topics=_load(topics),
        competitor_mentions=_load(competitor_mentions),
        urgency=Urgency(urgency) if urgency else Urgency.low,
        feature_requests=_load(feature_requests),
        language=language or "en",
        review_length_chars=review_length_chars,
        confidence=confidence,
        extraction_meta=meta,
    )
