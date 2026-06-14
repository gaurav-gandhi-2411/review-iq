"""Cassette record/replay layer for GroqProvider.

Controls the EVAL_CASSETTE_MODE env var:
  - unset / "" / "live"  -> production live call (default, unchanged)
  - "record"             -> make the live call, then persist the response
  - "replay"             -> return stored response with ZERO network calls

Cassette store: eval/cassettes/cassettes.json
  { "<sha256-key>": {"raw": "...", "tokens_in": N, "tokens_out": N} }

This file is imported by GroqProvider and the eval harness. It must NOT
import anything from app.core.config to avoid circular imports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# Store path — relative to project root.  Override in tests via monkeypatch.
# ---------------------------------------------------------------------------

_DEFAULT_CASSETTES_PATH = (
    Path(__file__).parent.parent.parent.parent / "eval" / "cassettes" / "cassettes.json"
)

# Module-level mutable so tests can swap it out with a temp path.
CASSETTES_PATH: Path = _DEFAULT_CASSETTES_PATH


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class CassetteEntry(TypedDict):
    raw: str
    tokens_in: int
    tokens_out: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cassette_mode() -> str:
    """Return the normalised cassette mode from the env var.

    Possible values: "live" | "record" | "replay".
    Anything unset / empty / "live" maps to "live" (production default).
    """
    raw = os.environ.get("EVAL_CASSETTE_MODE", "").strip().lower()
    if raw in ("", "live"):
        return "live"
    if raw in ("record", "replay"):
        return raw
    raise ValueError(
        f"Unknown EVAL_CASSETTE_MODE={raw!r}. Valid values: '' / 'live' / 'record' / 'replay'."
    )


def _load_store() -> dict[str, CassetteEntry]:
    """Load the cassette JSON file, returning an empty dict if it doesn't exist."""
    if not CASSETTES_PATH.exists():
        return {}
    text = CASSETTES_PATH.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    data: dict[str, CassetteEntry] = json.loads(text)
    return data


def _save_store(store: dict[str, CassetteEntry]) -> None:
    """Persist the cassette store to disk (pretty-printed for easy diffing)."""
    CASSETTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASSETTES_PATH.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")


def record(key: str, raw: str, tokens_in: int, tokens_out: int) -> None:
    """Append (or overwrite) a cassette entry and persist the store.

    Thread-safety: the eval runner is sequential, so a simple load-mutate-save
    cycle is safe. This is intentionally not atomic for simplicity.
    """
    store = _load_store()
    store[key] = {"raw": raw, "tokens_in": tokens_in, "tokens_out": tokens_out}
    _save_store(store)


def replay(key: str) -> tuple[str, int, int] | None:
    """Return (raw, tokens_in, tokens_out) for *key*, or None if not found."""
    store = _load_store()
    entry = store.get(key)
    if entry is None:
        return None
    return entry["raw"], entry["tokens_in"], entry["tokens_out"]
