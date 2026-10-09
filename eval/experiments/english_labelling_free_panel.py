"""S20 F5: free-panel re-run of the English labelling pilot with urgency rubric v1.1.

Why: the paid pilot (english_labelling_pilot.py) stopped at its gate with urgency alpha 0.618 < 0.67.
This re-run labels FRESH items (disjoint from the pilot's 20) on OpenRouter models whose id ends in
':free' ONLY (no paid spend, ever), with one tie-break sentence added to the urgency rubric
(v1.1, added AFTER seeing 0.618, so the resulting alpha is exploratory). Plan and numbers:
docs/research/s20-english-fixture-labelling-plan.md ("Free-panel re-run").

Free limits (BELIEVED, OpenRouter docs, <10 credits purchased): 20 req/min, 50 req/day across all
':free' models. Hence: >= 4 s between calls, bounded retries, hard stop at MAX_REQUESTS or the
moment a daily-cap error appears, state persisted atomically after every call (resumable).

Modes: `run` (canary = first item x 3 models, then the rest; resumable), `report` (zero-cost).
The API key never reaches argv or output.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.agreement import krippendorff_alpha  # noqa: E402
from eval.consensus import panel  # noqa: E402
from eval.experiments.english_labelling_pilot import OUT_DIR, SELECTION, _key  # noqa: E402
from eval.provenance import get_git_sha, now_iso  # noqa: E402

SEED = 42
API = "https://openrouter.ai/api/v1"
RUBRIC_VERSION = "v1.1"
TIE_BREAK = (
    "If a review names a cost, safety, health or deadline consequence it is at least medium; "
    "use high only when the reviewer states ongoing harm or an unmet urgent need; when torn "
    "between two adjacent levels choose the lower one."
)
# Appended to the end of the "low" definition: the only change to the rubric text.
URGENCY_ANCHOR = '  "low" = no concrete defect -- praise, neutral commentary, or a subjective preference\n    only.\n'

PANEL: tuple[str, ...] = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "dots-studio/dots-3-note-preview:free",
)
N_ITEMS = 14
MAX_REQUESTS = 44  # hard cap on HTTP requests made by this experiment (50/day free limit)
MIN_GAP_S = 4.0
MAX_TRIES = 3  # per item per model
MAX_TOKENS = 1500  # headroom in case a model emits a reasoning preamble
ORDINAL_URGENCY = ["low", "medium", "high"]
PILOT_ALPHA_URGENCY = 0.618
BOOT_RESAMPLES = 2000
MIN_BOOT_N = 8

STATE = OUT_DIR / "free_panel_state.json"
RESULT = ROOT / "eval" / "results" / "s20_english_labelling_free_panel.json"


def v11_user_template() -> str:
    """The judge user template with the single v1.1 tie-break sentence added to urgency."""
    tpl = panel.JUDGE_USER_TEMPLATE
    if tpl.count(URGENCY_ANCHOR) != 1:
        raise SystemExit("rubric anchor not found exactly once; panel.py changed")
    addition = "  Tie-break: " + TIE_BREAK
    # wrap at ~90 cols like the surrounding text
    words, lines, cur = addition.split(), [], "  "
    for w in words:
        if cur.strip() and len(cur) + 1 + len(w) > 90:
            lines.append(cur)
            cur = "    " + w
        else:
            cur = f"{cur} {w}" if cur.strip() else cur + w
    lines.append(cur)
    return tpl.replace(URGENCY_ANCHOR, URGENCY_ANCHOR + "\n".join(lines) + "\n")


def pilot_ids() -> set[str]:
    """Item ids the paid pilot labelled (from its raw outputs in the scratchpad)."""
    raw = OUT_DIR / "raw_label.jsonl"
    rows = raw.read_text(encoding="utf-8").splitlines()
    return {json.loads(x)["item_id"] for x in rows if x.strip()}


def choose_items() -> list[dict[str, Any]]:
    """Deterministic (seed 42) fresh sample from the 71-item pool, disjoint from the pilot."""
    sel = json.loads(SELECTION.read_text(encoding="utf-8"))["items"][:71]
    used = pilot_ids()
    fresh = sorted((i for i in sel if i["id"] not in used), key=lambda i: i["id"])
    picked = random.Random(SEED).sample(fresh, N_ITEMS)
    overlap = {i["id"] for i in picked} & used
    assert not overlap, f"pilot overlap: {overlap}"  # noqa: S101 -- experiment-integrity assertion
    assert len(used) == 20 and len(picked) == N_ITEMS  # noqa: S101
    return picked


def load_state() -> dict[str, Any]:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"requests": 0, "stopped": None, "records": [], "errors": [], "started": now_iso()}


def save_state(state: dict[str, Any]) -> None:
    """Atomic write: temp file then replace."""
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def verify_models() -> dict[str, Any]:
    """Confirm every panel id is listed, ends in ':free' and supports response_format."""
    with urllib.request.urlopen(f"{API}/models", timeout=60) as r:  # noqa: S310
        data = {m["id"]: m for m in json.loads(r.read())["data"]}
    out = {}
    for mid in PANEL:
        m = data.get(mid)
        ok = bool(m) and mid.endswith(":free") and "response_format" in m["supported_parameters"]
        if not ok:
            raise SystemExit(f"panel model unavailable or not :free/json-capable: {mid}")
        out[mid] = {"listed": True, "free_suffix": True, "response_format": True}
    return out


def _classify(code: int, body: str) -> str:
    low = body.lower()
    if code == 429 and ("per-day" in low or "per day" in low or "daily" in low):
        return "daily_cap"
    return {429: "rate_limit_429", 503: "unavailable_503", 403: "forbidden_403"}.get(
        code, f"http_{code}"
    )


def one_request(model: str, text: str, tpl: str, key: str) -> tuple[str | None, str | None, Any]:
    """One HTTP request. Returns (content, error_class, usage_cost). Never raises on HTTP errors."""
    if not model.endswith(":free"):  # belt and braces: never call a paid model
        raise SystemExit(f"refusing non-:free model {model}")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": panel.JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": tpl.format(text=text)},
        ],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    req = urllib.request.Request(  # noqa: S310 -- fixed https URL
        f"{API}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
            resp = json.loads(r.read())
        if "error" in resp and "choices" not in resp:
            return None, "body_error", None
        content = resp["choices"][0]["message"].get("content") or ""
        return content, None, (resp.get("usage") or {}).get("cost")
    except urllib.error.HTTPError as exc:
        try:
            msg = exc.read().decode("utf-8", "replace")[:500]
        except OSError:
            msg = ""
        return None, _classify(exc.code, msg), None
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__, None


def _breaker_open(state: dict[str, Any], model: str) -> bool:
    """True when the last item for this model exhausted all its tries (3 canary 429s)."""
    recent = [r for r in state["records"] if r["model"] == model][-1:]
    return bool(recent) and recent[0].get("error") == "exhausted"


def cmd_run(canary_only: bool = False) -> None:
    """Canary (first item x 3 models), then remaining items, resumable and hard-capped."""
    models = verify_models()
    items = choose_items()
    tpl = v11_user_template()
    key = _key()
    state = load_state()
    state["models_verified"] = models
    state["item_ids"] = [i["id"] for i in items]
    done = {(r["item_id"], r["model"]) for r in state["records"]}
    last = 0.0
    for it in items:
        for model in PANEL:
            if (it["id"], model) in done:
                continue
            if _breaker_open(state, model):  # model keeps failing: do not burn the daily quota
                continue
            for attempt in range(1, MAX_TRIES + 1):
                if state["requests"] >= MAX_REQUESTS or state["stopped"]:
                    break
                wait = MIN_GAP_S - (time.time() - last)
                if wait > 0:
                    time.sleep(wait)
                content, err, cost = one_request(model, it["text"], tpl, key)
                last = time.time()
                state["requests"] += 1
                rec = {
                    "ts": now_iso(),
                    "item_id": it["id"],
                    "model": model,
                    "attempt": attempt,
                    "error": err,
                    "raw": content,
                    "cost": cost,
                }
                if err:
                    state["errors"].append({k: rec[k] for k in ("ts", "item_id", "model", "error")})
                    if err == "daily_cap":
                        state["stopped"] = "daily_cap"
                    elif err in ("rate_limit_429", "unavailable_503", "forbidden_403"):
                        time.sleep(15 * attempt)  # bounded backoff
                    save_state(state)
                    continue
                state["records"].append(rec)
                save_state(state)
                break
            else:
                # exhausted MAX_TRIES: record the permanent miss so a resume does not re-spend
                state["records"].append(
                    {"item_id": it["id"], "model": model, "error": "exhausted", "raw": None}
                )
                save_state(state)
            if state["requests"] >= MAX_REQUESTS and not state["stopped"]:
                state["stopped"] = "request_cap"
            if state["stopped"]:
                save_state(state)
                print("STOP:", state["stopped"], "requests", state["requests"])
                return
        if it is items[0]:
            print("canary done:", [(r["model"], bool(r["raw"])) for r in state["records"]])
            if canary_only:
                return
    # Spare-quota pass: one single try per item for models whose breaker opened, until the cap.
    for it in items:
        for model in PANEL:
            if state["stopped"] or state["requests"] >= MAX_REQUESTS:
                break
            if any(r["item_id"] == it["id"] and r["model"] == model for r in state["records"]):
                continue
            time.sleep(max(0.0, MIN_GAP_S - (time.time() - last)))
            content, err, cost = one_request(model, it["text"], tpl, key)
            last = time.time()
            state["requests"] += 1
            rec = {"ts": now_iso(), "item_id": it["id"], "model": model, "attempt": 1}
            rec.update({"error": err, "raw": content, "cost": cost, "pass": 2})
            if err:
                state["errors"].append({k: rec[k] for k in ("ts", "item_id", "model", "error")})
                if err == "daily_cap":
                    state["stopped"] = "daily_cap"
            state["records"].append(rec)
            save_state(state)
    state["stopped"] = state["stopped"] or (
        "request_cap" if state["requests"] >= MAX_REQUESTS else "completed"
    )
    save_state(state)
    print("done; requests", state["requests"], state["stopped"])


def _bootstrap(mat: list[list[Any]], level: str, cats: list[str] | None) -> list[float] | None:
    n_units = len(mat[0])
    if n_units < MIN_BOOT_N:
        return None
    rng = random.Random(SEED)
    vals: list[float] = []
    for _ in range(BOOT_RESAMPLES):
        idx = [rng.randrange(n_units) for _ in range(n_units)]
        try:
            v = krippendorff_alpha([[row[i] for i in idx] for row in mat], level, categories=cats)
        except KeyError:  # ordinal delta needs every category present; resample skipped
            continue
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    return [vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1], len(vals)]


def cmd_report() -> None:
    state = load_state()
    item_ids = state["item_ids"]
    labels: dict[str, dict[str, dict[str, Any] | None]] = {
        i: {m: None for m in PANEL} for i in item_ids
    }
    unparseable: Counter[str] = Counter()
    for r in state["records"]:
        if not r.get("raw"):
            continue
        p = panel.parse_judge_response(r["raw"])
        if p is None:
            unparseable[r["model"]] += 1
            continue
        labels[r["item_id"]][r["model"]] = {
            "sentiment": p.sentiment,
            "urgency": p.urgency,
        }
    usable = [i for i in item_ids if sum(labels[i][m] is not None for m in PANEL) >= 2]
    stats: dict[str, Any] = {"n_items_usable_ge2_models": len(usable)}
    for field, level, cats in (
        ("urgency", "nominal", None),
        ("urgency", "ordinal", ORDINAL_URGENCY),
        ("sentiment", "nominal", None),
    ):
        mat = [[(labels[i][m] or {}).get(field) for i in usable] for m in PANEL]
        a = krippendorff_alpha(mat, level, categories=cats) if usable else None
        stats[f"{field}_{level}"] = {
            "alpha": a,
            "bootstrap_ci95_lo_hi_nresamples": _bootstrap(mat, level, cats) if usable else None,
        }
    valid = {m: sum(labels[i][m] is not None for i in item_ids) for m in PANEL}
    unanimous = {
        f: sum(
            len({labels[i][m][f] for m in PANEL if labels[i][m]}) == 1
            and sum(labels[i][m] is not None for m in PANEL) >= 2
            for i in usable
        )
        for f in ("urgency", "sentiment")
    }
    errs = Counter(e["error"] for e in state["errors"])
    empty = Counter(
        r["model"] for r in state["records"] if r.get("error") is None and not r.get("raw")
    )
    out = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "command": "python eval/experiments/english_labelling_free_panel.py run; ... report",
        "script": "eval/experiments/english_labelling_free_panel.py",
        "seed": SEED,
        "rubric_version": RUBRIC_VERSION,
        "rubric_tie_break": TIE_BREAK,
        "rubric_note": "tie-break added AFTER seeing pilot alpha 0.618: exploratory, not confirmatory",
        "panel": list(PANEL),
        "models_verified": state.get("models_verified"),
        "item_ids": item_ids,
        "pilot_item_overlap": sorted(set(item_ids) & pilot_ids()),
        "requests_used": state["requests"],
        "max_requests": MAX_REQUESTS,
        "stopped": state["stopped"],
        "errors_by_type": dict(errs),
        "valid_labels_per_model": valid,
        "unparseable_per_model": dict(unparseable),
        "empty_content_http200_per_model": dict(empty),
        "unanimous_items_among_usable": unanimous,
        "stats": stats,
        "pilot_urgency_alpha": PILOT_ALPHA_URGENCY,
        "per_item_labels": labels,
        "total_cost_usd_reported": sum(r.get("cost") or 0 for r in state["records"]),
    }
    RESULT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "requests_used",
                    "stopped",
                    "errors_by_type",
                    "valid_labels_per_model",
                    "unanimous_items_among_usable",
                    "stats",
                )
            },
            indent=1,
        )
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["run", "report"])
    ap.add_argument("--canary", action="store_true", help="first item x 3 models only")
    a = ap.parse_args()
    if a.mode == "run":
        cmd_run(a.canary)
    else:
        cmd_report()


if __name__ == "__main__":
    main()
