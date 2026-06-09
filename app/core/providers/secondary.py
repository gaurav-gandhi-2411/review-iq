from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class SecondaryProvider:
    """Config-gated no-train failover provider stub.

    trains_on_input MUST remain False — any secondary provider on the org-key
    path MUST be privacy-vetted before being wired here. Raise at provisioning
    time if a train-on-input provider is accidentally configured.
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
        if not self.is_configured:
            raise RuntimeError(
                "SecondaryProvider is not configured. "
                "Set SECONDARY_PROVIDER_API_KEY and SECONDARY_PROVIDER_MODEL."
            )
        raise NotImplementedError(
            "SecondaryProvider is a stub. Wire a real no-train provider here."
        )
