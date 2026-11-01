"""S17 X3 (ADR 0035): the Gemini fallback is retired; pin that it stays gone."""

from __future__ import annotations

import app.core.llm as llm_module
import app.core.router as router_module
from app.core.config import Settings


def test_no_gemini_code_path_or_setting_remains_in_app():
    assert not hasattr(llm_module, "_call_gemini")
    assert not [f for f in Settings.model_fields if "gemini" in f.lower()]


def test_extract_and_route_take_no_gemini_flag():
    import inspect

    assert "allow_gemini_fallback" not in inspect.signature(llm_module.extract_with_llm).parameters
    assert (
        "allow_gemini_fallback" not in inspect.signature(router_module.route_extraction).parameters
    )


def test_no_google_sdk_or_endpoint_referenced_by_the_llm_path():
    # The only providers left are Groq and the OpenRouter secondary.
    src = inspect_source()
    assert "genai" not in src and "generativelanguage" not in src


def inspect_source() -> str:
    import inspect

    return inspect.getsource(llm_module) + inspect.getsource(router_module)
