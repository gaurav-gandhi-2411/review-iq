"""Post eval results to a Slack webhook.

Usage:
    uv run python -m eval.slack_notify \
        --webhook $SLACK_WEBHOOK_URL \
        --run-url $RUN_URL
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

RESULTS_PATH = Path(__file__).parent / "results.json"


def _status_emoji(passed: bool) -> str:
    return "green_circle" if passed else "red_circle"


def build_payload(results_path: Path, run_url: str) -> dict:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    overall: float = data["overall_score"]
    passed: bool = data["passed"]
    per_language: dict = data.get("per_language", {})

    emoji = _status_emoji(passed)
    status_text = "PASS" if passed else "FAIL"
    header = f":{emoji}: Review IQ Eval — {status_text} {overall:.1%}"

    lang_lines = []
    for lang in sorted(per_language):
        info = per_language[lang]
        lang_emoji = _status_emoji(info["passed"])
        lang_lines.append(
            f":{lang_emoji}: *{lang}* {info['score']:.1%}"
            f" (gate {info['threshold']:.0%})"
        )

    fields = [
        {
            "type": "mrkdwn",
            "text": f"*Overall:* {overall:.1%} (gate {data['threshold']:.0%})",
        }
    ] + [{"type": "mrkdwn", "text": line} for line in lang_lines]

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header, "emoji": True},
        },
        {"type": "section", "fields": fields},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View run"},
                    "url": run_url,
                }
            ],
        },
    ]

    return {"blocks": blocks}


def post(webhook_url: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Slack returned HTTP {resp.status}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook", required=True, help="Slack incoming webhook URL")
    parser.add_argument("--run-url", default="", help="GitHub Actions run URL")
    args = parser.parse_args()

    if not RESULTS_PATH.exists():
        print(f"No results at {RESULTS_PATH} — run eval first", file=sys.stderr)
        return 1

    payload = build_payload(RESULTS_PATH, args.run_url)
    post(args.webhook, payload)
    print("Slack notification sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
