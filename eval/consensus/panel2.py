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

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.agreement import fleiss_kappa, krippendorff_alpha  # noqa: E402
from eval.consensus import calibration, panel, voting  # noqa: E402
from eval.free_text_scoring import (  # noqa: E402
    canonical_competitor,
    canonical_product,
    canonical_topic,
)

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


# ---------------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------------


def load_control_items() -> list[dict[str, Any]]:
    base = calibration.load_control_set()
    hinglish = json.loads(HINGLISH_CONTROL_PATH.read_text(encoding="utf-8"))
    return [*base, *hinglish]


def count_checks(item: dict[str, Any]) -> int:
    return len(item.get("expected", {})) + len(item.get("expected_list_contains", {}))


def select_active_panel(
    results: dict[str, dict[str, Any]], max_misses: int = CALIBRATION_MAX_MISSES
) -> list[str]:
    """3 passing candidates with the fewest misses from 3 distinct vendors (spec: Design).

    Tie-break: lower mean cost per call, then id. Returns [] when fewer than 3 distinct vendors
    pass (the caller must STOP, not relax the gate).
    """
    passing = [m for m, r in results.items() if r["misses"] <= max_misses]
    ranked = sorted(
        passing, key=lambda m: (results[m]["misses"], results[m]["mean_cost_per_call"], m)
    )
    chosen: list[str] = []
    families: set[str] = set()
    for m in ranked:
        fam = panel.model_family(m)
        if fam not in families:
            chosen.append(m)
            families.add(fam)
        if len(chosen) == 3:
            return chosen
    return []


async def run_calibration(runner: Runner, zdr_models: set[str] | None) -> dict[str, Any]:
    items = load_control_items()
    total_checks = sum(count_checks(i) for i in items)
    candidates = [dict(c) for c in CANDIDATES]
    dropped: list[dict[str, Any]] = []
    live_candidates: list[dict[str, str]] = []
    for c in candidates:
        c["family"] = panel.model_family(c["id"])
        if zdr_models is not None and c["id"] not in zdr_models:
            dropped.append({"model_id": c["id"], "reason": "no ZDR endpoint in /endpoints/zdr"})
        else:
            live_candidates.append(c)
    # Independence guard before any call.
    panel.assert_no_self_judging(
        tuple(live_candidates), extra_forbidden_families=PANEL1_FORBIDDEN_FAMILIES
    )

    misses: dict[str, list[dict[str, Any]]] = {c["id"]: [] for c in live_candidates}
    parse_fail: dict[str, int] = {c["id"]: 0 for c in live_candidates}
    call_errors: dict[str, list[str]] = {c["id"]: [] for c in live_candidates}
    for item in items:
        votes = await asyncio.gather(
            *[
                runner.judge("calibration", item["id"], c["id"], item["text"])
                for c in live_candidates
            ]
        )
        for c, v in zip(live_candidates, votes, strict=True):
            if v["parsed"] is None:
                parse_fail[c["id"]] += 1
                if v.get("error"):
                    call_errors[c["id"]].append(f"{item['id']}: {str(v['error'])[:120]}")
            missed = calibration.check_item_against_expected(item, v["parsed"])
            if missed:
                misses[c["id"]].append({"item_id": item["id"], "fields": missed})
        await asyncio.sleep(PACE_SECONDS if runner.mode == "record" else 0)

    results: dict[str, dict[str, Any]] = {}
    for c in live_candidates:
        m = c["id"]
        n_miss = sum(len(d["fields"]) for d in misses[m])
        cost = runner.ledger.cost.get(m, 0.0)
        calls = max(runner.ledger.calls.get(m, 0), 1)
        results[m] = {
            "vendor_family": c["family"],
            "owner": c["owner"],
            "misses": n_miss,
            "miss_details": misses[m],
            "n_checks": total_checks,
            "passed": n_miss <= CALIBRATION_MAX_MISSES,
            "parse_failures_or_errors": parse_fail[m],
            "call_errors": call_errors[m][:5],
            "mean_cost_per_call": cost / calls,
        }
    active = select_active_panel(results)
    for r in results.values():
        r["mean_cost_per_call"] = round(r["mean_cost_per_call"], 8)
    return {
        "label": SILVER_LABEL,
        "prompt_hash": PROMPT_HASH,
        "control_items": len(items),
        "total_checks_per_candidate": total_checks,
        "max_allowed_misses": CALIBRATION_MAX_MISSES,
        "candidates": results,
        "dropped_no_zdr": dropped,
        "active_panel": active,
        "gate_outcome": "pass"
        if active
        else "FAIL: fewer than 3 passing candidates from 3 vendors",
        "cost": runner.ledger.summary(),
    }


