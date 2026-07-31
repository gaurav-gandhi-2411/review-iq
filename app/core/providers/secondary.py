from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)

_OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


class SecondaryProvider:
    """OpenRouter-backed failover provider for the org-key (paying-customer) path.

    trains_on_input=False -- genuinely verified, not assumed. Wave 1 Section F
    reliability gap: this class was previously a stub (`NotImplementedError` on
    every call), leaving the org-key path with zero real failover behind Groq.

    Privacy verification (2026-07-31, cite: https://openrouter.ai/docs/guides/features/zdr,
    live-checked against https://openrouter.ai/api/v1/endpoints/zdr):

    OpenRouter is an aggregator -- its own "we don't train" policy does NOT bind every
    upstream model/provider it routes to (some upstream providers DO train on inputs
    by default). The mechanism used here is OpenRouter's own documented per-request
    Zero Data Retention enforcement, NOT a blanket trust of "OpenRouter says so":

      Every request sent by this class includes `"provider": {"zdr": true}`. Per
      OpenRouter's docs, this restricts routing to ONLY endpoints on their live
      Zero-Data-Retention allowlist (GET /api/v1/endpoints/zdr, "automatically
      updated when there are changes to a provider's data policy"), and the docs
      state explicitly: "Providers that do not retain your data are also unable to
      train on your data." If no ZDR-flagged endpoint exists for the configured
      model, OpenRouter returns an error (verified live: HTTP 404 "No endpoints
      found for <model>") rather than silently routing to a non-ZDR endpoint --
      i.e. the enforcement fails closed, not open.

    This was live-verified (not just read from docs) on 2026-07-31: 3 real chat-
    completion calls against `meta-llama/llama-3.3-70b-instruct` with
    `provider.zdr=true` each returned a `"provider"` field (DeepInfra, AkashML,
    DeepInfra) that was cross-checked against that same model's live entries in
    GET /api/v1/endpoints/zdr -- all 3 matched. `SECONDARY_PROVIDER_MODEL` is
    recommended to be pinned to `meta-llama/llama-3.3-70b-instruct` (same nominal
    family as this repo's Groq large tier, and it has ZDR-flagged endpoints from
    11 different upstream providers including Groq itself, so failing over from
    Groq-direct to Groq-via-OpenRouter's ZDR endpoint is possible). The zdr=true
    request flag is sent unconditionally regardless of which model an operator
    configures -- this is enforced in code, not left to config discipline alone.

    Per-upstream verification, not just an OpenRouter-API-tier claim (Wave 1 S0/P1
    remediation, 2026-07-31): ZDR status is determined by OpenRouter per PROVIDER,
    through direct engagement with each one (their own docs: "OpenRouter works with
    providers to understand each of their data policies... If OpenRouter is not able
    to establish or ascertain a clear policy for a provider or endpoint, we take a
    conservative stance and assume that the endpoint both retains and trains on
    data") -- not a single blanket flag covering everything they route to. Live-
    enumerated the exact set for `meta-llama/llama-3.3-70b-instruct`: 12 providers
    currently serve this model (GET /api/v1/models/{model}/endpoints), of which 11
    are ZDR-confirmed for it (GET /api/v1/endpoints/zdr filtered to this model_id) --
    Cloudflare is the one NOT confirmed. The `zdr:true` flag above is what excludes
    Cloudflare specifically; this class never needs a hardcoded exclusion list
    because the flag's server-side enforcement already does it, and does so
    correctly regardless of which of the 12 providers OpenRouter happens to route to
    on a given request, or how that set changes over time (re-run both endpoints
    above to re-verify the current set -- see legal/sub-processors.md for the same
    enumeration written up for the DPA).
    """

    trains_on_input: bool = False

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key
        self._model = model

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._model)

    async def complete(
        self,
        user_prompt: str,
        *,
        system_prompt: str,
        retry: bool = False,
        timeout: int | None = None,
    ) -> tuple[str, int, int]:
        """Call OpenRouter chat completions, restricted to Zero-Data-Retention endpoints.

        Returns (raw_text, tokens_in, tokens_out) -- same contract as GroqProvider.complete.
        Raises RuntimeError if unconfigured, httpx.HTTPError on any network/HTTP failure
        (including the fail-closed 404 when no ZDR endpoint exists for the model).
        """
        if not self.is_configured:
            raise RuntimeError(
                "SecondaryProvider is not configured. "
                "Set SECONDARY_PROVIDER_API_KEY and SECONDARY_PROVIDER_MODEL."
            )

        from app.core.providers.groq import _RETRY_SUFFIX

        prompt = user_prompt + (_RETRY_SUFFIX if retry else "")
        effective_timeout = float(timeout) if timeout is not None else 30.0

        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            response = await client.post(
                _OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.0,
                    # Fail-closed privacy enforcement -- see class docstring. Restricts
                    # routing to OpenRouter's live Zero-Data-Retention endpoint allowlist;
                    # a model with no ZDR-flagged endpoint errors rather than silently
                    # falling back to a training-eligible one.
                    "provider": {"zdr": True},
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPError:
                log.warning(
                    "secondary.openrouter_http_error",
                    status_code=response.status_code,
                    model=self._model,
                )
                raise
            data = response.json()

        raw = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0) or 0)
        tokens_out = int(usage.get("completion_tokens", 0) or 0)
        log.info(
            "secondary.openrouter_completed",
            model=self._model,
            provider=data.get("provider"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        return raw, tokens_in, tokens_out
