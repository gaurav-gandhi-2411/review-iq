from __future__ import annotations

import hashlib

import structlog
from groq import AsyncGroq

from app.core.providers.cassette import cassette_mode, record, replay

log = structlog.get_logger(__name__)

_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your previous response could not be parsed. "
    "Return ONLY the JSON object with no markdown, no code blocks, no commentary."
)


def _make_cassette_key(model: str, system_prompt: str, user_prompt: str) -> str:
    """Deterministic SHA-256 key for the (model, system_prompt, user_prompt) triple.

    Uses a null-byte separator that cannot appear in normal prompt text,
    making collisions between different field combinations impossible.
    """
    payload = f"{model}\x00{system_prompt}\x00{user_prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GroqProvider:
    """Groq LLM provider — structurally satisfies the Provider Protocol.

    trains_on_input=False: Groq does not train on API inputs (vetted).

    Cassette gate (controlled by EVAL_CASSETTE_MODE env var):
      - unset / "live"  -> live network call (production default, unchanged)
      - "record"        -> live call + persist response under the cassette key
      - "replay"        -> return stored response, ZERO network calls;
                          raises RuntimeError if key is missing
    """

    trains_on_input: bool = False

    def __init__(self, model: str, api_key: str, timeout: int = 30) -> None:
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        """Call Groq chat completions with JSON mode.

        Returns (raw_text, tokens_in, tokens_out).
        Raises groq.APIError / groq.APIStatusError on API-level failures.

        In replay mode the cassette is returned directly without any network
        call. In record mode the live call is made and the response is saved
        before returning. In live mode (default) behaviour is unchanged.
        """
        # Use the base user_prompt (without retry suffix) for the cassette key
        # so that retried calls resolve to the same cassette entry.
        key = _make_cassette_key(self._model, system_prompt, user_prompt)
        mode = cassette_mode()

        if mode == "replay":
            result = replay(key)
            if result is None:
                raise RuntimeError(
                    f"No cassette for key {key!r}; re-record with EVAL_CASSETTE_MODE=record"
                )
            raw, tokens_in, tokens_out = result
            log.debug(
                "provider.cassette_replay",
                model=self._model,
                key=key[:16],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
            return raw, tokens_in, tokens_out

        # --- live / record path (unchanged behaviour) ----------------------
        client = AsyncGroq(api_key=self._api_key)
        prompt = user_prompt + (_RETRY_SUFFIX if retry else "")
        effective_timeout = timeout if timeout is not None else self._timeout

        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            timeout=effective_timeout,
        )
        raw = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        if usage:
            tokens_in = getattr(usage, "prompt_tokens", 0) or 0
            tokens_out = getattr(usage, "completion_tokens", 0) or 0
        else:
            log.warning("provider.missing_token_counts", provider="groq", model=self._model)
            tokens_in, tokens_out = 0, 0

        if mode == "record":
            record(key, raw, tokens_in, tokens_out)
            log.debug(
                "provider.cassette_recorded",
                model=self._model,
                key=key[:16],
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        return raw, tokens_in, tokens_out
