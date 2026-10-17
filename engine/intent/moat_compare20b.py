"""Moat compare report for the gpt-oss-20b arms (spec Amendment 11, registered before any number here was computed).

Arms: the run-2 encoder, `gpt-oss-20b-local` (zero-shot, Amendment 9) and `gpt-oss-20b-local-fewshot` (Amendment 10), plus the
502 Groq gpt-oss-20b rows as a labelled cross-check that never enters a verdict. Gold = four-judge panel consensus (SILVER).

Rules (see Amendment 11 for the authoritative text):
  * operating point per arm and task = the highest-coverage confidence threshold whose Wilson 95 percent LOWER bound of
    precision among answered items is at least 0.95 (whole tie groups; coverage 0 if none qualifies);
  * MATCHED ids = first 300 sealed-order ids that every compared arm answered and that have gold and an encoder prediction;
  * matched-coverage reading: every arm read at the coverage of the weakest arm with non-zero coverage;
  * paired item bootstrap, seed 42, 2000 resamples, one index set per replicate shared by all arms, operating point recomputed;
  * "A beats B" only if the bootstrap interval excludes 0 in A's favour AND the operating points agree in direction.

    python -m engine.intent.moat_compare20b --out-json reports/intent/moat_compare_20b.json --out-md reports/intent/moat_compare_20b.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from engine.experiments import selective as SEL
from engine.intent import llm_moat as M

ROOT = M.ROOT
TARGET = 0.95
N_BOOT = 2000
SEED = 42
N_MATCHED_MAX = 300
Z = 1.96
ENC_RUN = "run2"
ARM_ZS, ARM_FS, ARM_ENC, ARM_GROQ = (
    "gpt-oss-20b-local",
    "gpt-oss-20b-local-fewshot",
    "encoder-run2",
    "groq-gpt-oss-20b",
)
PAIRS = [(ARM_ENC, ARM_ZS), (ARM_ENC, ARM_FS), (ARM_FS, ARM_ZS)]
DEFAULTS = {
    "zs": Path("D:/ml-cache/s23/llm_local/openai_gpt-oss-20b__local.jsonl"),
    "fs": Path("D:/ml-cache/s24/llm_local_fs/openai_gpt-oss-20b__local_fewshot.jsonl"),
    "groq": Path("D:/ml-cache/s23/llm/openai_gpt-oss-20b.jsonl"),
}
Arrays = tuple[np.ndarray, np.ndarray]  # (correct bool, confidence float), aligned on ids


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials; (nan, nan) when n == 0."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half) / den, (centre + half) / den


def _groups(correct: np.ndarray, conf: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(answered count, correct count, threshold) at every tie-group boundary, highest confidence first."""
    order = np.argsort(-conf, kind="stable")
    s = conf[order]
    cum = np.cumsum(correct[order].astype(int))
    ends = np.flatnonzero(np.r_[s[1:] != s[:-1], True])
    return ends + 1, cum[ends], s[ends]


def op_point(correct: np.ndarray, conf: np.ndarray, target: float = TARGET) -> dict:
    """Highest-coverage threshold whose Wilson lower bound of precision is >= target (Amendment 11 (a))."""
    n = len(correct)
    if n == 0:
        return {"n": 0, "answered": 0, "coverage": 0.0, "qualifies": False}
    ans, ok, thr = _groups(correct, conf)
    lo = SEL.wilson_lower(ok.astype(float), ans.astype(float))
    q = np.flatnonzero(lo >= target)
    if len(q) == 0:
        return {"n": n, "answered": 0, "coverage": 0.0, "qualifies": False}
    j = int(
        q[-1]
    )  # answered counts increase with j, so the last qualifying group has the highest coverage
    return _point(n, int(ans[j]), int(ok[j]), float(thr[j]), True)


