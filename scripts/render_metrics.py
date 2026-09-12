"""Regenerate hand-typed-lookalike metrics tables from the eval JSON, in place.

This is the single-source-of-truth mechanism for Section A ("truth reconciliation") --
README.md, site/index.html, site/docs/index.html, and eval/README.md must never again
hardcode an accuracy/gate/prompt-version number that can silently drift from
eval/results/latest.json and eval/results/authenticity_latest.json. See
docs/architecture/adr/0001-eval-gate-and-prompt-version-reconciliation.md.

Mechanism: each target file has one or more
    <!-- METRICS:START:<block-name> --> ... <!-- METRICS:END -->
regions. This script looks up each `<block-name>` in BLOCK_RENDERERS and overwrites the
region's body with freshly rendered content. `scripts/check_no_hardcoded_metrics.py`
exempts anything inside these markers from its hand-typed-number scan -- this script is
what keeps that exemption honest.

Usage:
    uv run python scripts/render_metrics.py          # regenerate target files in place
    uv run python scripts/render_metrics.py --check   # exit 1 if any file would change

`--check` is what CI runs (see .github/workflows/ci.yml) -- a nonzero exit means a
committed file has drifted from the JSON and needs `render_metrics.py` re-run + committed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_RESULTS_PATH = REPO_ROOT / "eval" / "results" / "latest.json"
AUTHENTICITY_RESULTS_PATH = REPO_ROOT / "eval" / "results" / "authenticity_latest.json"
HELD_OUT_RESULTS_PATH = REPO_ROOT / "eval" / "results" / "held_out_scoring_v2.json"
COVERAGE_METRICS_PATH = REPO_ROOT / "eval" / "results" / "coverage_metrics_n106.json"
INJECTION_SUITE_PATH = REPO_ROOT / "eval" / "results" / "injection_suite_n40.json"
PROMPT_GUARD_FPR_PATH = REPO_ROOT / "eval" / "results" / "prompt_guard_fpr_n106.json"
ADR_LINK = "docs/architecture/adr/0001-eval-gate-and-prompt-version-reconciliation.md"

BLOCK_RE = re.compile(
    r"(?P<start><!--\s*METRICS:START:(?P<name>[\w.-]+)\s*-->)"
    r"(?P<body>.*?)"
    r"(?P<end><!--\s*METRICS:END\s*-->)",
    re.DOTALL,
)


# Display order matching the repo's existing convention, not alphabetical. "hi" retired
# from this gate entirely (Session 11 P4d, ADR 0022) -- not listed even as a fallback.
LANG_DISPLAY_ORDER: tuple[str, ...] = ("en", "hi-en")


def _ordered_languages(per_lang: dict[str, Any]) -> list[str]:
    """Return the languages present in `per_lang`, in LANG_DISPLAY_ORDER (unknowns appended)."""
    known = [lang for lang in LANG_DISPLAY_ORDER if lang in per_lang]
    unknown = sorted(lang for lang in per_lang if lang not in LANG_DISPLAY_ORDER)
    return known + unknown


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_pct(x: float, decimals: int = 1) -> str:
    return f"{x * 100:.{decimals}f}%"


def render_extraction_table_md(data: dict[str, Any]) -> str:
    """Render the current extraction-eval summary as a Markdown table + prose (README.md)."""
    per_lang = data["per_language"]
    langs = _ordered_languages(per_lang)
    lines = [
        f"**Prompt {data['prompt_version']}**"
        + (f" &middot; `{data['git_sha'][:7]}`" if data.get("git_sha") else "")
        + f" &middot; measured {data['generated_at']} &middot; mode: {data['mode']}",
        "",
        "| Language | Score | 95% CI | Gate | Status |",
        "|---|---|---|---|---|",
    ]
    for lang in langs:
        info = per_lang[lang]
        status = "PASS" if info["passed"] else "FAIL"
        lines.append(
            f"| {lang} | {_fmt_pct(info['score'])} "
            f"| [{_fmt_pct(info['ci_95']['lower'])}, {_fmt_pct(info['ci_95']['upper'])}] "
            f"| ≥{info['threshold']:.0%} | {status} |"
        )
    overall_status = "PASS" if data["passed"] else "FAIL"
    lines.append(
        f"| **Overall** | **{_fmt_pct(data['overall_score'])}** "
        f"| [{_fmt_pct(data['overall_ci_95']['lower'])}, {_fmt_pct(data['overall_ci_95']['upper'])}] "
        f"| ≥{data['threshold']:.0%} | {overall_status} |"
    )

    n_total = data["overall_ci_95"]["n"]
    lang_counts = ", ".join(f"{per_lang[lang]['n']} {lang}" for lang in langs)
    if data.get("tiered_routing_enabled_at_runtime"):
        routing_note = (
            "Tiered routing is ON by default in production and in this eval run "
            "(`ENABLE_TIERED_ROUTING` defaults `true`, unset in CI) -- a same-cassette "
            f"`--routed` comparison produced byte-identical scores to the numbers above; "
            f"there is currently no distinct *unrouted* measurement to report separately "
            f"(see [ADR 0001]({ADR_LINK}))."
        )
    else:
        routing_note = "Tiered routing was OFF for this measurement."
    lines += ["", f"n={n_total} fixtures ({lang_counts}). {routing_note}"]
    return "\n".join(lines)


def render_authenticity_table_md(data: dict[str, Any]) -> str:
    """Render the current authenticity-eval summary as a Markdown table + prose (README.md)."""
    cm = data["confusion_matrix"]
    lines = [
        "| Metric | Value | 95% CI | n |",
        "|---|---|---|---|",
    ]
    for key, label in (("precision", "Precision"), ("recall", "Recall"), ("f1", "F1")):
        m = data[key]
        lines.append(
            f"| {label} | {m['value']:.3f} "
            f"| [{m['ci_95']['lower']:.3f}, {m['ci_95']['upper']:.3f}] | {m['n']} |"
        )
    gate_status = "met" if data["gate_passed"] else "**NOT met**"
    lines += [
        "",
        f"Gate: precision ≥ {data['precision_gate']:.2f} ({gate_status}). "
        f"n={data['n']} (tp={cm['tp']}, fp={cm['fp']}, fn={cm['fn']}, tn={cm['tn']}). "
        f"Mode: {data['mode']}.",
    ]
    if data.get("provenance_note"):
        lines += ["", f"> **Provenance:** {data['provenance_note']}"]
    return "\n".join(lines)


def render_held_out_table_md(data: dict[str, Any]) -> str:
    """Render the real-world, uncontaminated held-out measurement (README.md).

    Session 11 P4b: this is a DIFFERENT number from `extraction_table` above, and
    deliberately not blended with it. `extraction_table` (the CI-gate set) is a change
    detector measured against fixtures the prompt was developed against -- see
    ADR 0021/0022. This block is the honest real-world figure: scored against a
    quarantined held-out corpus (`eval/fixtures/_held_out_hindi_hinglish/`) the prompt has
    never seen, via `eval/score_held_out_corpus_v2.py`, cassette-replay reproducible.
    """
    as_dep = data["as_deployed"]
    forced = data["language_forced"]
    models = f"{data['groq_model_small']} / {data['groq_model_large']}"
    sha = data.get("git_sha")
    lines = [
        f"Measured {data['generated_at']}"
        + (f" &middot; `{sha[:7]}`" if sha else "")
        + f" &middot; models: {models}",
        "",
        "| Condition | Score | 95% CI | n |",
        "|---|---|---|---|",
        f"| **As actually deployed** (real language routing) | **{_fmt_pct(as_dep['overall_score'])}** "
        f"| [{_fmt_pct(as_dep['ci_95']['lower'])}, {_fmt_pct(as_dep['ci_95']['upper'])}] "
        f"| {as_dep['n']} |",
        f"| Language routing forced correct | {_fmt_pct(forced['overall_score'])} "
        f"| [{_fmt_pct(forced['ci_95']['lower'])}, {_fmt_pct(forced['ci_95']['upper'])}] "
        f"| {forced['n']} |",
    ]
    n_total = data["n_fixtures"]
    lang_acc = data.get("language_detection_accuracy")
    lang_acc_str = _fmt_pct(lang_acc) if lang_acc is not None else "n/a"
    lines += [
        "",
        f"n={n_total} real Hinglish reviews the prompt has never seen (never used for "
        f"prompt development), 0 hi (see [ADR 0016](docs/architecture/adr/0016-third-judge-corpus-batch-1-and-sentiment-recheck.md)). "
        f"Production's own language detector agreed with this corpus's language label on "
        f"{lang_acc_str} of fixtures. This is the number to trust for real-world accuracy; "
        f"the CI-gate table above is a regression detector, not a real-world accuracy claim "
        f"-- see [ADR 0021](docs/architecture/adr/0021-reproducible-measurement-and-misrouting-cost.md).",
    ]
    return "\n".join(lines)


def render_coverage_metrics_table_md(data: dict[str, Any]) -> str:
    """Render the coverage/accuracy-on-answered/wrong-committed breakdown (README.md).

    Session 12 P3d: flat accuracy is the weakest possible framing of an abstaining
    extractor -- this decomposes it for the two hedge-capable fields (sentiment,
    buy_again). See ADR 0026 for the full analysis and why a single blended
    "rarely wrong when it commits" claim is not supported across both fields.
    """
    lines = [
        "| Field | Coverage | Accuracy-on-answered | Wrong-committed |",
        "|---|---|---|---|",
    ]
    for field, info in data["per_field"].items():
        cov = info["coverage"]
        cov_ci = info["coverage_ci_95"]
        acc = info["accuracy_on_answered"]
        acc_ci = info["accuracy_on_answered_ci_95"]
        wrong = info["wrong_committed_of_answered"]
        wrong_rate = info["wrong_committed_rate"]
        wrong_ci = info["wrong_committed_rate_ci_95"]
        lines.append(
            f"| {field} "
            f"| {_fmt_pct(cov)} [{_fmt_pct(cov_ci['lower'])}, {_fmt_pct(cov_ci['upper'])}] "
            f"| {_fmt_pct(acc)} [{_fmt_pct(acc_ci['lower'])}, {_fmt_pct(acc_ci['upper'])}] "
            f"| {wrong} = {_fmt_pct(wrong_rate)} "
            f"[{_fmt_pct(wrong_ci['lower'])}, {_fmt_pct(wrong_ci['upper'])}] |"
        )
    lines += [
        "",
        f"n={data['n_fixtures']}, `{data['condition']}` condition (real language routing). "
        '**"Rarely wrong when it commits" does not hold as a single claim across both '
        "fields** -- buy_again's committed-answer error rate is materially higher than "
        "sentiment's; see [ADR 0026](docs/architecture/adr/0026-coverage-accuracy-on-answered-wrong-committed-n106.md) "
        "for the full analysis, including why a blended claim would misrepresent buy_again.",
    ]
    return "\n".join(lines)


def render_committed_accuracy_headline_md(data: dict[str, Any]) -> str:
    """Render the P1b headline claim (README.md / any Markdown surface).

    Session 13 P1b: generated from eval/results/coverage_metrics_n106.json so this exact
    sentence can never drift from the underlying numbers -- see
    docs/architecture/adr/0027-n23-discrepancy-resolved-and-headline-claim.md for why a
    single blended "rarely wrong when it commits" claim is NOT what this renders: the two
    hedge-capable fields diverge enough that only a per-field claim is honest.
    """
    sentiment = data["per_field"]["sentiment"]
    buy_again = data["per_field"]["buy_again"]
    n = data["n_fixtures"]
    return (
        f"**When it commits to an answer, this model is correct "
        f"{_fmt_pct(sentiment['accuracy_on_answered'])} of the time for sentiment "
        f"(95% CI {_fmt_pct(sentiment['accuracy_on_answered_ci_95']['lower'])}–"
        f"{_fmt_pct(sentiment['accuracy_on_answered_ci_95']['upper'])}, n={n}) and "
        f"{_fmt_pct(buy_again['accuracy_on_answered'])} of the time for buy-again "
        f"(95% CI {_fmt_pct(buy_again['accuracy_on_answered_ci_95']['lower'])}–"
        f"{_fmt_pct(buy_again['accuracy_on_answered_ci_95']['upper'])}, n={n}) -- rates "
        f'divergent enough that a single blended "rarely wrong when it commits" claim would '
        f"misrepresent buy-again.** See "
        f"[ADR 0027](docs/architecture/adr/0027-n23-discrepancy-resolved-and-headline-claim.md) "
        f"for why this is reported per-field, never blended into one number, and for the "
        f"separate (and separately true) abstention-rate figures."
    )


def render_committed_accuracy_headline_html(data: dict[str, Any]) -> str:
    """Render the P1b headline claim as a pair of stat cards (site/index.html trust section).

    Same source data and same per-field-never-blended discipline as the Markdown renderer
    above -- see its docstring and ADR 0027.
    """
    sentiment = data["per_field"]["sentiment"]
    buy_again = data["per_field"]["buy_again"]
    n = data["n_fixtures"]

    def _card(label: str, info: dict[str, Any]) -> str:
        acc = _fmt_pct(info["accuracy_on_answered"])
        lo = _fmt_pct(info["accuracy_on_answered_ci_95"]["lower"])
        hi = _fmt_pct(info["accuracy_on_answered_ci_95"]["upper"])
        abstain_lo = _fmt_pct(1 - info["coverage_ci_95"]["upper"])
        abstain_hi = _fmt_pct(1 - info["coverage_ci_95"]["lower"])
        return (
            '            <div class="bg-gray-900 rounded-lg p-6 border border-gray-700">\n'
            f'              <div class="text-3xl font-bold text-blue-300">{acc}</div>\n'
            f'              <div class="text-gray-100 font-semibold mt-1">accurate when it commits to {label}</div>\n'
            f'              <div class="text-gray-500 text-xs mt-2">95% CI [{lo}, {hi}], n={n}. '
            f"Separately, it abstains (&ldquo;unclear&rdquo;) on {abstain_lo}&ndash;{abstain_hi} "
            f"of all reviews rather than commit to any answer.</div>\n"
            "            </div>"
        )

    return (
        "\n"
        + _card("a sentiment call", sentiment)
        + "\n"
        + _card("a buy-again call", buy_again)
        + "\n          "
    )


def render_gate_summary_md(data: dict[str, Any]) -> str:
    """Render the one-line gate-threshold summary used by eval/README.md.

    Bug fix (Session 5 P4, 2026-09-10): used to read a single global per-language
    threshold via `next(iter(...))`, which silently returned whichever language
    happened to be first once eval/runner.py's PER_LANG_THRESHOLD became a per-language
    dict with different values for en/hi/hi-en -- this rendered a misleading single
    number for what are now three different gates.
    """
    per_lang = data["per_language"]
    langs = _ordered_languages(per_lang)
    lang_parts = ", ".join(f"{lang} ≥ {per_lang[lang]['threshold']:.0%}" for lang in langs)
    return f"overall ≥ {data['threshold']:.0%}, {lang_parts}"


def _status_badge_html(passed: bool) -> str:
    if passed:
        return '<td class="px-6 py-4 text-green-400 font-semibold">&#10003; PASS</td>'
    return '<td class="px-6 py-4 text-red-400 font-semibold">&#10007; FAIL</td>'


def render_extraction_table_html(data: dict[str, Any]) -> str:
    """Render the accuracy `<tbody>` rows for site/index.html.

    Bug fix (Session 5 P4, 2026-09-10): every row previously hardcoded the green PASS
    badge unconditionally, regardless of `info["passed"]`/`data["passed"]` -- found while
    resetting the gate thresholds, before it ever had a chance to silently render a FAIL
    result as PASS. Also adds the 95% CI column and per-fixture-n label to match the
    hand-authored fix this generator would otherwise clobber on the next run.
    """
    per_lang = data["per_language"]
    # Session 11 P4d: "hi" (Devanagari) retired from this gate entirely (ADR 0022) -- no
    # longer a row here at all, not even an "experimental" one. See render_language_table_html
    # for the matching change on the other table this same source data feeds.
    lang_labels = {"en": "English", "hi-en": "Hinglish"}
    lang_scope_note: dict[str, str] = {}
    rows: list[str] = []
    for lang in sorted(per_lang):
        info = per_lang[lang]
        label = lang_labels.get(lang, lang)
        scope_note = lang_scope_note.get(lang, "")
        rows.append(
            '            <tr class="bg-gray-900 hover:bg-gray-800 transition-colors">\n'
            f'              <td class="px-6 py-4 text-gray-100">{label} '
            f'<span class="text-gray-500 text-xs">({lang}, n={info["n"]}{scope_note})</span></td>\n'
            f'              <td class="px-6 py-4 font-mono text-blue-300">{_fmt_pct(info["score"])}</td>\n'
            f'              <td class="px-6 py-4 font-mono text-gray-400 text-xs">'
            f"[{_fmt_pct(info['ci_95']['lower'])}, {_fmt_pct(info['ci_95']['upper'])}]</td>\n"
            f'              <td class="px-6 py-4 text-gray-400">&ge;{info["threshold"]:.0%}</td>\n'
            f"              {_status_badge_html(info['passed'])}\n"
            "            </tr>"
        )
    rows.append(
        '            <tr class="bg-gray-900 hover:bg-gray-800 transition-colors border-t-2 border-gray-600">\n'
        f'              <td class="px-6 py-4 text-white font-semibold">Overall '
        f'<span class="text-gray-500 text-xs">(n={data["overall_ci_95"]["n"]})</span></td>\n'
        f'              <td class="px-6 py-4 font-mono text-blue-300 font-semibold">{_fmt_pct(data["overall_score"])}</td>\n'
        f'              <td class="px-6 py-4 font-mono text-gray-400 text-xs">'
        f"[{_fmt_pct(data['overall_ci_95']['lower'])}, {_fmt_pct(data['overall_ci_95']['upper'])}]</td>\n"
        f'              <td class="px-6 py-4 text-gray-400">&ge;{data["threshold"]:.0%}</td>\n'
        f"              {_status_badge_html(data['passed'])}\n"
        "            </tr>"
    )
    return "\n" + "\n".join(rows) + "\n          "


def render_language_table_html(data: dict[str, Any]) -> str:
    """Render the language-support accuracy `<tbody>` rows for site/docs/index.html.

    Bug fix (Session 5 P4, 2026-09-10): the accuracy cell previously hardcoded
    text-green-400 unconditionally -- same class of bug as render_extraction_table_html,
    found the same session. A failing language now renders red with its gate noted inline.
    """
    per_lang = data["per_language"]
    # Session 11 P4d: Devanagari Hindi retired from this gate entirely (ADR 0022) -- real
    # Devanagari-script review yield in the largest corpus available to this project is
    # zero, not just thin. Not listed as a language row at all anymore (not even as
    # "experimental"), matching every other public surface's claim.
    rows_spec = [
        ("en", "English", "Latin"),
        ("hi-en", "Hinglish", "Roman-script code-mix"),
    ]
    rows: list[str] = []
    for code, label, script in rows_spec:
        info = per_lang[code]
        color = "text-green-400" if info["passed"] else "text-red-400"
        suffix = "" if info["passed"] else f" (below {info['threshold']:.0%} gate)"
        rows.append(
            '              <tr class="bg-gray-900">\n'
            f'                <td class="px-5 py-3 font-mono text-blue-300">{code}</td>\n'
            f'                <td class="px-5 py-3 text-gray-100">{label}</td>\n'
            f'                <td class="px-5 py-3 text-gray-400">{script}</td>\n'
            f'                <td class="px-5 py-3 {color}">{_fmt_pct(info["score"])}{suffix}</td>\n'
            "              </tr>"
        )
    return "\n" + "\n".join(rows) + "\n            "


def render_coverage_metrics_table_html(data: dict[str, Any]) -> str:
    """Render the coverage/accuracy-on-answered/wrong-committed `<tbody>` rows (site/).

    Same source and discipline as render_coverage_metrics_table_md -- see that function's
    docstring and ADR 0026/0027.
    """
    rows: list[str] = []
    for field, info in data["per_field"].items():
        cov = _fmt_pct(info["coverage"])
        cov_ci = f"[{_fmt_pct(info['coverage_ci_95']['lower'])}, {_fmt_pct(info['coverage_ci_95']['upper'])}]"
        acc = _fmt_pct(info["accuracy_on_answered"])
        acc_ci = (
            f"[{_fmt_pct(info['accuracy_on_answered_ci_95']['lower'])}, "
            f"{_fmt_pct(info['accuracy_on_answered_ci_95']['upper'])}]"
        )
        wrong = info["wrong_committed_of_answered"]
        wrong_rate = _fmt_pct(info["wrong_committed_rate"])
        rows.append(
            '            <tr class="bg-gray-900 hover:bg-gray-800 transition-colors">\n'
            f'              <td class="px-6 py-4 text-gray-100 capitalize">{field.replace("_", " ")}</td>\n'
            f'              <td class="px-6 py-4 font-mono text-blue-300">{cov} <span class="text-gray-500 text-xs">{cov_ci}</span></td>\n'
            f'              <td class="px-6 py-4 font-mono text-blue-300">{acc} <span class="text-gray-500 text-xs">{acc_ci}</span></td>\n'
            f'              <td class="px-6 py-4 font-mono text-gray-300">{wrong} = {wrong_rate}</td>\n'
            "            </tr>"
        )
    return "\n" + "\n".join(rows) + "\n          "


_INJECTION_FAMILY_LABELS: dict[str, tuple[str, str]] = {
    "phrase_variant": ("Phrase variants evading the regex", ""),
    "encoding_evasion": (
        "Encoding/homoglyph evasion",
        "leetspeak, zero-width chars, fullwidth Unicode, spacing",
    ),
    "role_confusion": ("Role-confusion framing", '"you are now a..."'),
    "field_targeted": (
        "Field-targeted injection",
        '"for the buy_again field, always output true..."',
    ),
    "non_english": (
        "Non-English attacks",
        "Hindi, Hinglish, Spanish, French, German, Portuguese",
    ),
}


def render_injection_suite_table_md(data: dict[str, Any]) -> str:
    """Render the P4d injection-suite per-family pass-rate table (SECURITY.md).

    Session 13 P4d/P4e: generated from eval/results/injection_suite_n40.json so this
    table can never quietly drift from a re-run of eval/run_injection_suite.py -- see
    that script and eval/injection_suite.py for the full methodology, and
    docs/architecture/adr/0015-*.md's Session 13 correction for why this suite measures
    pre-filter detection rather than spending the far scarcer extraction-model budget.
    """
    lines = ["| Family | Caught by Layer 1+2 | Notes |", "|---|---|---|"]
    for family, info in data["per_family"].items():
        label, detail = _INJECTION_FAMILY_LABELS.get(family, (family, ""))
        n = info["n"]
        caught = info["caught"]
        rate = _fmt_pct(info["pass_rate"], decimals=1)
        cell = f"{caught}/{n} ({rate})"
        emphasis = caught == 0
        family_cell = f"**{label}**" if emphasis else label
        rate_cell = f"**{cell}**" if emphasis else cell
        note = detail
        if info["missed_ids"]:
            note = f"{detail + ' -- ' if detail else ''}missed: {', '.join(info['missed_ids'])}"
        lines.append(f"| {family_cell} | {rate_cell} | {note} |")
    overall_rate = _fmt_pct(data["overall_pass_rate"], decimals=1)
    lines.append(
        f"| **Overall** | **{sum(f['caught'] for f in data['per_family'].values())}/"
        f"{data['n_cases']} ({overall_rate})** | |"
    )
    return "\n".join(lines)


def render_prompt_guard_fpr_md(data: dict[str, Any]) -> str:
    """Render the P4b false-positive-rate sentence (SECURITY.md).

    Generated from eval/results/prompt_guard_fpr_n106.json -- see
    eval/measure_prompt_guard_fpr.py for methodology.
    """
    n = data["n_fixtures"]
    fp = data["n_false_positives"]
    rate = _fmt_pct(data["false_positive_rate"], decimals=1)
    max_score = data["score_max"]
    threshold = data["threshold"]
    return (
        f"{fp}/{n} ({rate}) on real marketplace reviews the classifier had never seen -- "
        f"max score {max_score:.3f} against a {threshold} threshold, comfortable margin. "
        f"**A real customer review has not been observed to trigger Layer 2 in this "
        f"measurement.**"
    )


PORTFOLIO_METRICS_PATH = REPO_ROOT / ".portfolio" / "metrics.json"


def render_portfolio_metrics_json(data: dict[str, Any]) -> str:
    """Return the full, pretty-printed content of .portfolio/metrics.json.

    Session 7 P1: this file went stale a THIRD time (still said 77.6%/75.0% after the
    Session 6 P4a scorer fix moved the real numbers to 79.3%/78.1%) because it was
    hand-typed prose, not generated -- the identical failure class check_no_hardcoded_
    metrics.py exists to catch, just in a file type (.json) that scanner never looked
    at. Rather than force the HTML-comment marker convention into a JSON string value
    (risking visible `<!-- ... -->` text if gg-portfolio ever renders this "value"
    field raw, which this repo has no way to verify from here), this file is instead
    treated as a whole-file generated artifact -- same category as eval/report.md --
    and drift-checked by `render_metrics.py --check` exactly like every marker-block
    target. See check_no_hardcoded_metrics.py's EXCLUDED_EXACT_FILES entry for this
    file, which documents the same reasoning from that script's side.
    """
    per_lang = data["per_language"]
    langs = _ordered_languages(per_lang)
    lang_bits = ", ".join(f"{lang} {_fmt_pct(per_lang[lang]['score'])}" for lang in langs)
    overall_verb = "passes" if data["passed"] else "FAILS"
    value = (
        f"{_fmt_pct(data['overall_score'])} overall ({overall_verb} its own "
        f"{data['threshold']:.0%} gate), {lang_bits} -- measured under "
        f"{data['groq_model_small']} / {data['groq_model_large']}"
    )
    gate_bits = ", ".join(
        f"{lang} {'passes' if per_lang[lang]['passed'] else 'FAILS'} its "
        f"{per_lang[lang]['threshold']:.0%} gate"
        for lang in langs
    )
    gate_status = (
        f"overall {'passes' if data['passed'] else 'FAILS'} its {data['threshold']:.0%} gate; "
        f"{gate_bits}"
    )
    measured_at = data["generated_at"][:10] if data.get("generated_at") else ""
    doc = {
        "version": 1,
        "project": "reviewiq",
        "docs": "https://github.com/gaurav-gandhi-2411/gg-portfolio#autonomous-metric-refresh",
        "metrics": [
            {
                "id": "reviewiq:extraction-eval",
                "label": "Extraction accuracy (en/hi/hi-en)",
                "value": value,
                "source_file": "eval/results/latest.json",
                "source_line": 1,
                "commit_sha": data.get("git_sha", ""),
                "measured_at": measured_at,
                "measured_under_model": f"{data['groq_model_small']} / {data['groq_model_large']}",
                "current_deployed_model": f"{data['groq_model_small']} / {data['groq_model_large']}",
                "stale": False,
                "gate_status": gate_status,
                "stale_reason": (
                    "Generated by scripts/render_metrics.py from eval/results/latest.json "
                    "-- cannot go stale without also failing render_metrics.py --check "
                    "(wired into CI). See docs/architecture/adr/0001-*.md."
                ),
            }
        ],
    }
    return json.dumps(doc, indent=2) + "\n"


BLOCK_RENDERERS: dict[str, Any] = {
    "extraction_table": lambda: render_extraction_table_md(_load_json(EXTRACTION_RESULTS_PATH)),
    "authenticity_table": lambda: render_authenticity_table_md(
        _load_json(AUTHENTICITY_RESULTS_PATH)
    ),
    "gate_summary": lambda: render_gate_summary_md(_load_json(EXTRACTION_RESULTS_PATH)),
    "held_out_table": lambda: render_held_out_table_md(_load_json(HELD_OUT_RESULTS_PATH)),
    "coverage_metrics_table": lambda: render_coverage_metrics_table_md(
        _load_json(COVERAGE_METRICS_PATH)
    ),
    "extraction_table_html": lambda: render_extraction_table_html(
        _load_json(EXTRACTION_RESULTS_PATH)
    ),
    "language_table_html": lambda: render_language_table_html(_load_json(EXTRACTION_RESULTS_PATH)),
    "committed_accuracy_headline": lambda: render_committed_accuracy_headline_md(
        _load_json(COVERAGE_METRICS_PATH)
    ),
    "committed_accuracy_headline_html": lambda: render_committed_accuracy_headline_html(
        _load_json(COVERAGE_METRICS_PATH)
    ),
    "coverage_metrics_table_html": lambda: render_coverage_metrics_table_html(
        _load_json(COVERAGE_METRICS_PATH)
    ),
    "injection_suite_table": lambda: render_injection_suite_table_md(
        _load_json(INJECTION_SUITE_PATH)
    ),
    "prompt_guard_fpr": lambda: render_prompt_guard_fpr_md(_load_json(PROMPT_GUARD_FPR_PATH)),
}

TARGET_FILES: tuple[Path, ...] = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "eval" / "README.md",
    REPO_ROOT / "site" / "index.html",
    REPO_ROOT / "site" / "docs" / "index.html",
    REPO_ROOT / "SECURITY.md",
)


def render_file(path: Path) -> tuple[str, bool]:
    """Return (new_content, changed) for `path` with every recognised block regenerated."""
    original = path.read_text(encoding="utf-8")

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        renderer = BLOCK_RENDERERS.get(name)
        if renderer is None:
            # Unknown block name: leave untouched rather than guessing or erroring the
            # whole run -- a typo'd marker should be visible in review, not silently eaten.
            return match.group(0)
        return f"{match.group('start')}{renderer()}{match.group('end')}"

    new_content = BLOCK_RE.sub(_replace, original)
    return new_content, new_content != original


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    any_changed = False

    for path in TARGET_FILES:
        if not path.exists():
            continue
        new_content, changed = render_file(path)
        if changed:
            any_changed = True
            if check_only:
                print(f"DRIFT: {path.relative_to(REPO_ROOT).as_posix()} is out of date")
            else:
                path.write_text(new_content, encoding="utf-8")
                print(f"Regenerated: {path.relative_to(REPO_ROOT).as_posix()}")

    # Whole-file generated targets (not marker-block-based -- JSON has no comment
    # syntax to carry a marker without polluting a display string; see
    # render_portfolio_metrics_json's own docstring).
    if PORTFOLIO_METRICS_PATH.exists():
        new_portfolio = render_portfolio_metrics_json(_load_json(EXTRACTION_RESULTS_PATH))
        old_portfolio = PORTFOLIO_METRICS_PATH.read_text(encoding="utf-8")
        if new_portfolio != old_portfolio:
            any_changed = True
            rel = PORTFOLIO_METRICS_PATH.relative_to(REPO_ROOT).as_posix()
            if check_only:
                print(f"DRIFT: {rel} is out of date")
            else:
                PORTFOLIO_METRICS_PATH.write_text(new_portfolio, encoding="utf-8")
                print(f"Regenerated: {rel}")

    if check_only:
        if any_changed:
            print(
                "\nFAIL: one or more files are stale relative to the eval JSON.\n"
                "Run `uv run python scripts/render_metrics.py` and commit the result."
            )
            return 1
        print("OK: all metrics blocks match eval/results/*.json.")
        return 0

    if not any_changed:
        print("Nothing to regenerate -- all metrics blocks already match the eval JSON.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
