"""Apply all migrations in supabase/migrations/ to the live Supabase DB.

Usage:
    uv run python supabase/push.py

Reads credentials from .env (direct connection, port 5432).
Migrations are applied in filename order. Each file is idempotent
(IF NOT EXISTS / CREATE OR REPLACE / DROP IF EXISTS), so re-running is safe.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parents[1]
load_dotenv(ROOT / ".env")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def main() -> None:
    direct_url = os.environ["SUPABASE_DIRECT_URL"]
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print("No migration files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting via SUPABASE_DIRECT_URL (port 5432) …")
    conn = psycopg2.connect(direct_url)
    conn.autocommit = False

    try:
        for path in migration_files:
            sql = path.read_text(encoding="utf-8")
            print(f"  Applying {path.name} …", end=" ", flush=True)
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            print("OK")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\n{len(migration_files)} migration(s) applied.")


if __name__ == "__main__":
    main()
