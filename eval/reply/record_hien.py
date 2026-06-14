"""One-shot: re-record cassettes for hi-en fixtures only (new prompt, no probe).

Handles Groq TPD 429 rate limits with automatic retry (sliding window — typically
clears in a few minutes for the free tier).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

os.environ["EVAL_CASSETTE_MODE"] = "record"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # noqa: E402

from groq import APIStatusError, RateLimitError  # noqa: E402

from app.core.reply.engine import draft_reply  # noqa: E402
from app.core.reply.schema import ReplyRequest, ReplyTone  # noqa: E402
from app.core.schemas import ReviewExtraction, Urgency  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_RETRY_WAIT_SECONDS = 420  # 7 min — gives sliding window time to release tokens


async def _record_one(f: dict, attempt: int = 1) -> None:
    ext = ReviewExtraction(
        product="unknown product",
        cons=f["pre_extracted_cons"],
        topics=f["pre_extracted_topics"],
        pros=[], feature_requests=[], competitor_mentions=[],
        language=f["language"], urgency=Urgency.low,
    )
    req = ReplyRequest(
        text=f["review_text"],
        tone=ReplyTone(f["tone"]),
        brand_name=f.get("brand_name"),
        signature=f.get("signature"),
        extraction=ext,
    )
    try:
        draft, tin, tout = await draft_reply(req)
    except (RuntimeError, APIStatusError, RateLimitError) as exc:
        if "rate_limit" in str(exc).lower() or "429" in str(exc):
            print(f"  [QUOTA] {f['id']} — sleeping {_RETRY_WAIT_SECONDS}s then retrying...")
            await asyncio.sleep(_RETRY_WAIT_SECONDS)
            await _record_one(f, attempt + 1)
            return
        raise
    enc = sys.stdout.encoding or "utf-8"
    preview = draft.reply_text[:120].encode(enc, errors="replace").decode(enc)
    print(f"[RECORDED] {f['id']} — {tin}in/{tout}out — model={draft.model_used}")
    print(f"  PREVIEW: {preview}")
    print()


async def main() -> None:
    hi_en_fixtures = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(FIXTURES_DIR.glob("*.json"))
        if json.loads(p.read_text(encoding="utf-8"))["language"] == "hi-en"
    ]
    print(f"Recording {len(hi_en_fixtures)} hi-en cassettes (with quota-retry)...\n")
    for f in hi_en_fixtures:
        await _record_one(f)
    print("Done. Run eval/reply/runner.py to verify.")


if __name__ == "__main__":
    asyncio.run(main())
