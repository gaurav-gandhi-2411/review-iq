"""Record real Judge.me list responses into a replayable, redacted fixture (spec section 9).

READ-ONLY and MANUAL: run by GG against a test store, never in CI. Token and shop come from the
environment (JUDGEME_API_TOKEN, JUDGEME_SHOP_DOMAIN); the token is never printed and never
written. Reviewer PII (the whole ``reviewer`` object, plus any email/phone-looking string value)
is redacted BEFORE the file is written, so the fixture is safe to commit after a read-through.

    $env:JUDGEME_API_TOKEN="..."; $env:JUDGEME_SHOP_DOMAIN="x.myshopify.com"
    python scripts/record_judgeme_fixture.py --pages 4 --per-page 100 --out tests/fixtures/judgeme/recorded_day1.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from app.core.ingestion.judgeme_client import JUDGEME_BASE_URL, validate_shop_domain

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def redact(value: Any) -> Any:
    """Recursively drop reviewer identity; mask email/phone-shaped strings anywhere."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k in {"reviewer", "email", "phone"} else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _PHONE.sub("[PHONE]", _EMAIL.sub("[EMAIL]", value))
    return value


async def record(shop: str, token: str, pages: int, per_page: int) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as http:
        for page in range(1, pages + 1):
            resp = await http.get(
                f"{JUDGEME_BASE_URL}/reviews",
                headers={"X-Api-Token": token},
                params={"shop_domain": shop, "per_page": per_page, "page": page},
            )
            try:
                body: Any = resp.json()
            except ValueError:
                body = {"_non_json_body": resp.text[:200]}
            keep = {k: v for k, v in resp.headers.items() if k.lower() in {"retry-after"}}
            entries.append(
                {
                    "page": page,
                    "per_page": per_page,
                    "status": resp.status_code,
                    "headers": keep,
                    "body": redact(body),
                }
            )
            if resp.status_code != 200 or not isinstance(body, dict) or not body.get("reviews"):
                break
    return {"shop_domain": "REDACTED", "requests": entries}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--per-page", type=int, default=100)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    token = os.environ.get("JUDGEME_API_TOKEN", "")
    shop = os.environ.get("JUDGEME_SHOP_DOMAIN", "")
    if not token or not shop:
        print("Set JUDGEME_API_TOKEN and JUDGEME_SHOP_DOMAIN in the environment.", file=sys.stderr)
        return 2
    data = asyncio.run(record(validate_shop_domain(shop), token, args.pages, args.per_page))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out} ({len(data['requests'])} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
