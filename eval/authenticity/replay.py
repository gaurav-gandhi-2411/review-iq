"""Cassette replay/record plumbing for the authenticity eval.

Reuses app.core.providers.cassette (the same mechanism eval/score_held_out_corpus_v2.py uses)
pointed at a SEPARATE file, eval/cassettes/authenticity_cassettes.json, so it never reads or
writes cassettes.json / held_out_cassettes.json.

The cassette key is GroqProvider's own: sha256(model \\0 system_prompt \\0 user_prompt). Because the
key embeds the model string and the fully-rendered prompt (which embeds the review text and the
language-specific template), any prompt or model change makes every old entry a miss.

Replay is strict: `preflight` computes every key up front and raises CassetteMissError listing the
misses BEFORE any scoring, so a missing entry fails loudly instead of degrading to a per-row
"LLM error" (engine._call_authenticity_llm swallows exceptions into a neutral 0.5 score). In
replay mode GroqProvider never opens a network connection.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import app.core.providers.cassette as cassette_module
from app.core.language import detect_language
from app.core.prompts.authenticity import build_authenticity_prompt
from app.core.providers.groq import _make_cassette_key

AUTHENTICITY_CASSETTES_PATH = (
    Path(__file__).resolve().parents[1] / "cassettes" / "authenticity_cassettes.json"
)
CASSETTE_KEY_SCHEME = (
    "sha256(model NUL system_prompt NUL user_prompt) -- GroqProvider._make_cassette_key"
)

Mode = Literal["replay", "record"]


class CassetteMissError(RuntimeError):
    """Replay was requested but one or more cassette keys are absent."""


def authenticity_cassette_key(review_text: str, model: str) -> str:
    """Cassette key for the authenticity call on `review_text` with `model`.

    Mirrors app.core.authenticity.engine._call_authenticity_llm: language is auto-detected and
    the prompt is built by build_authenticity_prompt.
    """
    language = detect_language(review_text)
    system_prompt, user_prompt = build_authenticity_prompt(review_text, language)
    return _make_cassette_key(model, system_prompt, user_prompt)


def cassette_entry_count(path: Path = AUTHENTICITY_CASSETTES_PATH) -> int:
    """Number of entries in the cassette file (0 if the file is absent or empty)."""
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    return len(json.loads(text)) if text.strip() else 0


def configure_cassettes(mode: Mode, path: Path = AUTHENTICITY_CASSETTES_PATH) -> None:
    """Point the shared cassette layer at the authenticity file and set EVAL_CASSETTE_MODE."""
    if mode not in ("replay", "record"):
        raise ValueError(f"mode must be 'replay' or 'record', got {mode!r}")
    os.environ["EVAL_CASSETTE_MODE"] = mode
    cassette_module.CASSETTES_PATH = path


def preflight(
    texts: list[str], model: str, path: Path = AUTHENTICITY_CASSETTES_PATH
) -> dict[str, str]:
    """Return {text_index(str): key} after verifying every key is present in `path`.

    Raises CassetteMissError (listing up to 5 missing keys) if any is absent.
    """
    store: dict[str, object] = {}
    if path.exists() and path.read_text(encoding="utf-8").strip():
        store = json.loads(path.read_text(encoding="utf-8"))
    keys = {str(i): authenticity_cassette_key(t, model) for i, t in enumerate(texts)}
    missing = [(i, k) for i, k in keys.items() if k not in store]
    if missing:
        sample = ", ".join(f"item {i}: {k[:16]}" for i, k in missing[:5])
        raise CassetteMissError(
            f"{len(missing)}/{len(keys)} authenticity cassette keys missing from {path.name} "
            f"(model={model!r}); first misses: {sample}. Refusing to fall through to a live "
            "call. Re-record explicitly with --mode record."
        )
    return keys