def _point(n: int, answered: int, ok: int, thr: float, qualifies: bool) -> dict:
    lo, hi = wilson(ok, answered)
    return {
        "n": n,
        "answered": answered,
        "threshold": thr,
        "coverage": answered / n,
        "precision": ok / answered,
        "precision_wilson95": [lo, hi],
        "qualifies": qualifies,
    }


def at_count(correct: np.ndarray, conf: np.ndarray, k_max: int) -> dict:
    """Largest whole-tie-group answered set with at most k_max items; the top group alone if it is larger (overshoot)."""
    n = len(correct)
    ans, ok, thr = _groups(correct, conf)
    fit = np.flatnonzero(ans <= k_max)
    j, over = (int(fit[-1]), False) if len(fit) else (0, True)
    p = _point(n, int(ans[j]), int(ok[j]), float(thr[j]), True)
    p["overshoot"] = over
    return p


def matched_ids(
    order: list[str], tables: dict[str, dict[str, tuple[bool, float]]], n_max: int
) -> list[str]:
    """First n_max ids of `order` that every table has (Amendment 11, MATCHED ids)."""
    return [i for i in order[:n_max] if all(i in t for t in tables.values())]


def _stack(ids: list[str], t: dict[str, tuple[bool, float]]) -> Arrays:
    return np.array([t[i][0] for i in ids], dtype=bool), np.array(
        [t[i][1] for i in ids], dtype=float
    )


def verdict(lo: float, hi: float, op_diff: float, a: str = "A", b: str = "B") -> dict:
    """Amendment 11 (d) for a difference A minus B. `op_diff` = A's minus B's operating-point coverage.

    A beats B only if the interval excludes 0 on A's side AND op_diff favours A strictly; the mirror holds for B.
    """
    a_ci, b_ci = lo > 0, hi < 0
    a_op, b_op = op_diff > 0, op_diff < 0
    v = (
        f"{a} beats {b}"
        if (a_ci and a_op)
        else f"{b} beats {a}"
        if (b_ci and b_op)
        else "not separated"
    )
    return {
        "verdict": v,
        "ci_excludes_zero": bool(a_ci or b_ci),
        "op_points_agree": bool((a_ci and a_op) or (b_ci and b_op)),
    }


def compare_task(
    arms: dict[str, Arrays],
    pairs: list[tuple[str, str]],
    n_boot: int = N_BOOT,
    seed: int = SEED,
    verdicts: bool = True,
) -> dict:
    """Operating points, matched-coverage reading and paired bootstrap differences for one task on aligned arrays."""
    n = len(next(iter(arms.values()))[0])
    ops = {a: op_point(c, k) for a, (c, k) in arms.items()}
    acc = {a: _acc(c) for a, (c, _) in arms.items()}
    pos = [o["coverage"] for o in ops.values() if o["coverage"] > 0]
    mc: dict = {"computed": bool(pos)}
    if pos:
        cstar = min(pos)
        kmax = math.ceil(cstar * n - 1e-9)
        mc.update(
            {
                "target_coverage": cstar,
                "k_max": kmax,
                "arms": {a: at_count(c, k, kmax) for a, (c, k) in arms.items()},
            }
        )
    rng = np.random.default_rng(seed)
    cov_b = {a: np.empty(n_boot) for a in arms}
    acc_b = {a: np.empty(n_boot) for a in arms}
    for b in range(n_boot):
        ix = rng.integers(0, n, n)
        for a, (c, k) in arms.items():
            cov_b[a][b] = op_point(c[ix], k[ix])["coverage"]
            acc_b[a][b] = c[ix].mean()
    out_pairs = {}
    for a, b in pairs:
        row = {}
        for name, boot, pt in (("coverage_at_op_point", cov_b, ops), ("accuracy", acc_b, None)):
            d = boot[a] - boot[b]
            lo, hi = (float(x) for x in np.percentile(d, [2.5, 97.5]))
            diff = (
                (pt[a]["coverage"] - pt[b]["coverage"]) if pt else acc[a]["value"] - acc[b]["value"]
            )
            row[name] = {"diff": diff, "ci95": [lo, hi]}
            if verdicts:
                row[name].update(verdict(lo, hi, ops[a]["coverage"] - ops[b]["coverage"], a, b))
        out_pairs[f"{a} minus {b}"] = row
    return {
        "n": n,
        "arms": {a: {"accuracy": acc[a], "op_point": ops[a]} for a in arms},
        "matched_coverage": mc,
        "pairs": out_pairs,
    }


