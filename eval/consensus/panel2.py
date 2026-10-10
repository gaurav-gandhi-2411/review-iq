"""Panel 2: a vendor-disjoint silver adjudication of the 60 unscored held-out field-pairs (S17 G6).

Pre-registration: docs/specs/s17-panel2-adjudication.md (read it first; every threshold below is
fixed there). Silver labels produced here are "LLM-consensus silver (panel 2), disjoint from
production; NOT ground truth".

Modes:
  record  -- use the cassette where an entry exists, otherwise make ONE live OpenRouter call
             (ZDR-only), store the response in the cassette immediately (resumable), cost-capped.
  replay  -- zero network; every call must be in the cassette; rewrites the result artifacts.
  report  -- like replay but only prints a summary and writes nothing.

Phases: `calibration` (candidates vs control sets), `main` (T + V + self-consistency, active
panel from the calibration artifact), `all`.

    uv run python -m eval.consensus.panel2 --mode record --phase calibration
    uv run python -m eval.consensus.panel2 --mode record --phase main
    uv run python -m eval.consensus.panel2 --mode replay --phase all
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.consensus import calibration, panel, voting  # noqa: E402

RESULTS_DIR = ROOT / "eval" / "consensus" / "results"
CASSETTE_PATH = ROOT / "eval" / "cassettes" / "panel2_cassettes.json"
CALIBRATION_PATH = RESULTS_DIR / "panel2_calibration.json"
VOTES_PATH = RESULTS_DIR / "panel2_votes.jsonl"
SILVER_PATH = RESULTS_DIR / "panel2_silver.json"
HINGLISH_CONTROL_PATH = Path(__file__).resolve().parent / "control_set_hinglish.json"
HELD_OUT_ARTIFACT = ROOT / "eval" / "results" / "held_out_scoring_v2.json"

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_ZDR_URL = "https://openrouter.ai/api/v1/endpoints/zdr"

SILVER_LABEL = "LLM-consensus silver (panel 2), disjoint from production; NOT ground truth"

# Pre-registered roster (spec: Design). `family` is derived by panel.model_family at runtime.
CANDIDATES: tuple[dict[str, str], ...] = (
    {"id": "deepseek/deepseek-v4-flash", "provider": "openrouter", "owner": "DeepSeek"},
    {"id": "nvidia/nemotron-3-super-120b-a12b", "provider": "openrouter", "owner": "NVIDIA"},
    {"id": "mistralai/mistral-small-2603", "provider": "openrouter", "owner": "Mistral AI"},
    {"id": "z-ai/glm-4.7-flash", "provider": "openrouter", "owner": "Zhipu AI"},
    {
        "id": "thinkingmachines/inkling-small",
        "provider": "openrouter",
        "owner": "Thinking Machines",
    },
)
# Panel-1 vendors: forbidden for independence, in addition to every production vendor.
PANEL1_FORBIDDEN_FAMILIES = {
    "alibaba": "panel-1 vendor (qwen3.6-27b, qwen3.8-27b)",
    "google": "panel-1 vendor (gemini-3.5-flash-lite)",
}

HEADLINE_FIELDS: tuple[str, ...] = (
    "product",
    "buy_again",
    "sentiment",
    "topics",
    "competitor_mentions",
    "pros",
    "cons",
    "stars_inferred",
)
# Amendment A1 (post-hoc, see the spec): the v2 gate counts only these control-set fields.
SCOPED_FIELDS: tuple[str, ...] = (*HEADLINE_FIELDS, "stars")
LIST_FIELDS = ("topics", "competitor_mentions", "pros", "cons")
CALIBRATION_MAX_MISSES = calibration.MAX_ALLOWED_MISSES
BUDGET_HARD_CAP_USD = 2.00
N_VALIDATION = 20
SEED = 42
SELF_CONSISTENCY_N = 10
JACCARD = voting.JACCARD_THRESHOLD
MAX_TOKENS = 2000
RETRY_DELAYS = (2.0, 4.0, 8.0)
PACE_SECONDS = 0.5
REQUEST_TIMEOUT = 120.0
# Per-model extra request body fields (spec: reasoning disabled where the model accepts it).
DEFAULT_EXTRA_BODY: dict[str, Any] = {"reasoning": {"enabled": False}}
EXTRA_BODY_OVERRIDES: dict[str, dict[str, Any]] = {}

PROMPT_HASH = hashlib.sha256(
    (panel.JUDGE_SYSTEM_PROMPT + "\n" + panel.JUDGE_USER_TEMPLATE).encode()
).hexdigest()[:12]

PANEL1_SILVER_NOTE = "panel-1 silver = the stored held-out fixture gold on a resolved pair"


class BudgetExceededError(RuntimeError):
    pass


class MissingCassetteError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------
# Cassette + cost ledger
# ---------------------------------------------------------------------------------------------


def cassette_key(model: str, text: str, rep: int = 0) -> str:
    blob = f"panel2|{PROMPT_HASH}|{model}|rep{rep}|{text}"
    return hashlib.sha256(blob.encode()).hexdigest()


class Cassette:
    def __init__(self, path: Path = CASSETTE_PATH) -> None:
        self.path = path
        self.store: dict[str, dict[str, Any]] = {}
        if path.exists() and path.read_text(encoding="utf-8").strip():
            self.store = json.loads(path.read_text(encoding="utf-8"))

    def get(self, key: str) -> dict[str, Any] | None:
        return self.store.get(key)

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self.store[key] = entry
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.store, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


@dataclass
class Ledger:
    cap_usd: float = BUDGET_HARD_CAP_USD
    live_cost: dict[str, float] = field(default_factory=dict)
    cost: dict[str, float] = field(default_factory=dict)
    tokens_in: dict[str, int] = field(default_factory=dict)
    tokens_out: dict[str, int] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)

    @property
    def live_total(self) -> float:
        return sum(self.live_cost.values())

    def add(self, vote: dict[str, Any], *, live: bool) -> None:
        m = vote["model"]
        self.cost[m] = self.cost.get(m, 0.0) + float(vote.get("cost_usd") or 0.0)
        self.tokens_in[m] = self.tokens_in.get(m, 0) + int(vote.get("tokens_in") or 0)
        self.tokens_out[m] = self.tokens_out.get(m, 0) + int(vote.get("tokens_out") or 0)
        self.calls[m] = self.calls.get(m, 0) + 1
        if live:
            self.live_cost[m] = self.live_cost.get(m, 0.0) + float(vote.get("cost_usd") or 0.0)

    def check(self) -> None:
        # Cap is on cumulative spend recorded in the cassette (all runs), not just this process.
        if self.live_total >= self.cap_usd:
            raise BudgetExceededError(f"live spend {self.live_total:.4f} USD >= cap {self.cap_usd}")

    def summary(self) -> dict[str, Any]:
        return {
            "total_cost_usd": round(sum(self.cost.values()), 6),
            "total_tokens_in": sum(self.tokens_in.values()),
            "total_tokens_out": sum(self.tokens_out.values()),
            "total_calls": sum(self.calls.values()),
            "per_model": {
                m: {
                    "calls": self.calls[m],
                    "cost_usd": round(self.cost.get(m, 0.0), 6),
                    "tokens_in": self.tokens_in.get(m, 0),
                    "tokens_out": self.tokens_out.get(m, 0),
                }
                for m in sorted(self.calls)
            },
        }


# ---------------------------------------------------------------------------------------------
# Parsing + live call
# ---------------------------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


def parse_raw(raw: str) -> dict[str, Any] | None:
    """Parse a judge response; tolerant of <think> blocks and prose around one JSON object.

    Leniency applies to FORMAT only (spec Deviations, D1); it never edits field values.
    """
    if not raw:
        return None
    parsed = panel.parse_judge_response(raw)
    if parsed is None:
        cleaned = _THINK_RE.sub("", raw)
        lo, hi = cleaned.find("{"), cleaned.rfind("}")
        if lo != -1 and hi > lo:
            parsed = panel.parse_judge_response(cleaned[lo : hi + 1])
    return parsed.model_dump() if parsed else None


def load_api_key() -> str:
    """OPENROUTER_API_KEY from the environment, else from a .env file (never printed)."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    env_file = os.environ.get("PANEL2_ENV_FILE", "")
    if env_file and Path(env_file).exists():
        from dotenv import dotenv_values

        vals = dotenv_values(env_file)
        key = vals.get("OPENROUTER_API_KEY") or ""
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not available: set it in the environment, or point "
            "PANEL2_ENV_FILE at a .env that defines it."
        )
    return key


