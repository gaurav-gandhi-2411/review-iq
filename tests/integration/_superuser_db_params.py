"""Shared superuser (postgres role) connection params for tests/integration/.

Single source of truth for the 7 files that previously each hardcoded
`db.<project-ref>.supabase.co` -- introduced 2026-08-01 while building the
pre-cutover ephemeral-Postgres CI job (P3), which needs every one of these
files to point at a local container instead of the real Supabase project.

Defaults to the real production host, so every existing manual/live-verification
invocation of these tests (`uv run pytest tests/integration/... -m integration`)
is completely unchanged unless the new env vars are explicitly set -- which only
the ephemeral-Postgres CI job does.
"""

from __future__ import annotations

import os

_DEFAULT_HOST = "db.enqpluazgxewepchdeut.supabase.co"
_DEFAULT_SSLMODE = "require"


def superuser_db_params() -> dict[str, object]:
    """Return connection kwargs for psycopg2.connect(**params), postgres superuser."""
    return {
        "host": os.environ.get("TEST_DB_HOST", _DEFAULT_HOST),
        "port": int(os.environ.get("TEST_DB_PORT", "5432")),
        "dbname": "postgres",
        "user": "postgres",
        "password": os.environ.get("SUPABASE_DB_PASSWORD", ""),
        "sslmode": os.environ.get("TEST_DB_SSLMODE", _DEFAULT_SSLMODE),
        "connect_timeout": 15,
    }
