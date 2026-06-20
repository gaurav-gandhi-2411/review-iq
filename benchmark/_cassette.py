"""Benchmark-specific cassette — separate from eval/cassettes, path-parameterized."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypedDict


class _Entry(TypedDict):
    raw: str
    tokens_in: int
    tokens_out: int


def make_key(model: str, system_prompt: str, user_prompt: str) -> str:
    payload = f"{model}\x00{system_prompt}\x00{user_prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BenchCassette:
    """File-backed cassette for a single benchmark system's LLM calls."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict[str, _Entry]:
        if not self._path.exists():
            return {}
        text = self._path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}

    def _save(self, store: dict[str, _Entry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")

    def get(self, key: str) -> tuple[str, int, int] | None:
        entry = self._load().get(key)
        if entry is None:
            return None
        return entry["raw"], entry["tokens_in"], entry["tokens_out"]

    def put(self, key: str, raw: str, tokens_in: int, tokens_out: int) -> None:
        store = self._load()
        store[key] = {"raw": raw, "tokens_in": tokens_in, "tokens_out": tokens_out}
        self._save(store)

    def size(self) -> int:
        return len(self._load())
