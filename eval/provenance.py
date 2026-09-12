"""Shared run-provenance helpers for eval JSON outputs (git SHA, timestamp)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def get_git_sha() -> str | None:
    """Best-effort current commit SHA, or None if git is unavailable.

    Never raises — an eval run must not fail just because git isn't on PATH or the
    working tree isn't a git repo (e.g. some minimal execution contexts / Docker builds).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 with a 'Z' suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
