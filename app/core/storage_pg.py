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
    cur.execute('SET LOCAL "app.current_org_id" = %s', (org_id,))


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
            "id",
            "input_hash",
            "product",
            "stars",
            "stars_inferred",
            "buy_again",
            "sentiment",
            "urgency",
            "language",
            "review_length_chars",
            "confidence",
            "topics",
            "competitor_mentions",
            "pros",
            "cons",
            "feature_requests",
            "model",
            "prompt_version",
            "schema_version",
            "latency_ms",
            "extracted_at",
            "created_at",
        ]
        return [dict(zip(cols, row, strict=False)) for row in rows]
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
            "SELECT urgency, COUNT(*) FROM public.extractions WHERE org_id = %s GROUP BY urgency",
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
# Batch job helpers (v2 Postgres-backed)
# ---------------------------------------------------------------------------


def create_batch_job_pg(
    org_id: str,
    job_id: str,
    total: int,
    source_columns: str | None = None,
) -> None:
    """Insert a new batch job record (org-scoped)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO public.batch_jobs
                (job_id, org_id, total, source_columns)
            VALUES (%s, %s, %s, %s)
            """,
            (job_id, org_id, total, source_columns),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_batch_job_pg(org_id: str, job_id: str) -> dict[str, Any] | None:
    """Return a batch job row for this org, or None if not found / wrong org."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT job_id, org_id, status, total, processed, failed,
                   source_columns, created_at, completed_at
            FROM public.batch_jobs
            WHERE job_id = %s AND org_id = %s
            """,
            (job_id, org_id),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        cols = [
            "job_id",
            "org_id",
            "status",
            "total",
            "processed",
            "failed",
            "source_columns",
            "created_at",
            "completed_at",
        ]
        return dict(zip(cols, row, strict=False))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_batch_job_pg(
    org_id: str,
    job_id: str,
    *,
    processed: int | None = None,
    failed: int | None = None,
    status: str | None = None,
    source_columns: str | None = None,
) -> None:
    """Update mutable fields on a batch job (org-scoped)."""
    parts: list[str] = []
    params: list[Any] = []
    if processed is not None:
        parts.append("processed = %s")
        params.append(processed)
    if failed is not None:
        parts.append("failed = %s")
        params.append(failed)
    if status is not None:
        parts.append("status = %s")
        params.append(status)
        if status in ("done", "failed"):
            parts.append("completed_at = now()")
    if source_columns is not None:
        parts.append("source_columns = %s")
        params.append(source_columns)
    if not parts:
        return
    params.extend([org_id, job_id])
    sql = f"UPDATE public.batch_jobs SET {', '.join(parts)} WHERE org_id = %s AND job_id = %s"
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Authenticity audit helpers (IS 19000:2022 compliance)
# ---------------------------------------------------------------------------


