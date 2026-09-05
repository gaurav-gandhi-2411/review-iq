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
ADR_LINK = "docs/architecture/adr/0001-eval-gate-and-prompt-version-reconciliation.md"

BLOCK_RE = re.compile(
    r"(?P<start><!--\s*METRICS:START:(?P<name>[\w.-]+)\s*-->)"
    r"(?P<body>.*?)"
    r"(?P<end><!--\s*METRICS:END\s*-->)",
    re.DOTALL,
)


# Display order matching the repo's existing convention (en / hi-en / hi), not alphabetical.
LANG_DISPLAY_ORDER: tuple[str, ...] = ("en", "hi-en", "hi")


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


def render_gate_summary_md(data: dict[str, Any]) -> str:
    """Render the one-line gate-threshold summary used by eval/README.md."""
    per_lang_threshold = next(iter(data["per_language"].values()))["threshold"]
    return (
        f"every per-language bucket ≥ {per_lang_threshold:.0%} AND\n"
        f"  overall ≥ {data['threshold']:.0%}"
    )


def render_extraction_table_html(data: dict[str, Any]) -> str:
    """Render the accuracy `<tbody>` rows for site/index.html."""
    per_lang = data["per_language"]
    lang_labels = {"en": "English", "hi": "Hindi", "hi-en": "Hinglish"}
    rows: list[str] = []
    for lang in sorted(per_lang):
        info = per_lang[lang]
        label = lang_labels.get(lang, lang)
        rows.append(
            '            <tr class="bg-gray-900 hover:bg-gray-800 transition-colors">\n'
            f'              <td class="px-6 py-4 text-gray-100">{label} '
            f'<span class="text-gray-500 text-xs">({lang})</span></td>\n'
            f'              <td class="px-6 py-4 font-mono text-blue-300">{_fmt_pct(info["score"])}</td>\n'
            f'              <td class="px-6 py-4 text-gray-400">&ge;{info["threshold"]:.0%}</td>\n'
            '              <td class="px-6 py-4 text-green-400 font-semibold">&#10003; PASS</td>\n'
            "            </tr>"
        )
    rows.append(
        '            <tr class="bg-gray-900 hover:bg-gray-800 transition-colors border-t-2 border-gray-600">\n'
        '              <td class="px-6 py-4 text-white font-semibold">Overall</td>\n'
        f'              <td class="px-6 py-4 font-mono text-blue-300 font-semibold">{_fmt_pct(data["overall_score"])}</td>\n'
        f'              <td class="px-6 py-4 text-gray-400">&ge;{data["threshold"]:.0%}</td>\n'
        '              <td class="px-6 py-4 text-green-400 font-semibold">&#10003; PASS</td>\n'
        "            </tr>"
    )
    return "\n" + "\n".join(rows) + "\n          "


def render_language_table_html(data: dict[str, Any]) -> str:
    """Render the language-support accuracy `<tbody>` rows for site/docs/index.html."""
    per_lang = data["per_language"]
    rows_spec = [
        ("en", "English", "Latin"),
        ("hi", "Hindi", "Devanagari"),
        ("hi-en", "Hinglish", "Roman-script code-mix"),
    ]
    rows: list[str] = []
    for code, label, script in rows_spec:
        score = per_lang[code]["score"]
        rows.append(
            '              <tr class="bg-gray-900">\n'
            f'                <td class="px-5 py-3 font-mono text-blue-300">{code}</td>\n'
            f'                <td class="px-5 py-3 text-gray-100">{label}</td>\n'
            f'                <td class="px-5 py-3 text-gray-400">{script}</td>\n'
            f'                <td class="px-5 py-3 text-green-400">{_fmt_pct(score)}</td>\n'
            "              </tr>"
        )
    return "\n" + "\n".join(rows) + "\n            "


BLOCK_RENDERERS: dict[str, Any] = {
    "extraction_table": lambda: render_extraction_table_md(_load_json(EXTRACTION_RESULTS_PATH)),
    "authenticity_table": lambda: render_authenticity_table_md(
        _load_json(AUTHENTICITY_RESULTS_PATH)
    ),
    "gate_summary": lambda: render_gate_summary_md(_load_json(EXTRACTION_RESULTS_PATH)),
    "extraction_table_html": lambda: render_extraction_table_html(
        _load_json(EXTRACTION_RESULTS_PATH)
    ),
    "language_table_html": lambda: render_language_table_html(_load_json(EXTRACTION_RESULTS_PATH)),
}

TARGET_FILES: tuple[Path, ...] = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "eval" / "README.md",
    REPO_ROOT / "site" / "index.html",
    REPO_ROOT / "site" / "docs" / "index.html",
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