# ---------------------------------------------------------------------------------------------
# Equivalence, resolution, agreement
# ---------------------------------------------------------------------------------------------

NO_RESPONSE = voting.NO_RESPONSE


def _norm_items(field_name: str, items: Sequence[str] | None) -> set[str]:
    out = items or []
    if field_name == "topics":
        return {canonical_topic(i) for i in out if str(i).strip()}
    if field_name == "competitor_mentions":
        return {canonical_competitor(i) for i in out if str(i).strip()}
    return voting._normalize_list(out)


def equivalent(field_name: str, a: Any, b: Any) -> bool:
    """Pre-registered equivalence relation per field (spec: Equivalence classes)."""
    if field_name == "product":
        ca, cb = canonical_product(a), canonical_product(b)
        return ca == cb
    if field_name in LIST_FIELDS:
        return voting._jaccard(_norm_items(field_name, a), _norm_items(field_name, b)) >= JACCARD
    if field_name == "stars_inferred":
        if a is None or b is None:
            return a is None and b is None
        return abs(int(a) - int(b)) <= 1
    return a == b


def resolve_field(
    field_name: str, outputs: dict[str, dict[str, Any] | None], variant: str = "adr0030"
) -> dict[str, Any]:
    """Resolve one field across the panel. Returns level, silver, classes (judge order).

    `variant="panel1_literal"` is voting.consensus_for_item unchanged; `adr0030` uses
    `equivalent()` with the panel-1 unanimous/majority/split semantics (ADR 0020: an invited judge
    that did not respond prevents "unanimous").
    """
    judges = list(outputs)
    vals = {
        j: (outputs[j].get(field_name) if outputs[j] is not None else NO_RESPONSE) for j in judges
    }
    if variant == "panel1_literal":
        res = voting.consensus_for_item(outputs)[field_name]
        return {
            "level": res["agreement"],
            "silver": res["silver"],
            "classes": _classes(field_name, vals),
        }
    present = [j for j in judges if vals[j] is not NO_RESPONSE]
    classes = _classes(field_name, vals)
    if len(present) < 2:
        return {"level": "insufficient", "silver": None, "classes": classes}

    if field_name == "stars_inferred":
        silver, level = voting.vote_scalar_tolerant(vals, 1)
        return {"level": level, "silver": silver, "classes": classes}

    pairs = [
        (a, b)
        for i, a in enumerate(present)
        for b in present[i + 1 :]
        if equivalent(field_name, vals[a], vals[b])
    ]
    all_pairs = len(present) * (len(present) - 1) // 2
    if len(present) == len(judges) and len(pairs) == all_pairs:
        level = "unanimous"
        agreeing = present
    elif pairs:
        level = "majority"
        agreeing = list(pairs[0])
    else:
        return {"level": "split", "silver": None, "classes": classes}
    if field_name in LIST_FIELDS:
        rep = max(agreeing, key=lambda j: len(vals[j] or []))
        silver = list(vals[rep] or [])
    else:
        silver = vals[agreeing[0]]
    return {"level": level, "silver": silver, "classes": classes}


def _classes(field_name: str, vals: dict[str, Any]) -> dict[str, Any]:
    """Class id per judge (first-appearance order over connected components); None = no response."""
    judges = list(vals)
    parent = {j: j for j in judges}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    present = [j for j in judges if vals[j] is not NO_RESPONSE]
    for i, a in enumerate(present):
        for b in present[i + 1 :]:
            if equivalent(field_name, vals[a], vals[b]):
                parent[find(a)] = find(b)
    ids: dict[str, int] = {}
    out: dict[str, Any] = {}
    for j in judges:
        if vals[j] is NO_RESPONSE:
            out[j] = None
            continue
        root = find(j)
        ids.setdefault(root, len(ids))
        out[j] = ids[root]
    return out


def agreement_stats(
    unit_classes: list[dict[str, Any]], judges: list[str], *, ordinal: bool = False
) -> dict[str, Any]:
    """Krippendorff alpha + Fleiss kappa over per-unit class assignments {judge: class|None}."""
    matrix = [[u[j] for u in unit_classes] for j in judges]
    # Ordinal rank uses the categories actually observed (eval.agreement needs a marginal for
    # every ranked category; an unobserved 1..5 value would KeyError). Disclosed in spec D2.
    observed = sorted({v for row in matrix for v in row if v is not None}) if ordinal else []
    alpha = (
        krippendorff_alpha(matrix, "ordinal", categories=observed)
        if ordinal
        else krippendorff_alpha(matrix, "nominal")
    )
    full = [u for u in unit_classes if all(u[j] is not None for j in judges)]
    cats = sorted({u[j] for u in full for j in judges}, key=str)
    table = [[sum(1 for j in judges if u[j] == c) for c in cats] for u in full]
    kappa = fleiss_kappa(table) if table else None
    return {
        "alpha": alpha,
        "alpha_level": "ordinal" if ordinal else "nominal",
        "fleiss_kappa": kappa,
        "n_units": len(unit_classes),
        "n_units_fully_covered": len(full),
    }


