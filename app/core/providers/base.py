from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Capability contract for all LLM providers used by review-iq.

    Structural typing — implementors do NOT inherit from this class.
    """

    trains_on_input: bool

    async def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int = 30,
    ) -> tuple[str, int, int]:
        """Return (raw_text, tokens_in, tokens_out)."""
        ...


def assert_privacy_safe(provider: Provider, context: str = "org-key path") -> None:
    """Raise RuntimeError if the provider trains on user input.

    Called before every org-key extraction to enforce the privacy guarantee
    in code, not just config.
    """
    if provider.trains_on_input:
        raise RuntimeError(
            f"Provider {type(provider).__name__!r} trains on input "
            f"and MUST NOT be used on the {context}."
        )
