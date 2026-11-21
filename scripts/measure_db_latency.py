"""Measure DB connection/query latency from wherever this runs (S23).

Reads the DB URL from SUPABASE_DATABASE_URL (never printed). Output is JSON with
only host/port and timing statistics. Self-contained (stdlib + psycopg2) so it can
be shipped inline into a Cloud Run Job on the serving image, which has no scripts/.
"""

from __future__ import annotations

import json
import os
import socket
import statistics
import sys
import time
import urllib.parse

import psycopg2
from psycopg2 import pool as pgpool


def _stats(xs: list[float]) -> dict[str, float | int]:
    s = sorted(xs)

    def q(p: float) -> float:
        return round(s[min(len(s) - 1, int(p * len(s)))], 2)

    return {
        "n": len(s),
        "min": round(s[0], 2),
        "p50": round(statistics.median(s), 2),
        "p95": q(0.95),
        "p99": q(0.99),
        "max": round(s[-1], 2),
        "mean": round(statistics.fmean(s), 2),
    }


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def main(n_new: int = 60, n_reuse: int = 200) -> dict:
    dsn = os.environ["SUPABASE_DATABASE_URL"]
    u = urllib.parse.urlparse(dsn)
    host, port = u.hostname or "", u.port or 5432
    out: dict = {"host": host, "port": port, "where": os.environ.get("MEASURE_LABEL", "unlabelled")}
    errs: dict[str, int] = {}

    def note(e: Exception) -> None:
        errs[type(e).__name__] = errs.get(type(e).__name__, 0) + 1

    dns, tcp = [], []
    for _ in range(n_new):
        t0 = time.perf_counter()
        ai = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        dns.append(_ms(t0))
        fam, typ, proto, _, addr = ai[0]
        s = socket.socket(fam, typ, proto)
        s.settimeout(10)
        t0 = time.perf_counter()
        s.connect(addr)
        tcp.append(_ms(t0))
        s.close()
    out["dns_ms"] = _stats(dns)
    out["tcp_connect_ms"] = _stats(tcp)

    conn_t, query_t, close_t, total_t = [], [], [], []
    for _ in range(n_new):
        t_all = time.perf_counter()
        try:
            t0 = time.perf_counter()
            c = psycopg2.connect(dsn, connect_timeout=30)
            ct = _ms(t0)
            cur = c.cursor()
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchone()
            qt = _ms(t0)
            t0 = time.perf_counter()
            c.close()
            clt = _ms(t0)
        except Exception as e:  # noqa: BLE001 - count by class only; messages may embed the DSN
            note(e)
            continue
        conn_t.append(ct)
        query_t.append(qt)
        close_t.append(clt)
        total_t.append(_ms(t_all))
    if total_t:
        out["new_conn_connect_ms"] = _stats(conn_t)
        out["new_conn_select1_ms"] = _stats(query_t)
        out["new_conn_close_ms"] = _stats(close_t)
        out["new_conn_total_ms"] = _stats(total_t)

    try:
        c = psycopg2.connect(dsn, connect_timeout=30)
        cur = c.cursor()
        cur.execute("SELECT 1")
        r = []
        for _ in range(n_reuse):
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchone()
            r.append(_ms(t0))
        out["reused_conn_select1_ms"] = _stats(r)
        c.close()
        p = pgpool.ThreadedConnectionPool(1, 2, dsn, connect_timeout=30)
        pt = []
        for _ in range(n_reuse):
            t0 = time.perf_counter()
            pc = p.getconn()
            pcur = pc.cursor()
            pcur.execute("SELECT 1")
            pcur.fetchone()
            pc.rollback()
            p.putconn(pc)
            pt.append(_ms(t0))
        out["pooled_getconn_select1_ms"] = _stats(pt)
        p.closeall()
    except Exception as e:  # noqa: BLE001
        note(e)
    out["errors_by_class"] = errs
    return out


if __name__ == "__main__":
    json.dump(main(), sys.stdout, indent=2)
    print()
