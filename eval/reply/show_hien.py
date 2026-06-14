"""Print full hi-en reply texts for quality review."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("EVAL_CASSETTE_MODE", "replay")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.reply.engine import VernacularModelUnavailableError, draft_reply  # noqa: E402
from app.core.reply.schema import ReplyRequest, ReplyTone  # noqa: E402
from app.core.schemas import ReviewExtraction, Urgency  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ENC = sys.stdout.encoding or "utf-8"


def s(text: str) -> str:
    return text.encode(ENC, errors="replace").decode(ENC)


async def main() -> None:
    hi_en_fixtures = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(FIXTURES_DIR.glob("*.json"))
        if json.loads(p.read_text(encoding="utf-8"))["language"] == "hi-en"
    ]
    for f in hi_en_fixtures:
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
            draft, _, _ = await draft_reply(req)
        except VernacularModelUnavailableError:
            print(f"=== {f['id']} | {f['tone']} | [LARGE MODEL UNAVAILABLE — re-record] ===")
            print()
            continue
        model_tag = f"[{draft.model_used}]"
        caveat_tag = " [DEGRADED]" if any("degraded" in c for c in draft.caveats) else ""
        print(f"=== {f['id']} | {f['tone']} | {model_tag}{caveat_tag} ===")
        print(f"REVIEW : {s(f['review_text'])}")
        print(f"REPLY  : {s(draft.reply_text)}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