def _acc(c: np.ndarray) -> dict:
    k, n = int(c.sum()), len(c)
    lo, hi = wilson(k, n)
    return {"value": k / n if n else float("nan"), "correct": k, "n": n, "wilson95": [lo, hi]}


# ---------------------------------------------------------------- data loading


def read_rows(p: Path) -> list[dict]:
    return [json.loads(ln) for ln in p.open(encoding="utf-8") if ln.strip()]


def llm_table(rows: list[dict], gold: dict[str, str], task: str) -> dict[str, tuple[bool, float]]:
    """id -> (correct, confidence), via the SAME per-item scoring as `llm_moat.llm_arrays`."""
    t = {}
    for r in rows:
        if r["id"] in gold:
            c, k = M.llm_arrays([r], gold, task)
            t[r["id"]] = (bool(c[0]), float(k[0]))
    return t


def enc_table(pred: dict, gold: dict[str, str]) -> dict[str, tuple[bool, float]]:
    classes = pred["classes"]
    sp = pred["splits"]["test"]
    probs = np.array(sp["probs"]).reshape(len(sp["ids"]), len(classes))
    return {
        i: (bool(classes[int(probs[j].argmax())] == gold[i]), float(probs[j].max()))
        for j, i in enumerate(sp["ids"])
        if i in gold
    }


def row_stats(rows: list[dict], ids: set[str]) -> dict:
    """Descriptive counts on the given ids: empty rows, invalid labels per task, share of confidences equal to 90."""
    sel = [r for r in rows if r["id"] in ids]
    empty = sum(1 for r in sel if not isinstance(r.get("ans"), dict) or not r["ans"])
    invalid = {t: 0 for t in M.TASKS}
    confs = []
    for r in sel:
        a = r["ans"] if isinstance(r.get("ans"), dict) else {}
        for t in M.TASKS:
            x = (a.get(t) or {}) if isinstance(a.get(t) or {}, dict) else {}
            if str(x.get("label", "")).strip().lower() not in M.CLASSES[t]:
                invalid[t] += 1
            else:
                try:
                    confs.append(float(x.get("confidence", 0)))
                except (TypeError, ValueError):
                    confs.append(0.0)
    return {
        "n_rows": len(sel),
        "n_empty_or_unparseable_rows": empty,
        "invalid_label_by_task": invalid,
        "n_valid_confidences": len(confs),
        "share_confidence_eq_90": (sum(1 for c in confs if c == 90.0) / len(confs))
        if confs
        else float("nan"),
    }


