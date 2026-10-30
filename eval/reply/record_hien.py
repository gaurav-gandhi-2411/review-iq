"""One-shot: re-record cassettes for hi-en fixtures only (new prompt, no probe).

Stops on the first Groq quota 429 (exit 2) and persists the TPD observation so eval.quota_guard
preflight refuses that model for 24h. It never sleeps and retries: the TPD window is rolling and
can be drained by other consumers of the key.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ["EVAL_CASSETTE_MODE"] = "record"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.reply.engine import VernacularModelUnavailableError, draft_reply  # noqa: E402
from app.core.reply.schema import ReplyRequest, ReplyTone  # noqa: E402
from app.core.schemas import ReviewExtraction, Urgency  # noqa: E402
from groq import APIStatusError, RateLimitError  # noqa: E402

from eval.quota_guard import record_tpd_observation  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def _record_one(f: dict) -> None:
    ext = ReviewExtraction(
        product="unknown product",
        cons=f["pre_extracted_cons"],
        topics=f["pre_extracted_topics"],
        pros=[],
        feature_requests=[],
        competitor_mentions=[],
        language=f["language"],
        urgency=Urgency.low,
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
    except (VernacularModelUnavailableError, RuntimeError, APIStatusError, RateLimitError) as exc:
        # 2026-10-08: sleeping 7 min and retrying a TPD 429 just re-hits a rolling window that can
        # be ~99.7% used by other consumers. Persist the observation (preflight then refuses the
        # model for 24h) and STOP; the operator reruns when the window has genuinely freed up.
        text = str(exc)
        if (
            "rate_limit" in text.lower()
            or "429" in text
            or isinstance(exc, VernacularModelUnavailableError)
        ):
            obs = record_tpd_observation(get_settings().groq_model_large, text)
            print(f"  [QUOTA] {f['id']} -- quota 429; STOPPING (no retry). observation={obs}")
            raise SystemExit(2) from exc
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
    print(f"Recording {len(hi_en_fixtures)} hi-en cassettes (stops on first quota 429)...\n")
    for f in hi_en_fixtures:
        await _record_one(f)
    print("Done. Run eval/reply/runner.py to verify.")


if __name__ == "__main__":
    asyncio.run(main())