def get_authenticity_audit_by_hash_pg(
    org_id: str,
    review_hash: str,
) -> dict[str, object] | None:
    """Return a stored authenticity audit row for (org_id, review_hash), or None if absent.

    Used as a pre-LLM short-circuit in POST /v2/authenticity to avoid re-scoring
    identical review text.  Reuses existing columns — no DDL required.

    Args:
        org_id:      Tenant identifier — query is scoped to this org via RLS + WHERE.
        review_hash: SHA-256 hex digest of the raw review text.

    Returns:
        dict with keys ``score``, ``label``, ``flags`` (list[str]), ``review_hash``
        when a matching row exists; ``None`` otherwise.
    """
    import json as _json

    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT score, label, flags, review_hash "
            "FROM public.authenticity_audits "
            "WHERE org_id = %s AND review_hash = %s "
            "LIMIT 1",
            (org_id, review_hash),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        score_val, label_val, flags_raw, rh = row
        flags_list: list[str] = (
            _json.loads(flags_raw) if isinstance(flags_raw, str) else (flags_raw or [])
        )
        return {
            "score": float(score_val),
            "label": str(label_val),
            "flags": flags_list,
            "review_hash": str(rh),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_authenticity_audit_pg(
    org_id: str,
    review_hash: str,
    score: float,
    label: str,
    flags: list[str],
) -> None:
    """Insert one authenticity audit record, scoped to org_id."""
    import json as _json

    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "INSERT INTO public.authenticity_audits (org_id, review_hash, score, label, flags)"
            " VALUES (%s, %s, %s, %s, %s)",
            (org_id, review_hash, score, label, _json.dumps(flags)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def count_authenticity_audits_pg(org_id: str) -> int:
    """Return count of audit rows visible to org_id (used in isolation tests)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT COUNT(*) FROM public.authenticity_audits WHERE org_id = %s",
            (org_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_VALID_BUCKETS = frozenset({"day", "week", "month"})


def authenticity_audit_summary_pg(
    org_id: str,
    since: datetime | None = None,
    until: datetime | None = None,
    bucket: str = "week",
) -> dict[str, Any]:
    """Return raw aggregated authenticity audit data for org_id.

    Callers are responsible for mapping stored label/flag values to
    display-safe strings before returning data to API consumers.

    Args:
        org_id:  Tenant identifier — all queries are scoped to this org.
        since:   Optional lower bound on created_at (inclusive).
        until:   Optional upper bound on created_at (inclusive).
        bucket:  Time-series bucket granularity — must be one of
                 ``day``, ``week``, or ``month`` (validated at API layer).

    Returns:
        dict with keys:
          total_audited, label_genuine, label_suspicious, label_likely_fake,
          mean_score, flag_frequency, time_series.
    """
    if bucket not in _VALID_BUCKETS:
        raise ValueError(f"bucket must be one of {_VALID_BUCKETS}, got {bucket!r}")

    # Build optional time-filter fragments once; reused across all queries.
    time_parts: list[str] = []
    time_params: list[Any] = []
    if since is not None:
        time_parts.append("created_at >= %s")
        time_params.append(since)
    if until is not None:
        time_parts.append("created_at <= %s")
        time_params.append(until)
    time_clause = (" AND " + " AND ".join(time_parts)) if time_parts else ""

    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)

        # 1. Total count + per-label counts
        cur.execute(
            f"SELECT COUNT(*), "
            f"COUNT(*) FILTER (WHERE label = 'genuine'), "
            f"COUNT(*) FILTER (WHERE label = 'suspicious'), "
            f"COUNT(*) FILTER (WHERE label = 'likely_fake') "
            f"FROM public.authenticity_audits "
            f"WHERE org_id = %s{time_clause}",
            [org_id, *time_params],
        )
        row = cur.fetchone()
        total, lbl_genuine, lbl_suspicious, lbl_likely_fake = (
            (row[0], row[1], row[2], row[3]) if row else (0, 0, 0, 0)
        )

        # 2. Mean score — guard: returns None when table is empty.
        cur.execute(
            f"SELECT AVG(score) FROM public.authenticity_audits WHERE org_id = %s{time_clause}",
            [org_id, *time_params],
        )
        avg_row = cur.fetchone()
        mean_score: float | None = float(avg_row[0]) if avg_row and avg_row[0] is not None else None

        # 3. Flag frequency: unnest the TEXT JSON column as jsonb.
        cur.execute(
            f"SELECT flag, COUNT(*) AS cnt "
            f"FROM public.authenticity_audits, "
            f"jsonb_array_elements_text(flags::jsonb) AS flag "
            f"WHERE org_id = %s{time_clause} "
            f"GROUP BY flag ORDER BY cnt DESC",
            [org_id, *time_params],
        )
        flag_frequency = [{"flag": r[0], "count": int(r[1])} for r in cur.fetchall()]

        # 4. Time series: per-bucket totals + non-genuine (flagged) count.
        cur.execute(
            f"SELECT date_trunc(%s, created_at) AS period, "
            f"COUNT(*) AS audited, "
            f"COUNT(*) FILTER (WHERE label <> 'genuine') AS flagged "
            f"FROM public.authenticity_audits "
            f"WHERE org_id = %s{time_clause} "
            f"GROUP BY period ORDER BY period",
            [bucket, org_id, *time_params],
        )
        time_series = [
            {"period": r[0], "audited": int(r[1]), "flagged": int(r[2])} for r in cur.fetchall()
        ]

        conn.commit()
        return {
            "total_audited": int(total),
            "label_genuine": int(lbl_genuine),
            "label_suspicious": int(lbl_suspicious),
            "label_likely_fake": int(lbl_likely_fake),
            "mean_score": mean_score,
            "flag_frequency": flag_frequency,
            "time_series": time_series,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Theme-trend aggregation (GET /v2/insights/trends)
# ---------------------------------------------------------------------------

# Whitelist for trend_of parameter — only these column names are ever injected
# into SQL as identifiers. The dict serves as both the allow-list and the
# canonical name map so that SQL injection on column names is structurally
# impossible: user input never reaches the query string directly.
_TREND_OF_COLUMNS: dict[str, str] = {
    "topics": "topics",
    "cons": "cons",
}

_VALID_TREND_BUCKETS = frozenset({"day", "week", "month"})


def theme_trends_pg(
    org_id: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    bucket: str = "week",
    trend_of: str = "topics",
    product: str | None = None,
    language: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Aggregate theme/complaint trends over time for the given org.

    Returns raw structured data — the API layer is responsible for shaping
    the final response (delta computation, period ISO formatting, etc.).

    SQL injection prevention
    ------------------------
    ``trend_of`` is validated at the API layer (422 if invalid) and again here
    via ``_TREND_OF_COLUMNS``. The whitelisted column name is interpolated as a
    literal into the SQL template using an ``f-string`` only after lookup from
    the safe dict — the raw user string is never used directly.

    ``bucket`` is passed as a parameterised ``%s`` placeholder in
    ``date_trunc(%s, ...)`` — Postgres treats it as a string value, never as
    an identifier, so no injection risk exists.

    Args:
        org_id:   Tenant identifier — all queries are scoped to this value.
        since:    Optional lower bound on created_at (inclusive).
        until:    Optional upper bound on created_at (inclusive).
        bucket:   Time-series granularity — ``day``, ``week``, or ``month``.
        trend_of: JSONB column to unnest — ``topics`` or ``cons``.
        product:  Optional product filter (ILIKE %<product>%).
        language: Optional exact language filter.
        limit:    Maximum number of top themes to return (default 10, max 50).

    Returns:
        dict with key ``themes``, a list of per-theme dicts each containing:
          theme, total, rows (raw per-(theme, period, language) rows).
    """
    if bucket not in _VALID_TREND_BUCKETS:
        raise ValueError(f"bucket must be one of {_VALID_TREND_BUCKETS}, got {bucket!r}")

    col = _TREND_OF_COLUMNS.get(trend_of)
    if col is None:
        raise ValueError(f"trend_of must be one of {set(_TREND_OF_COLUMNS)}, got {trend_of!r}")

    # Build shared optional-filter fragments (reused in both queries).
    filter_parts: list[str] = []
    filter_params: list[Any] = []
    if since is not None:
        filter_parts.append("created_at >= %s")
        filter_params.append(since)
    if until is not None:
        filter_parts.append("created_at <= %s")
        filter_params.append(until)
    if product is not None:
        filter_parts.append("product ILIKE %s")
        filter_params.append(f"%{product}%")
    if language is not None:
        filter_parts.append("language = %s")
        filter_params.append(language)

    extra_clause = (" AND " + " AND ".join(filter_parts)) if filter_parts else ""

    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)

        # Query A — top-N themes by total count in the window.
        # ``col`` comes from _TREND_OF_COLUMNS — never from raw user input.
        cur.execute(
            f"SELECT theme, COUNT(*) AS cnt "
            f"FROM public.extractions, jsonb_array_elements_text({col}) AS theme "
            f"WHERE org_id = %s{extra_clause} "
            f"GROUP BY theme ORDER BY cnt DESC LIMIT %s",
            [org_id, *filter_params, limit],
        )
        top_rows = cur.fetchall()
        top_themes = [r[0] for r in top_rows]
        theme_totals = {r[0]: int(r[1]) for r in top_rows}

        if not top_themes:
            conn.commit()
            return {"themes": []}

        # Query B — per-(theme, period, language) counts for the top themes.
        cur.execute(
            f"SELECT theme, date_trunc(%s, created_at) AS period, language, COUNT(*) "
            f"FROM public.extractions, jsonb_array_elements_text({col}) AS theme "
            f"WHERE org_id = %s AND theme = ANY(%s){extra_clause} "
            f"GROUP BY theme, period, language ORDER BY theme, period",
            [bucket, org_id, top_themes, *filter_params],
        )
        detail_rows = cur.fetchall()

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Assemble per-theme data structures in Python.
    # Each detail row: (theme, period_datetime, language, count)
    from collections import defaultdict

    # theme → { period_dt → {lang → count} }
    tree: dict[str, dict[Any, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for theme, period_dt, lang, cnt in detail_rows:
        tree[theme][period_dt][lang or "unknown"] = int(cnt)

    themes_out: list[dict[str, Any]] = []
    for theme in top_themes:
        by_period = tree[theme]
        # Sort periods chronologically.
        sorted_periods = sorted(by_period.keys())

        # Language breakdown summed across all periods.
        lang_totals: dict[str, int] = defaultdict(int)
        for lang_counts in by_period.values():
            for lang, cnt in lang_counts.items():
                lang_totals[lang] += cnt

        themes_out.append(
            {
                "theme": theme,
                "total": theme_totals[theme],
                "sorted_periods": sorted_periods,
                "by_period": {p: dict(by_period[p]) for p in sorted_periods},
                "by_language": dict(lang_totals),
            }
        )

    return {"themes": themes_out}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _row_to_extraction_v2(row: tuple[Any, ...], org_id: str) -> ReviewExtractionV2:
    (
        product,
        stars,
        stars_inferred,
        buy_again,
        sentiment,
        urgency,
        language,
        review_length_chars,
        confidence,
        topics,
        competitor_mentions,
        pros,
        cons,
        feature_requests,
        model,
        prompt_version,
        schema_version,
        latency_ms,
        extracted_at,
        input_hash,
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
        extracted_at=extracted_at
        if isinstance(extracted_at, datetime)
        else datetime.fromisoformat(str(extracted_at)),
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