def provenance(paths: dict[str, Path]) -> dict:
    out = {}
    for name, p in paths.items():
        d: dict = {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        try:
            rel = p.resolve().relative_to(ROOT)
            d["path"] = rel.as_posix()
            d["git_blob_sha"] = subprocess.run(  # noqa: S603 - fixed argv, repo-relative path
                ["git", "hash-object", str(rel)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except ValueError:
            d["git_blob_sha"] = None  # outside the repo (private rows on D:)
        out[name] = d
    return out


def head_commit() -> str:
    return subprocess.run(  # noqa: S603, S607 - fixed argv
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def build(zs: Path, fs: Path, groq: Path, n_boot: int = N_BOOT) -> dict:
    labels_p = ROOT / "reports/intent/labels/labels.json"
    labels = json.loads(labels_p.read_text(encoding="utf-8"))["labels"]["test"]
    sealed = M.sealed_ids()
    rows = {ARM_ZS: read_rows(zs), ARM_FS: read_rows(fs), ARM_GROQ: read_rows(groq)}
    preds = {
        t: json.loads(
            (ROOT / f"reports/intent/{ENC_RUN}/pred_{t}.json").read_text(encoding="utf-8")
        )
        for t in M.TASKS
    }
    ev = json.loads((ROOT / "reports/intent/eval_run2.json").read_text(encoding="utf-8"))
    res: dict = {
        "amendment": "docs/specs/review-intent.md, Amendment 11",
        "code_commit": head_commit(),
        "labels": "SILVER (four-judge panel consensus), not human gold",
        "settings": {
            "target_wilson_lower": TARGET,
            "n_boot": n_boot,
            "seed": SEED,
            "n_matched_max": N_MATCHED_MAX,
            "encoder": ENC_RUN,
        },
        "n_sealed_after_ledger_exclusions": len(sealed),
        "tasks": {},
        "unmatched_zeroshot_vs_encoder": {},
        "groq_crosscheck": {},
        "row_stats_first300": {},
    }
    first = set(sealed[:N_MATCHED_MAX])
    res["row_stats_first300"] = {a: row_stats(r, first) for a, r in rows.items()}
    for task in M.TASKS:
        g = labels[task]
        tabs = {
            ARM_ENC: enc_table(preds[task], g),
            ARM_ZS: llm_table(rows[ARM_ZS], g, task),
            ARM_FS: llm_table(rows[ARM_FS], g, task),
        }
        ids = matched_ids(sealed, tabs, N_MATCHED_MAX)
        arrs = {a: _stack(ids, t) for a, t in tabs.items()}
        entry = compare_task(arrs, PAIRS, n_boot)
        thr = ev["tasks"][task]["selective"]["conservative_p0.95"]["threshold"]
        ce, ke = arrs[ARM_ENC]
        sel = ke >= thr
        entry["encoder_validation_threshold"] = (
            {
                "threshold": thr,
                "source": "reports/intent/eval_run2.json selective.conservative_p0.95.threshold (chosen on validation)",
                **_point(len(ids), int(sel.sum()), int(ce[sel].sum()), float(thr), True),
            }
            if sel.any()
            else {"threshold": thr, "answered": 0, "n": len(ids)}
        )
        res["tasks"][task] = entry
        # unmatched: zero-shot local on ALL sealed ids it covers, vs the encoder on the same ids
        u = matched_ids(sealed, {ARM_ENC: tabs[ARM_ENC], ARM_ZS: tabs[ARM_ZS]}, len(sealed))
        res["unmatched_zeroshot_vs_encoder"][task] = compare_task(
            {a: _stack(u, tabs[a]) for a in (ARM_ENC, ARM_ZS)}, [(ARM_ENC, ARM_ZS)], n_boot
        )
        # cross-check: Groq rows on the matched ids they cover (never in a verdict)
        gt = llm_table(rows[ARM_GROQ], g, task)
        gi = [i for i in ids if i in gt]
        tabs3 = {a: tabs[a] for a in (ARM_ENC, ARM_ZS)} | {ARM_GROQ: gt}
        res["groq_crosscheck"][task] = compare_task(
            {a: _stack(gi, t) for a, t in tabs3.items()},
            [(ARM_GROQ, ARM_ZS), (ARM_ENC, ARM_GROQ)],
            n_boot,
            verdicts=False,
        )
    res["provenance"] = provenance(
        {
            "rows_zeroshot_local": zs,
            "rows_fewshot_local": fs,
            "rows_groq_20b_crosscheck": groq,
            "labels": labels_p,
            "scale_manifest": ROOT / "reports/labelling/scale/manifest.json",
            "eval_run2": ROOT / "reports/intent/eval_run2.json",
            **{
                f"pred_{t}_{ENC_RUN}": ROOT / f"reports/intent/{ENC_RUN}/pred_{t}.json"
                for t in M.TASKS
            },
        }
    )
    return res


# ---------------------------------------------------------------- report


def _f(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


def _ci(c: list[float]) -> str:
    return f"[{_f(c[0])}, {_f(c[1])}]"


def _op(o: dict) -> str:
    if not o.get("qualifies"):
        return "0 (no qualifying threshold)"
    return f"{_f(o['coverage'])} ({o['answered']}/{o['n']}) thr {_f(o['threshold'])}; prec {_f(o['precision'])} {_ci(o['precision_wilson95'])}"


def _block(name: str, body: str) -> str:
    return f"<!-- METRICS:START:{name} -->\n{body.rstrip()}\n<!-- METRICS:END -->\n"


def _arms_table(entry: dict) -> str:
    rows = [
        "| arm | n | accuracy [Wilson 95] | operating point (Wilson lower >= target) |",
        "|---|---|---|---|",
    ]
    for a, v in entry["arms"].items():
        ac = v["accuracy"]
        rows.append(
            f"| {a} | {ac['n']} | {_f(ac['value'])} {_ci(ac['wilson95'])} | {_op(v['op_point'])} |"
        )
    return "\n".join(rows)


def _mc_table(entry: dict) -> str:
    mc = entry["matched_coverage"]
    if not mc["computed"]:
        return "not computed: every arm has zero operating-point coverage"
    rows = [
        f"matched-coverage target (weakest non-zero arm): {_f(mc['target_coverage'])}, at most {mc['k_max']} items per arm",
        "",
        "| arm | answered | coverage | precision [Wilson 95] | overshoot |",
        "|---|---|---|---|---|",
    ]
    for a, p in mc["arms"].items():
        rows.append(
            f"| {a} | {p['answered']} | {_f(p['coverage'])} | {_f(p['precision'])} {_ci(p['precision_wilson95'])} | {p['overshoot']} |"
        )
    return "\n".join(rows)


def _pair_table(entry: dict, with_verdict: bool = True) -> str:
    head = "| pair (A minus B) | metric | diff [paired bootstrap 95] |" + (
        " CI excludes 0 | op points agree | verdict |" if with_verdict else ""
    )
    rows = [head, "|---|---|---|" + ("---|---|---|" if with_verdict else "")]
    for pr, d in entry["pairs"].items():
        for m, v in d.items():
            line = f"| {pr} | {m} | {_f(v['diff'])} {_ci(v['ci95'])} |"
            if with_verdict:
                line += f" {v['ci_excludes_zero']} | {v['op_points_agree']} | {v['verdict']} |"
            rows.append(line)
    return "\n".join(rows)


def _enc_thr_table(res: dict) -> str:
    rows = [
        "| task | validation-chosen threshold | answered | coverage | precision [Wilson 95] |",
        "|---|---|---|---|---|",
    ]
    for t, e in res["tasks"].items():
        x = e["encoder_validation_threshold"]
        if x.get("answered", 0) == 0:
            rows.append(f"| {t} | {_f(x['threshold'])} | 0 | 0 | n/a |")
        else:
            rows.append(
                f"| {t} | {_f(x['threshold'])} | {x['answered']} | {_f(x['coverage'])} | {_f(x['precision'])} {_ci(x['precision_wilson95'])} |"
            )
    return "\n".join(rows)


def _verdict_summary(res: dict) -> str:
    rows = ["| task | pair | coverage at op point | accuracy |", "|---|---|---|---|"]
    for t, e in res["tasks"].items():
        for pr, d in e["pairs"].items():
            rows.append(
                f"| {t} | {pr} | {d['coverage_at_op_point']['verdict']} | {d['accuracy']['verdict']} |"
            )
    return "\n".join(rows)


def _stats_table(res: dict) -> str:
    rows = [
        "| arm (first 300 sealed ids) | rows | empty or unparseable rows | invalid labels per task (na/bi/se/ur) | valid confidences | share equal to 90 |",
        "|---|---|---|---|---|---|",
    ]
    for a, s in res["row_stats_first300"].items():
        inv = "/".join(str(s["invalid_label_by_task"][t]) for t in M.TASKS)
        rows.append(
            f"| {a} | {s['n_rows']} | {s['n_empty_or_unparseable_rows']} | {inv} | {s['n_valid_confidences']} | {_f(s['share_confidence_eq_90'])} |"
        )
    return "\n".join(rows)


def render_md(res: dict) -> str:
    s = res["settings"]
    out = [
        "# gpt-oss-20b moat compare: encoder vs local zero-shot vs local few-shot\n",
        f"Generated by `engine/intent/moat_compare20b.py` at code commit `{res['code_commit']}`; every number below sits in a generated block and is VERIFIED "
        "(computed from the artifacts listed in `moat_compare_20b.json` under `provenance`, with path, sha256 and git blob sha). Statements outside "
        "generated blocks that interpret results are marked BELIEVED. Decision rules: `docs/specs/review-intent.md`, Amendment 11 "
        "(registered and pushed before these numbers were computed). Aggregates only; no review text.\n",
        "> **Read with Amendment 12.** Both local arms here ran with the 600-token cap: 27 of 300 few-shot and 6 of 300 zero-shot completions ended "
        "empty (reasoning spent the budget) and count wrong. Any few-shot-versus-zero-shot difference below therefore mixes worse answers with "
        "empty answers and is NOT a finding about worked examples. The re-run without that artefact is under `reports/intent/moat_local20b_fewshot_v2/`.\n",
        "## Setup\n",
        "- Labels: four-judge panel consensus (SILVER), not human gold. Sample: the first 300 sealed ids in sealed order, MATCHED per task across all "
        "compared arms; a sample this small gives wide intervals and coverage at a precision floor is a knife-edge statistic.\n"
        "- Encoder: the run-2 model (`reports/intent/run2`, the checkpoint behind `model_report.md`; run 3 was a registered negative result and is not "
        "promoted). Score = maximum temperature-scaled class probability. Decision mode: the SAME confidence-ordered Wilson-lower-bound rule as for the LLM "
        "arms, evaluated in-sample on the matched ids (an operating point, not a deployable threshold). The deployable reading (threshold chosen on "
        "validation, stored in `eval_run2.json`) is given separately below.\n"
        "- LLM arms: verbalised integer confidence. Empty or unparseable answers count wrong at confidence 0 and stay in the denominator. Few-shot "
        "confidences are partly copied from the shots (confound, quantified below). The two local arms ran different Ollama builds.\n"
        "- Rule: operating point = highest-coverage threshold with Wilson 95 lower bound of precision at least the target, whole tie groups; "
        "paired item bootstrap with one index set per replicate for all arms. A verdict `beats` needs the bootstrap interval to exclude 0 AND the "
        "operating points to agree in direction; otherwise `not separated`.\n",
        _block(
            "moat20b_settings",
            f"target {s['target_wilson_lower']}; bootstrap {s['n_boot']} resamples, seed {s['seed']}; matched ids: first {s['n_matched_max']} sealed ids per task; sealed ids after ledger exclusions: {res['n_sealed_after_ledger_exclusions']}",
        ),
        "## Answer quality and confidence confounds (first 300 sealed ids)\n",
        _block("moat20b_row_stats", _stats_table(res)),
        "## Matched comparison per task (VERIFIED)\n",
    ]
    for t, e in res["tasks"].items():
        out += [
            f"### {t} (matched n = {e['n']})\n",
            _block(f"moat20b_{t}_arms", _arms_table(e)),
            "Matched-coverage reading (each arm at the coverage of the weakest arm that answers anything):\n",
            _block(f"moat20b_{t}_matched_coverage", _mc_table(e)),
            "Paired differences and registered verdicts:\n",
            _block(f"moat20b_{t}_pairs", _pair_table(e)),
        ]
    out += [
        "## Verdict summary under the registered rule (VERIFIED)\n",
        _block("moat20b_verdicts", _verdict_summary(res)),
        "## Encoder at its validation-chosen conservative threshold (deployable reading, matched ids; VERIFIED)\n",
        _block("moat20b_encoder_validation", _enc_thr_table(res)),
        "## Unmatched: zero-shot local on all sealed ids it covers vs the encoder (VERIFIED, UNMATCHED)\n",
        "Different n from the matched tables (all sealed ids with a zero-shot row and gold). Not used for the matched verdicts above; the few-shot arm "
        "stopped at 300 and is absent here.\n",
    ]
    for t, e in res["unmatched_zeroshot_vs_encoder"].items():
        out += [
            f"### {t} (unmatched, n = {e['n']})\n",
            _block(f"moat20b_unmatched_{t}", _arms_table(e) + "\n\n" + _pair_table(e)),
        ]
    out += [
        "## Cross-check: Groq gpt-oss-20b rows (VERIFIED, CROSS-CHECK ONLY, no verdicts)\n",
        "Groq rows are a different infrastructure (runtime, quantisation, sampling), kept read-only, and never mixed with the local arms. Shown on the matched ids "
        "they cover, with the same functions.\n",
    ]
    for t, e in res["groq_crosscheck"].items():
        out += [
            f"### {t} (cross-check, n = {e['n']})\n",
            _block(
                f"moat20b_groq_{t}", _arms_table(e) + "\n\n" + _pair_table(e, with_verdict=False)
            ),
        ]
    out += [
        "## gpt-oss-120b arms\n",
        "PENDING: needs a valid benchmark key; the previous key returns HTTP 401; no workaround attempted. Nothing about 120b is claimed here.\n",
        "## What this does and does not show\n",
        "- It shows (VERIFIED, above): under the pre-registered rule and on these matched silver-labelled items, which pairs separate on coverage at the "
        "Wilson-lower-bound operating point and on accuracy, with the intervals.\n"
        "- It does not show human-gold quality: labels are a four-judge panel consensus. It does not show 120b behaviour or any other model.\n"
        "- The sample is 300 sealed items for three arms (few-shot stopped there by the registered rule). Intervals are wide; a `not separated` verdict is "
        "absence of evidence of a difference, not evidence of equivalence (BELIEVED to be the usual reading for small n).\n"
        "- The operating point is chosen in-sample on the evaluated items for every arm, which flatters all arms; it is a symmetric comparison, not a "
        "deployable guarantee. The deployable encoder reading is the validation-threshold table.\n"
        "- LLM confidence is verbalised and coarse; with coarse ties the whole-tie-group rule makes coverage jump. The few-shot confidences copied from the "
        "shots and the truncated (empty) answers both depress the few-shot arm for reasons other than the shots' information content; those effects were "
        "not decomposed (BELIEVED to matter, not measured).\n"
        "- Local rows versus Groq rows differ in infrastructure; the cross-check does not isolate the model from the runtime.\n",
        "## Reproduce\n",
        "`python -m engine.intent.moat_compare20b --out-json reports/intent/moat_compare_20b.json --out-md reports/intent/moat_compare_20b.md` "
        "(needs the private row files on disk; paths and hashes are in the JSON).\n",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zs", type=Path, default=DEFAULTS["zs"])
    ap.add_argument("--fs", type=Path, default=DEFAULTS["fs"])
    ap.add_argument("--groq", type=Path, default=DEFAULTS["groq"])
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--out-md", type=Path, required=True)
    a = ap.parse_args()
    res = build(a.zs, a.fs, a.groq)
    a.out_json.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    a.out_md.write_text(render_md(res), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