async def fetch_zdr_models(http: Any) -> set[str]:
    resp = await http.get(OPENROUTER_ZDR_URL, timeout=60.0)
    resp.raise_for_status()
    return {row["model_id"] for row in resp.json()["data"]}


async def _live_call(http: Any, api_key: str, model: str, text: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": panel.JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": panel.build_user_prompt(text)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
        "usage": {"include": True},
        # ZDR-only routing; fails closed (error) when the model has no ZDR endpoint.
        "provider": {"zdr": True, "allow_fallbacks": True},
        **EXTRA_BODY_OVERRIDES.get(model, DEFAULT_EXTRA_BODY),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_err = ""
    status = 0
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            resp = await http.post(
                OPENROUTER_CHAT_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT
            )
            status = resp.status_code
        except Exception as exc:  # noqa: BLE001 -- transport failure: retry like a 5xx
            last_err, status = f"transport:{type(exc).__name__}", 0
        else:
            if status == 200:
                data = resp.json()
                usage = data.get("usage") or {}
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                return {
                    "raw": content,
                    "tokens_in": int(usage.get("prompt_tokens", 0) or 0),
                    "tokens_out": int(usage.get("completion_tokens", 0) or 0),
                    "cost_usd": float(usage.get("cost", 0.0) or 0.0),
                    "provider": data.get("provider"),
                    "status": 200,
                }
            last_err = resp.text[:300]
            if status not in (429, 500, 502, 503, 504):
                return {
                    "raw": "",
                    "error": last_err,
                    "status": status,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_usd": 0.0,
                }
        if attempt < len(RETRY_DELAYS):
            await asyncio.sleep(RETRY_DELAYS[attempt])
    return {
        "raw": "",
        "error": last_err,
        "status": status,
        "retryable": True,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
    }


@dataclass
class Runner:
    mode: str
    cassette: Cassette
    ledger: Ledger
    http: Any = None
    api_key: str = ""
    votes: list[dict[str, Any]] = field(default_factory=list)

    async def judge(
        self, phase: str, unit_id: str, model: str, text: str, rep: int = 0
    ) -> dict[str, Any]:
        key = cassette_key(model, text, rep)
        entry = self.cassette.get(key)
        live = False
        reuse = entry is not None and (self.mode != "record" or not entry.get("retryable", False))
        if not reuse:
            if self.mode != "record":
                raise MissingCassetteError(f"{phase}/{unit_id}/{model}/rep{rep}: not in cassette")
            self.ledger.check()
            entry = await _live_call(self.http, self.api_key, model, text)
            entry["model"] = model
            self.cassette.put(key, entry)
            live = True
        assert entry is not None
        parsed = parse_raw(entry.get("raw", "")) if entry.get("status", 200) == 200 else None
        vote = {
            "phase": phase,
            "unit_id": unit_id,
            "model": model,
            "rep": rep,
            "parsed": parsed,
            "raw": entry.get("raw", ""),
            "error": entry.get("error"),
            "status": entry.get("status", 200),
            "provider": entry.get("provider"),
            "tokens_in": entry.get("tokens_in", 0),
            "tokens_out": entry.get("tokens_out", 0),
            "cost_usd": entry.get("cost_usd", 0.0),
            "label": SILVER_LABEL,
        }
        self.ledger.add(vote, live=live)
        self.votes.append(vote)
        return vote
