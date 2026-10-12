"""In-memory SyncStore + Stager + Purger emulating the real pipeline's observable behaviour.

SYNTHETIC: ``drain()`` stands in for the ingest-tick worker. ``extractions`` mimics the
``UNIQUE (org_id, input_hash)`` + ``ON CONFLICT DO NOTHING`` contract of public.extractions, so
"what the dashboard would show" can be compared with "what the source publishes".
"""

from __future__ import annotations

from app.core.ingestion.judgeme_source import Mapped
from app.core.ingestion.judgeme_sync import StateRow


class MemPipeline:
    def __init__(self) -> None:
        self.state: dict[str, StateRow] = {}
        self.pending: list[Mapped] = []
        self.staged_log: list[list[Mapped]] = []  # one entry per stage() call
        self.extractions: dict[str, str] = {}  # input_hash -> text
        self.conflicts = 0
        self.crash_on_save_state = False
        self.writes = 0  # any stage/purge/state write, for the stateless test

    # SyncStore
    async def load_state(self) -> dict[str, StateRow]:
        return dict(self.state)

    async def save_state(self, rows: list[StateRow]) -> None:
        if self.crash_on_save_state:
            raise RuntimeError("simulated crash before state write")
        self.writes += 1
        for r in rows:
            self.state[r.review_id] = r

    # Stager
    async def stage(self, rows: list[Mapped]) -> str:
        self.writes += 1
        self.staged_log.append(list(rows))
        self.pending.extend(rows)
        return f"job-{len(self.staged_log)}"

    # Purger
    async def delete_extractions(self, input_hashes: list[str]) -> int:
        self.writes += 1
        return sum(self.extractions.pop(h, None) is not None for h in input_hashes)

    def drain(self) -> None:
        for m in self.pending:
            if m.input_hash in self.extractions:
                self.conflicts += 1
            else:
                self.extractions[m.input_hash] = m.text
        self.pending.clear()

    def staged_ids(self) -> list[str]:
        return [m.review_id for batch in self.staged_log for m in batch]