def _scalar_classes(field_name: str, vals: dict[str, Any]) -> dict[str, Any]:
    """For scalar closed-set fields the class is the value itself (None stays a real answer)."""
    return {
        j: ("null" if v is None else v) if v is not NO_RESPONSE else None for j, v in vals.items()
    }


def field_agreement(
    per_review: dict[str, dict[str, dict[str, dict[str, Any] | None]]],
    review_ids: list[str],
    judges: list[str],
) -> dict[str, Any]:
    """Per-field and pooled alpha/kappa over the given reviews.

    per_review[rid][judge] -> parsed output or None. Free-text classes are connected components
    of the pre-registered equivalence (arbitrary ids; within-item concordance, see spec).
    """
    out: dict[str, Any] = {}
    pooled: list[dict[str, Any]] = []
    for f in HEADLINE_FIELDS:
        units = []
        for rid in review_ids:
            outputs = per_review[rid]
            vals = {
                j: (outputs[j].get(f) if outputs[j] is not None else NO_RESPONSE) for j in judges
            }
            if f in ("buy_again", "sentiment"):
                cls = _scalar_classes(f, vals)
            elif f == "stars_inferred":
                cls = {j: (v if v is not NO_RESPONSE else None) for j, v in vals.items()}
                cls = {j: (v if v in (1, 2, 3, 4, 5) else None) for j, v in cls.items()}
            else:
                cls = _classes(f, vals)
            units.append(cls)
        out[f] = agreement_stats(units, judges, ordinal=(f == "stars_inferred"))
        pooled.extend({j: (None if c is None else f"{f}:{c}") for j, c in u.items()} for u in units)
    out["_pooled_nominal"] = agreement_stats(pooled, judges)
    return out


# ---------------------------------------------------------------------------------------------
# Target sets
# ---------------------------------------------------------------------------------------------


def load_targets() -> dict[str, Any]:
    """Fixtures, held-out records, T and V ids (spec: Target sets)."""
    from eval.score_held_out_corpus_v2 import load_quarantined_fixtures

    artifact = json.loads(HELD_OUT_ARTIFACT.read_text(encoding="utf-8"))
    fixtures = {fx["id"]: fx for fx in load_quarantined_fixtures()}
    records = [r for r in artifact["records"] if not r.get("exposure")]
    fields = artifact["headline_fields"]
    unscored = {r["id"]: sorted(set(r["unresolved_fields"]) & set(fields)) for r in records}
    t_ids = sorted(i for i, u in unscored.items() if u)
    pool = sorted(i for i, u in unscored.items() if not u)
    v_ids = sorted(random.Random(SEED).sample(pool, min(N_VALIDATION, len(pool))))  # noqa: S311
    return {
        "artifact": artifact,
        "fixtures": fixtures,
        "records": records,
        "headline_fields": fields,
        "unscored": unscored,
        "T": t_ids,
        "V": v_ids,
        "V_pool_size": len(pool),
    }


async def run_main(runner: Runner, active: list[str], targets: dict[str, Any]) -> dict[str, Any]:
    fixtures = targets["fixtures"]
    units = [("T", rid) for rid in targets["T"]] + [("V", rid) for rid in targets["V"]]
    for arm, rid in units:
        text = fixtures[rid]["review_text"]
        await asyncio.gather(*[runner.judge(f"main_{arm}", rid, m, text) for m in active])
        await asyncio.sleep(PACE_SECONDS if runner.mode == "record" else 0)
    for rid in targets["T"][:SELF_CONSISTENCY_N]:
        text = fixtures[rid]["review_text"]
        await asyncio.gather(
            *[runner.judge("self_consistency", rid, m, text, rep=1) for m in active]
        )
        await asyncio.sleep(PACE_SECONDS if runner.mode == "record" else 0)
    return {}


def outputs_by_review(
    votes: list[dict[str, Any]], phase_prefix: str, rep: int = 0
) -> dict[str, Any]:
    out: dict[str, dict[str, Any]] = {}
    for v in votes:
        if v["phase"].startswith(phase_prefix) and v["rep"] == rep:
            out.setdefault(v["unit_id"], {})[v["model"]] = v["parsed"]
    return out


