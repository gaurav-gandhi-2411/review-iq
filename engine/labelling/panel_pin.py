"""Amendment 16: a four-judge panel run is accepted only if its status file RECORDS the pinned Ollama build and the judge model digests.

    python -m engine.labelling.panel_pin D:/ml-cache/s30/panel/out/status.json [<session 2 status.json> ...]

Amendment 15's panel ran on an unpinned build and was reported as a deviation. Amendment 16 does not allow that: a status file that lacks
the pinned archive digest, the `ollama --version` line or a digest for each of the four judge models is REJECTED (exit 1) and the run is
discarded and repeated. The kernel (`engine/kaggle/kernel_scale.py`) refuses to start Ollama unless the downloaded archive matches the pinned
digest, and writes these fields at the end of the run. A run resumed over several Kaggle sessions passes every session's status
file: each must pass on its own AND all must record the SAME model digests (a tag such as `qwen3:8b` can move between sessions).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PINNED_SHA256 = "c164ab8b1b28e2a247c29719b8e404c423c5b83ae64c3ab4e59d50c7ff7fc5e0"  # ollama v0.40.6 linux-amd64 archive (Amendment 13)
PINNED_VERSION = "0.40.6"
JUDGES = ("llama3.1:8b", "qwen3:8b", "gemma2:9b", "mistral:7b")


def check(status: dict) -> list[str]:
    problems: list[str] = []
    if status.get("ollama_sha256") != PINNED_SHA256:
        problems.append(f"ollama_sha256 is {status.get('ollama_sha256')!r}, not the pinned digest")
    if PINNED_VERSION not in str(status.get("ollama_version", "")):
        problems.append(
            f"ollama_version {status.get('ollama_version')!r} does not name {PINNED_VERSION}"
        )
    digests = status.get("model_digests") or {}
    for j in JUDGES:
        d = digests.get(j)
        if not (isinstance(d, str) and len(d) >= 12):
            problems.append(f"no recorded digest for judge model {j}")
    if status.get("budget_hit") is None:
        problems.append("status has no budget_hit field (not a panel status file)")
    return problems


def check_sessions(statuses: list[dict]) -> list[str]:
    problems = [f"session {k + 1}: {p}" for k, s in enumerate(statuses) for p in check(s)]
    first = statuses[0].get("model_digests") or {}
    for k, s in enumerate(statuses[1:], start=2):
        for j in JUDGES:
            if (s.get("model_digests") or {}).get(j) != first.get(j):
                problems.append(f"session {k}: judge model {j} digest differs from session 1")
    return problems


def main() -> int:
    statuses = [json.loads(Path(a).read_text(encoding="utf-8")) for a in sys.argv[1:]]
    problems = check(statuses[0]) if len(statuses) == 1 else check_sessions(statuses)
    for p in problems:
        print("REJECTED:", p)
    print("panel pin: " + ("REJECTED" if problems else "recorded and correct"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
