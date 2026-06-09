from __future__ import annotations

import structlog
from groq import AsyncGroq

log = structlog.get_logger(__name__)

_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your previous response could not be parsed. "
    "Return ONLY the JSON object with no markdown, no code blocks, no commentary."
)


class GroqProvider:
    """Groq LLM provider — structurally satisfies the Provider Protocol.

    trains_on_input=False: Groq does not train on API inputs (vetted).
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
        """
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
        return raw, tokens_in, tokens_out