def build_silver(
    votes: list[dict[str, Any]], active: list[str], targets: dict[str, Any], ledger: Ledger
) -> dict[str, Any]:
    from eval.heldout_unscored import adjudicated_block

    fixtures = targets["fixtures"]
    fields = targets["headline_fields"]
    main_out = outputs_by_review(votes, "main_")
    t_ids, v_ids = targets["T"], targets["V"]

    def level_counts(ids: list[str], variant: str, only_unscored: bool) -> dict[str, Any]:
        per_field: dict[str, dict[str, int]] = {}
        total = {"pairs": 0, "unanimous": 0, "majority_only": 0, "resolved": 0}
        for f in fields:
            c = {"pairs": 0, "unanimous": 0, "majority_only": 0, "resolved": 0}
            for rid in ids:
                if only_unscored and f not in targets["unscored"][rid]:
                    continue
                c["pairs"] += 1
                lvl = resolve_field(f, main_out[rid], variant)["level"]
                if lvl == "unanimous":
                    c["unanimous"] += 1
                    c["resolved"] += 1
                elif lvl == "majority":
                    c["majority_only"] += 1
                    c["resolved"] += 1
            per_field[f] = c
            for k in total:
                total[k] += c[k]
        return {"overall": total, "per_field": per_field}

    variants: dict[str, Any] = {}
    for variant in ("adr0030", "panel1_literal"):
        t = level_counts(t_ids, variant, only_unscored=True)
        n = t["overall"]["pairs"]
        variants[variant] = {
            "T_unscored_pairs": t,
            "r_primary_resolved": t["overall"]["resolved"] / n if n else 0.0,
            "r_strict_unanimous": t["overall"]["unanimous"] / n if n else 0.0,
            "r_majority_of_3": t["overall"]["resolved"] / n if n else 0.0,
            "r_per_field_primary": {
                f: (c["resolved"] / c["pairs"] if c["pairs"] else None)
                for f, c in t["per_field"].items()
            },
        }

    # Silver labels for the primary variant.
    silver_pairs: dict[str, dict[str, Any]] = {}
    for rid in t_ids:
        for f in targets["unscored"][rid]:
            res = resolve_field(f, main_out[rid], "adr0030")
            silver_pairs[f"{rid}.{f}"] = {
                "review_id": rid,
                "field": f,
                "level": res["level"],
                "silver": res["silver"] if res["level"] in ("unanimous", "majority") else None,
                "votes": {
                    j: (main_out[rid][j].get(f) if main_out[rid][j] else "NO_RESPONSE")
                    for j in active
                },
                "label": SILVER_LABEL,
            }

    # Validation arm: concordance with panel-1 silver (stored fixture gold on resolved pairs).
    v_conc = {"all": _empty_conc(), "panel1_unanimous_only": _empty_conc(), "per_field": {}}
    for f in fields:
        v_conc["per_field"][f] = _empty_conc()
    for rid in v_ids:
        fx = fixtures[rid]
        agreement = fx["labeling_meta"]["agreement_per_field"]
        for f in fields:
            gold = fx["ground_truth"][f]
            res = resolve_field(f, main_out[rid], "adr0030")
            resolved = res["level"] in ("unanimous", "majority")
            concordant = resolved and equivalent(f, res["silver"], gold)
            for bucket in (v_conc["all"], v_conc["per_field"][f]) + (
                (v_conc["panel1_unanimous_only"],) if agreement.get(f) == "unanimous" else ()
            ):
                bucket["pairs"] += 1
                bucket["panel2_resolved"] += int(resolved)
                bucket["concordant"] += int(concordant)
    for bucket in (v_conc["all"], v_conc["panel1_unanimous_only"], *v_conc["per_field"].values()):
        p, r, c = bucket["pairs"], bucket["panel2_resolved"], bucket["concordant"]
        bucket["concordance_among_resolved"] = c / r if r else None
        bucket["concordance_counting_unresolved_as_miss"] = c / p if p else None

    # Agreement statistics.
    agreement = {
        "T": field_agreement(main_out, t_ids, active),
        "V": field_agreement(main_out, v_ids, active),
        "caveat": (
            "Free-text classes are connected components of the pre-registered equivalence "
            "(arbitrary ids across items): alpha/kappa there measure within-item concordance."
        ),
    }

    # Self-consistency.
    rep0 = outputs_by_review(votes, "main_", rep=0)
    rep1 = outputs_by_review(votes, "self_consistency", rep=1)
    sc: dict[str, Any] = {}
    for m in active:
        n = same = 0
        for rid, outs in rep1.items():
            if outs.get(m) is None or rep0[rid].get(m) is None:
                continue
            for f in fields:
                n += 1
                same += int(equivalent(f, outs[m].get(f), rep0[rid][m].get(f)))
        sc[m] = {"pairs_compared": n, "equivalent": same, "rate": same / n if n else None}

    narrowed = adjudicated_block(
        targets["artifact"]["records"],
        fixtures,
        {k: v["silver"] for k, v in silver_pairs.items() if v["silver"] is not None},
        fields,
    )
    return {
        "label": SILVER_LABEL,
        "active_panel": active,
        "n_T_reviews": len(t_ids),
        "n_V_reviews": len(v_ids),
        "V_pool_size": targets["V_pool_size"],
        "V_review_ids": v_ids,
        "variants": variants,
        "silver_pairs": silver_pairs,
        "validation_concordance": v_conc,
        "agreement": agreement,
        "self_consistency": sc,
        "narrowed_bounds": narrowed,
        "cost": ledger.summary(),
    }


