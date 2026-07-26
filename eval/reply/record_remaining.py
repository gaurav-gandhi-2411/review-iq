"""One-shot recorder for hi-en cassettes 013-015 (no retry loop).

Run ONCE on a fresh TPD window. If any fixture hits quota, exits immediately
with a non-zero code — do NOT rerun until the window has fully reset again.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ["EVAL_CASSETTE_MODE"] = "record"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # noqa: E402

from app.core.reply.engine import VernacularModelUnavailableError, draft_reply  # noqa: E402
from app.core.reply.schema import ReplyRequest, ReplyTone  # noqa: E402
from app.core.schemas import ReviewExtraction, Urgency  # noqa: E402
from groq import APIStatusError  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# All four fixtures that need large-model cassettes (012 cassette was never committed).
_TARGET_IDS = {
    "012_hien_packaging_bakwaas",
    "013_hien_delivery_bahut_late",
    "014_hien_refund_demand",
    "015_hien_mixed_complaint_brand",
}
ENC = sys.stdout.encoding or "utf-8"


def s(text: str) -> str:
    return text.encode(ENC, errors="replace").decode(ENC)


async def main() -> None:
    targets = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(FIXTURES_DIR.glob("*.json"))
        if p.stem in _TARGET_IDS
    ]
    if len(targets) != 4:
        print(f"ERROR: expected 3 target fixtures, found {len(targets)}", file=sys.stderr)
        sys.exit(1)

    print(f"Recording {len(targets)} hi-en cassettes (one-shot, no retry — all 4 or abort)...\n")

    for f in targets:
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
        except VernacularModelUnavailableError as exc:
            print(f"\n[QUOTA] {f['id']} — large model capped. Window not yet clear.")
            print(f"  Detail: {exc}")
            print("\nAbort. Do NOT commit cassettes.json. Wait for a fuller TPD window.")
            sys.exit(1)
        except (RuntimeError, APIStatusError) as exc:
            print(f"\n[ERROR] {f['id']} — unexpected error: {exc}")
            sys.exit(1)

        if draft.caveats:
            # Any caveat on a vernacular fixture means small model snuck through.
            print(f"\n[DEGRADED] {f['id']} — recorded on small model (policy violation).")
            print(f"  Caveats: {draft.caveats}")
            print("\nAbort. Do NOT commit cassettes.json.")
            sys.exit(1)

        preview = s(draft.reply_text[:120])
        print(f"[RECORDED] {f['id']} — {tin}in/{tout}out — model={draft.model_used}")
        print(f"  PREVIEW: {preview}")
        print()

    print("All 4 recorded on large model. Run eval/reply/runner.py to verify.")


if __name__ == "__main__":
    asyncio.run(main())