def _empty_conc() -> dict[str, Any]:
    return {"pairs": 0, "panel2_resolved": 0, "concordant": 0}


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_votes(votes: list[dict[str, Any]]) -> None:
    VOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with VOTES_PATH.open("w", encoding="utf-8") as fh:
        for v in votes:
            fh.write(json.dumps(v, ensure_ascii=False, sort_keys=True) + "\n")


def _load_votes_file() -> list[dict[str, Any]]:
    if not VOTES_PATH.exists():
        return []
    return [json.loads(x) for x in VOTES_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]


async def amain(args: argparse.Namespace) -> int:
    cassette = Cassette()
    ledger = Ledger()
    # The cap is cumulative across runs: seed live spend with what the cassette already cost.
    ledger.live_cost["__prior_cassette__"] = sum(
        float(e.get("cost_usd") or 0.0) for e in cassette.store.values()
    )
    runner = Runner(mode=args.mode, cassette=cassette, ledger=ledger)
    http: Any = None
    zdr: set[str] | None = None
    if args.mode == "record":
        import httpx

        http = httpx.AsyncClient()
        runner.http = http
        runner.api_key = load_api_key()
        zdr = await fetch_zdr_models(http)
    prior_votes = _load_votes_file() if args.phase == "main" else []
    try:
        if args.phase in ("calibration", "all"):
            cal = await run_calibration(runner, zdr)
            if args.mode != "report":
                _write_json(CALIBRATION_PATH, cal)
            print(json.dumps({k: v for k, v in cal.items() if k != "candidates"}, indent=1))
            for m, r in cal["candidates"].items():
                print(
                    f"  {m}: misses={r['misses']} passed={r['passed']} items={[d['item_id'] for d in r['miss_details']]}"
                )
            if not cal["active_panel"]:
                print("STOP: calibration gate failed (fewer than 3 passing, 3 vendors).")
                if args.mode != "report":
                    _write_votes(runner.votes)
                return 2
        if args.phase in ("main", "all"):
            cal = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
            active = cal["active_panel"]
            if len(active) != 3:
                print("STOP: no 3-judge active panel in the calibration artifact.")
                return 2
            roster = tuple({"id": m} for m in active)
            panel.assert_no_self_judging(roster, extra_forbidden_families=PANEL1_FORBIDDEN_FAMILIES)
            targets = load_targets()
            await run_main(runner, active, targets)
            all_votes = [*prior_votes, *runner.votes] if args.phase == "main" else runner.votes
            seen: set[tuple[str, str, str, int]] = set()
            deduped = []
            for v in all_votes:
                k = (v["phase"], v["unit_id"], v["model"], v["rep"])
                if k not in seen:
                    seen.add(k)
                    deduped.append(v)
            full_ledger = Ledger()
            for v in deduped:
                full_ledger.add(v, live=False)
            silver = build_silver(deduped, active, targets, full_ledger)
            if args.mode != "report":
                _write_votes(deduped)
                _write_json(SILVER_PATH, silver)
            print(
                json.dumps(
                    {k: v for k, v in silver.items() if k != "silver_pairs"}, indent=1, default=str
                )[:12000]
            )
        elif args.mode != "report" and args.phase == "calibration":
            _write_votes(runner.votes)
    except BudgetExceededError as exc:
        print(f"BUDGET STOP: {exc}")
        return 3
    finally:
        if http is not None:
            await http.aclose()
    print(json.dumps(ledger.summary(), indent=1))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["record", "replay", "report"], required=True)
    ap.add_argument("--phase", choices=["calibration", "main", "all"], default="all")
    args = ap.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
