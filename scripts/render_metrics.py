"""Regenerate hand-typed-lookalike metrics tables from the eval JSON, in place.

This is the single-source-of-truth mechanism for Section A ("truth reconciliation") --
README.md, site/index.html, site/docs/index.html, and eval/README.md must never again
hardcode an accuracy/gate/prompt-version number that can silently drift from
eval/results/latest.json. (The fake-review flag has no measurable eval -- no authenticity
labels exist for the held-out set, and eval/results/authenticity_latest.json is a historical,
non-reproducible in-sample file -- so no block renders it.) See
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
KNOWN_GAPS_PATH = REPO_ROOT / "eval" / "results" / "known_gaps_n106.json"
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


def render_headline_accuracy(data: dict[str, Any]) -> str:
    """The one sanctioned display of headline accuracy (GG display rule, 2026-10-05).

    Always "78.6% (95% CI 73-83%, n=43, eval 2026-09-19)" -- point estimate, whole-percent CI,
    sample size and eval date together, never a bare number and never split per language.
    Everything is read from the eval JSON so it cannot be hand-typed or drift.
    """
    ci = data["overall_ci_95"]
    return (
        f"{_fmt_pct(data['overall_score'])} "
        f"(95% CI {ci['lower'] * 100:.0f}–{ci['upper'] * 100:.0f}%, "
        f"n={ci['n']}, eval {data['generated_at'][:10]})"
    )


def render_extraction_table_md(data: dict[str, Any]) -> str:
    """Render the current extraction-eval summary as the headline line + prose (README.md).

    Name kept for the `extraction_table` marker block. Per-language scores are deliberately
    not rendered: n=27/n=16 slices carry CIs too wide to headline (hi-en 66.8%-87.7%).
    """
    per_lang = data["per_language"]
    langs = _ordered_languages(per_lang)
    overall_status = "PASS" if data["passed"] else "FAIL"
    lines = [
        f"**Prompt {data['prompt_version']}**"
        + (f" &middot; `{data['git_sha'][:7]}`" if data.get("git_sha") else "")
        + f" &middot; mode: {data['mode']}",
        "",
        f"**Headline accuracy: {render_headline_accuracy(data)}** -- "
        f"CI gate ≥{data['threshold']:.0%}: {overall_status}.",
    ]

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


def _label_agreement_text(data: dict[str, Any]) -> str:
    """The one sanctioned phrasing of the detector-vs-corpus-label figure (never "accuracy").

    The corpus label is noisy (inter-rater alpha 0.380), so the agreement partly measures label
    noise rather than detector error. Rendered from the artifact so the number, its CI and the
    alpha cannot drift apart or be hand-typed.
    """
    agreement = data.get("language_label_agreement")
    if agreement is None:
        return "n/a"
    ci = data["language_label_agreement_wilson_95"]
    alpha = data["language_label_alpha"]
    return (
        f"agreement with the corpus label {_fmt_pct(agreement)} "
        f"(95% CI {ci['lower'] * 100:.1f}-{ci['upper'] * 100:.1f}; label alpha {alpha:.3f}, "
        "so this partly measures label noise, not detector error)"
    )


def _render_held_out_grid_md(data: dict[str, Any]) -> str:
    """Held-out block once the artifact carries the Session 16 exposure x split-gold grid.

    Rule 65c: the published headline here is HIGHER than the one it replaces, so every cell of the
    grid is printed beside it, the field that goes DOWN is named, and the exposed-vs-unexposed
    comparison is shown -- all rendered from the one artifact, none hand-typed.
    """
    grid = data["headline_grid"]
    pub = grid[data["headline_policy"]["cell"]]
    sha = data.get("git_sha")
    models = f"{data['groq_model_small']} / {data['groq_model_large']}"
    un = data["unscored"]
    unscored_cell = (
        f"{un['n_pairs_unscored']} of {un['n_pairs']} ({_fmt_pct(un['unscored_fraction'])})"
    )
    lo_b = un["bounds"]["lower_unscored_all_wrong"]["score"]
    hi_b = un["bounds"]["upper_unscored_all_correct"]["score"]
    pros = data["per_field_split_effect"]["pros"]
    lines = [
        f"Measured {data['generated_at']}"
        + (f" &middot; `{sha[:7]}`" if sha else "")
        + f" &middot; models: {models}",
        "",
        # Session 17 (W3): the unscored fraction sits BESIDE the number it qualifies. The score
        # is accuracy where the panel agreed -- split pairs are the hardest reviews -- so it is
        # not overall accuracy and must not be read as such.
        f"**{_fmt_pct(pub['as_deployed']['score'])} "
        f"[{_fmt_pct(pub['as_deployed']['ci_95']['lower'])}, "
        f"{_fmt_pct(pub['as_deployed']['ci_95']['upper'])}] on {pub['n']} unseen reviews, scored "
        f"where the three-judge panel reached consensus; {_fmt_pct(un['unscored_fraction'])} of "
        f"field-pairs ({un['n_pairs_unscored']} of {un['n_pairs']}) had no consensus and are "
        "unscored.**",
        "",
        "| Condition | Score (headline fields) | 95% CI | n | Field-pairs unscored |",
        "|---|---|---|---|---|",
        f"| **As actually deployed** (real language routing) | "
        f"**{_fmt_pct(pub['as_deployed']['score'])}** "
        f"| [{_fmt_pct(pub['as_deployed']['ci_95']['lower'])}, "
        f"{_fmt_pct(pub['as_deployed']['ci_95']['upper'])}] | {pub['n']} | {unscored_cell} |",
        f"| Language routing forced correct | {_fmt_pct(pub['language_forced']['score'])} "
        f"| [{_fmt_pct(pub['language_forced']['ci_95']['lower'])}, "
        f"{_fmt_pct(pub['language_forced']['ci_95']['upper'])}] | {pub['n']} | {unscored_cell} |",
        "",
        "**This is accuracy where the panel agreed, not overall accuracy.** A *field-pair* is one "
        f"(review, field) cell: {pub['n']} reviews x {len(un['fields'])} fields = {un['n_pairs']}. "
        "A pair is unscored when the three judges split and the stored gold is a default, not a "
        "label. Those are the reviews the panel found hardest or most ambiguous, so scoring only "
        "the agreed pairs is a selection effect: it is why `pros` reads "
        f"{_fmt_pct(pros['score_excluding_split'])} with them excluded against "
        f"{_fmt_pct(pros['score_all_pairs'])} with the defaults scored (all reviews). "
        f"{un['n_reviews_with_any_unscored_pair']} of {un['n_reviews']} reviews have at least one "
        "unscored pair. **No point estimate of overall accuracy is identifiable from this "
        "data.** The assumption-free interval (every unscored pair all wrong / all right) is "
        f"[{_fmt_pct(lo_b)}, {_fmt_pct(hi_b)}]; that is a bound, not a result.",
        "",
        "**What this headline is.** The average of "
        f"{len(data['headline_fields'])} fields ({', '.join(f'`{f}`' for f in data['headline_fields'])}) "
        f"over the {pub['n']} of {data['n_fixtures']} corpus reviews that the prompt-development "
        f"process had **not** seen, with {pub['n_split_pairs_excluded']} gold "
        "(review, field) pairs excluded because the judge panel split and the stored gold was a "
        "default, not a label. `stars` (null everywhere) and `language` (an echo of the "
        f"detector, {_label_agreement_text(data)}) are excluded as before.",
        "",
        "**Every cell, same recorded model outputs, as deployed** (the change moves the number up, "
        "so all four are shown):",
        "",
        "| Reviews | Gold pairs | Score | 95% CI | n |",
        "|---|---|---|---|---|",
    ]
    labels = {
        "all_reviews_all_pairs": ("all", "all"),
        "all_reviews_split_excluded": ("all", "split excluded"),
        "unexposed_all_pairs": ("unseen only", "all"),
        "unexposed_split_excluded": ("**unseen only (headline)**", "**split excluded**"),
    }
    for key, (who, which) in labels.items():
        c = grid[key]["as_deployed"]
        lines.append(
            f"| {who} | {which} | {_fmt_pct(c['score'])} "
            f"| [{_fmt_pct(c['ci_95']['lower'])}, {_fmt_pct(c['ci_95']['upper'])}] "
            f"| {grid[key]['n']} |"
        )
    reasons = data["exposure_reasons"]
    sens = data.get("exposure_sensitivity", {})
    lines += [
        "",
        f"**Why {data['n_exposed_reviews']} reviews are excluded.** "
        f"{reasons.get('prompt_visible_dev_fixture', 0)} also appear in the prompt-visible "
        "development fixtures (`eval/fixtures/hi-en/`; four of the `hi_en` prompt's few-shot "
        f"examples are rewrites of them) and {reasons.get('benchmark_gold', 0)} in the "
        "internal benchmark whose adjudicated labels accepted prompt v2.2/v2.3. The builder "
        "only excluded already-quarantined text, so nothing stopped this "
        "([ADR 0032](docs/architecture/adr/0032-held-out-exposure-and-split-gold.md)). "
        + (
            f"Exposed reviews score {_fmt_pct(sens['exposed_score'])} vs "
            f"{_fmt_pct(sens['unexposed_score'])} for unseen ones (difference "
            f"{sens['difference'] * 100:+.1f} pp, 95% CI {sens['difference_ci_95']['lower'] * 100:+.1f}"
            f" to {sens['difference_ci_95']['upper'] * 100:+.1f}): no benefit is detectable at "
            "this sample size, but that is absence of evidence, not proof of none."
            if "difference" in sens
            else ""
        ),
        "",
        "**Effect of excluding split gold, per field** (as deployed, all reviews; one field goes "
        "down):",
        "",
        "| Field | Split-gold pairs | Score, all pairs | Score, split excluded |",
        "|---|---|---|---|",
    ]
    for field, info in data["per_field_split_effect"].items():
        excl = info["score_excluding_split"]
        lines.append(
            f"| `{field}` | {info['n_split_gold']} | {_fmt_pct(info['score_all_pairs'])} "
            f"| {'n/a' if excl is None else _fmt_pct(excl)} |"
        )
    lines += [
        "",
        f"**Unscored field-pairs and bounds, per field** (as deployed, the {un['n_reviews']} "
        "unseen reviews; each bound gives every unscored pair of that field the score 0 or 1):",
        "",
        "| Field | Unscored pairs | Unscored | Score on scored pairs | Bound, unscored all wrong "
        "| Bound, unscored all right |",
        "|---|---|---|---|---|---|",
    ]
    for field, info in un["per_field"].items():
        b = un["bounds"]["per_field"][field]
        lines.append(
            f"| `{field}` | {info['unscored']} of {info['pairs']} "
            f"| {_fmt_pct(info['unscored_fraction'])} | {_fmt_pct(b['scored_only'])} "
            f"| {_fmt_pct(b['lower_unscored_all_wrong'])} "
            f"| {_fmt_pct(b['upper_unscored_all_correct'])} |"
        )
    ob = un["bounds"]
    lines.append(
        f"| **all {len(un['fields'])} (per-review mean)** | **{un['n_pairs_unscored']} of "
        f"{un['n_pairs']}** | **{_fmt_pct(un['unscored_fraction'])}** "
        f"| **{_fmt_pct(un['scored_only']['score'])}** "
        f"| **{_fmt_pct(ob['lower_unscored_all_wrong']['score'])}** "
        f"| **{_fmt_pct(ob['upper_unscored_all_correct']['score'])}** |"
    )
    js = un.get("judge_sensitivity")
    if js:
        per_judge = [v["score"] for v in js["scored_against_each_judge"].values()]
        lines += [
            "",
            "**Sensitivity, conditional on an assumption that is not verified** (the true label of "
            "an unscored pair is one of the three judges' own answers): scoring each unscored "
            f"pair against each judge in turn gives {_fmt_pct(min(per_judge))} to "
            f"{_fmt_pct(max(per_judge))} overall, and the per-pair worst/best over the judges "
            f"gives [{_fmt_pct(js['envelope_min_over_judges']['score'])}, "
            f"{_fmt_pct(js['envelope_max_over_judges']['score'])}] -- all below the headline. "
            "A sensitivity analysis, not an estimate: all three judges can be wrong, and the "
            "unscored pairs have never been adjudicated.",
        ]
    lines += [
        "",
        "Gold labels are LLM-consensus silver, not human ground truth. Production's own language "
        "detector, measured against this corpus's language label: "
        f"{_label_agreement_text(data)}. That agreement figure is the number to trust for "
        "routing; the held-out headline above is real-world extraction accuracy only where the "
        "panel agreed, and the CI-gate table above is a regression detector, not a real-world "
        "accuracy claim -- see "
        "[ADR 0021](docs/architecture/adr/0021-reproducible-measurement-and-misrouting-cost.md) "
        "(its misrouting-cost finding was corrected in Session 15d).",
    ]
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
    if "headline_grid" in data:
        return _render_held_out_grid_md(data)
    as_dep = data["as_deployed"]
    forced = data["language_forced"]
    models = f"{data['groq_model_small']} / {data['groq_model_large']}"
    sha = data.get("git_sha")
    constant = data.get("constant_fields") or []
    # Session 15d (D7): the headline also excludes echo/label-noise fields (`language`).
    echo = data.get("headline_echo_fields") or []
    if constant or echo:
        # The headline excludes fields that carry no information (constant across the whole
        # corpus): a field that scores 100% on every review adds a free 100% to one of the
        # equal-weighted fields and flatters the overall. Indexed directly, not .get(): an
        # artifact that names excluded fields but lacks the matching score/CI must fail loudly
        # rather than silently fall back to the flattering all-fields figure.
        if echo:
            excl = data["overall_score_headline"]
            excl_ci = data["overall_score_headline_ci_95"]
        else:
            excl = data["overall_score_excluding_constant_fields"]
            excl_ci = data["overall_score_excluding_constant_fields_ci_95"]
        head = {
            "as_deployed": (excl["as_deployed"], excl_ci["as_deployed"]),
            "language_forced": (excl["language_forced"], excl_ci["language_forced"]),
        }
        score_label = "Score (informative fields only)"
    else:
        head = {
            "as_deployed": (as_dep["overall_score"], as_dep["ci_95"]),
            "language_forced": (forced["overall_score"], forced["ci_95"]),
        }
        score_label = "Score"
    dep_score, dep_ci = head["as_deployed"]
    frc_score, frc_ci = head["language_forced"]
    lines = [
        f"Measured {data['generated_at']}"
        + (f" &middot; `{sha[:7]}`" if sha else "")
        + f" &middot; models: {models}",
        "",
        f"| Condition | {score_label} | 95% CI | n |",
        "|---|---|---|---|",
        f"| **As actually deployed** (real language routing) | **{_fmt_pct(dep_score)}** "
        f"| [{_fmt_pct(dep_ci['lower'])}, {_fmt_pct(dep_ci['upper'])}] "
        f"| {as_dep['n']} |",
        f"| Language routing forced correct | {_fmt_pct(frc_score)} "
        f"| [{_fmt_pct(frc_ci['lower'])}, {_fmt_pct(frc_ci['upper'])}] "
        f"| {forced['n']} |",
    ]
    n_total = data["n_fixtures"]
    agreement_text = _label_agreement_text(data)
    if echo:
        # Session 15d (D7). Disclosed directly beside the headline, like the stars-only case
        # below: both higher figures are shown with the reason each field is excluded.
        stars_only = data["overall_score_excluding_constant_fields"]
        stars_only_ci = data["overall_score_excluding_constant_fields_ci_95"]
        names = " and ".join(f"`{f}`" for f in [*constant, *echo])
        lines += [
            "",
            f"**Why the headline excludes {names}.** Counting all fields, the same recorded "
            f"outputs score {_fmt_pct(as_dep['overall_score'])} as deployed "
            f"[{_fmt_pct(as_dep['ci_95']['lower'])}, {_fmt_pct(as_dep['ci_95']['upper'])}] and "
            f"{_fmt_pct(forced['overall_score'])} with language routing forced correct "
            f"[{_fmt_pct(forced['ci_95']['lower'])}, {_fmt_pct(forced['ci_95']['upper'])}]; "
            f"excluding only `stars` they score {_fmt_pct(stars_only['as_deployed'])} "
            f"[{_fmt_pct(stars_only_ci['as_deployed']['lower'])}, "
            f"{_fmt_pct(stars_only_ci['as_deployed']['upper'])}] and "
            f"{_fmt_pct(stars_only['language_forced'])} "
            f"[{_fmt_pct(stars_only_ci['language_forced']['lower'])}, "
            f"{_fmt_pct(stars_only_ci['language_forced']['upper'])}]. "
            f"`stars` is null in both gold and prediction on all {n_total} reviews, so it "
            f"scores {n_total}/{n_total} trivially and carries no information. `language` does "
            "not measure extraction: with routing forced the prompt itself states the language, "
            "so the field is 100% by echo (which is why the forced row is higher in the "
            "all-fields figures), and as deployed it just re-measures the language detector "
            f"against the corpus's language label: {agreement_text}. The headline therefore averages only "
            f"the {len(data['headline_fields'])} remaining informative fields.",
            "",
            # Rule 65c: this definition change RAISES the as-deployed headline, so say so, by
            # how much, and that no model output moved -- generated from the same artifact.
            "**Headline definition change (Session 15d).** Until Session 15d the headline "
            "averaged every field except `stars`; dropping `language` moves the as-deployed "
            f"headline from {_fmt_pct(stars_only['as_deployed'])} to {_fmt_pct(dep_score)} and "
            f"the forced row from {_fmt_pct(stars_only['language_forced'])} to "
            f"{_fmt_pct(frc_score)}. The recorded model outputs are byte-identical; only which "
            "fields are averaged changed.",
        ]
    elif constant:
        # Disclosed directly beside the headline (not in a trailing note): the all-fields
        # figure is the larger one, and the reader must see both and why it is not the headline.
        names = ", ".join(f"`{f}`" for f in constant)
        inflation_pts = round((as_dep["overall_score"] - excl["as_deployed"]) * 100)
        if constant == ["stars"]:
            reason = (
                f"`stars` is null in both gold and prediction on all {n_total} reviews, so it "
                f"scores {n_total}/{n_total} trivially and carries no information"
            )
        else:
            reason = (
                f"{names} scores 100% on every one of the {n_total} reviews (the corpus "
                "contains no case for it), so it carries no information"
            )
        lines += [
            "",
            f"**Why the headline excludes {names}.** Counting all fields, the same recorded "
            f"outputs score {_fmt_pct(as_dep['overall_score'])} as deployed "
            f"[{_fmt_pct(as_dep['ci_95']['lower'])}, {_fmt_pct(as_dep['ci_95']['upper'])}] and "
            f"{_fmt_pct(forced['overall_score'])} with language routing forced correct "
            f"[{_fmt_pct(forced['ci_95']['lower'])}, {_fmt_pct(forced['ci_95']['upper'])}]. "
            f"{reason}, and it inflates the overall by about "
            f"{inflation_pts} points. The headline therefore averages only the informative "
            "fields.",
        ]
    lines += [
        "",
        f"n={n_total} real Hinglish reviews the prompt has never seen (never used for "
        f"prompt development), 0 hi (see [ADR 0016](docs/architecture/adr/0016-third-judge-corpus-batch-1-and-sentiment-recheck.md)). "
        f"Production's own language detector, measured against this corpus's language label: "
        f"{agreement_text}. This is the number to trust for real-world extraction accuracy; "
        f"the CI-gate headline above is a regression detector, not a real-world accuracy claim "
        f"-- see [ADR 0021](docs/architecture/adr/0021-reproducible-measurement-and-misrouting-cost.md) "
        f"(its misrouting-cost finding was corrected in Session 15d).",
    ]
    strict = as_dep.get("overall_score_strict_exact_match")
    if strict is not None and data.get("scorer_version"):
        # Rule 65c disclosure, generated from the same artifact as the headline number: this
        # scorer change RAISED the published figure, so say so and by how much, alongside it.
        lines += [
            "",
            f"**Scoring note (scorer `{data['scorer_version']}`).** Free-text fields (`product`, "
            "`topics`, `competitor_mentions`) are compared after normalization, so different "
            'correct spellings of "no product named" (`unknown` vs `unknown product`) and '
            "near-identical topic labels (`battery` vs `battery_life`) are no longer scored wrong. "
            "Earlier published figures used exact-string matching on the same recorded model "
            "outputs and counted all fields"
            + (" (the same basis as the all-fields figures above)" if constant else "")
            + f": as deployed, {_fmt_pct(strict)} then vs {_fmt_pct(as_dep['overall_score'])} "
            "now. The model's outputs did not change, only the comparator "
            "([ADR 0030](docs/architecture/adr/0030-free-text-scorers.md)).",
        ]
    return "\n".join(lines)


def _coverage_view(data: dict[str, Any]) -> tuple[dict[str, Any], int, dict[str, Any] | None]:
    """(published per-field block, its n, the all-reviews block or None).

    Session 17 (W3): once the artifact carries an `unexposed` block the published coverage figures
    are the ones over reviews the prompt-development process had not seen (ADR 0032); the all-106
    figures are kept beside them, never dropped. An older artifact without it renders as before.
    """
    if "unexposed" in data:
        return data["unexposed"]["per_field"], data["unexposed"]["n_fixtures"], data
    return data["per_field"], data["n_fixtures"], None


def _coverage_exposure_sentence(data: dict[str, Any], n: int) -> str:
    """One sentence (plain text) naming the exposure scope and what the all-reviews cut was."""
    total = data["n_fixtures"]
    seen = data["exposed"]["n_fixtures"]
    allr, exp = data["per_field"], data["exposed"]["per_field"]
    parts = ", ".join(
        f"{f.replace('_', ' ')} coverage {_fmt_pct(allr[f]['coverage'])} / "
        f"accuracy-on-answered {_fmt_pct(allr[f]['accuracy_on_answered'])}"
        for f in allr
    )
    return (
        f"n={n} reviews the prompt-development process had not seen; the {seen} of {total} it had "
        f"seen are excluded (ADR 0032). Over all {total} (the earlier published basis): {parts}. "
        f"On the {seen} seen reviews alone buy-again coverage is "
        f"{_fmt_pct(exp['buy_again']['coverage'])}, against "
        f"{_fmt_pct(data['unexposed']['per_field']['buy_again']['coverage'])} on the unseen ones, "
        "so including them flattered it"
    )


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
    per_field, n_pub, allr = _coverage_view(data)
    for field, info in per_field.items():
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
    scope = _coverage_exposure_sentence(data, n_pub) + ". " if allr is not None else f"n={n_pub}, "
    lines += [
        "",
        f"{scope}`{data['condition']}` condition (real language routing). "
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
    per_field, n, allr = _coverage_view(data)
    sentiment = per_field["sentiment"]
    buy_again = per_field["buy_again"]
    # Session 17 (W3): n is the unseen-review count once the artifact carries it; say so beside n.
    unit = f"{n} unseen reviews" if allr is not None else f"{n}"
    return (
        f"**When it commits to an answer, this model is correct "
        f"{_fmt_pct(sentiment['accuracy_on_answered'])} of the time for sentiment "
        f"(95% CI {_fmt_pct(sentiment['accuracy_on_answered_ci_95']['lower'])}–"
        f"{_fmt_pct(sentiment['accuracy_on_answered_ci_95']['upper'])}, n={unit}) and "
        f"{_fmt_pct(buy_again['accuracy_on_answered'])} of the time for buy-again "
        f"(95% CI {_fmt_pct(buy_again['accuracy_on_answered_ci_95']['lower'])}–"
        f"{_fmt_pct(buy_again['accuracy_on_answered_ci_95']['upper'])}, n={unit}) -- rates "
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
    per_field, n, allr = _coverage_view(data)
    sentiment = per_field["sentiment"]
    buy_again = per_field["buy_again"]
    unit = f"{n} unseen reviews" if allr is not None else f"{n}"

    def _card(label: str, info: dict[str, Any]) -> str:
        acc = _fmt_pct(info["accuracy_on_answered"])
        lo = _fmt_pct(info["accuracy_on_answered_ci_95"]["lower"])
        hi = _fmt_pct(info["accuracy_on_answered_ci_95"]["upper"])
        abstain_lo = _fmt_pct(1 - info["coverage_ci_95"]["upper"])
        abstain_hi = _fmt_pct(1 - info["coverage_ci_95"]["lower"])
        return (
            '            <div class="stat-card">\n'
            f'              <div class="stat-num">{acc}</div>\n'
            f'              <div class="stat-label">accurate when it commits to {label}</div>\n'
            f'              <div class="stat-note">95% CI [{lo}, {hi}], n={unit}. '
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


def render_known_gaps_html(data: dict[str, Any]) -> str:
    """Render the "Known gaps" banner (site/index.html) from eval/analyze_known_gaps.py's
    output -- Session 14 P2d. Replaces two previously-unmeasured claims:

    - "Sarcastic Hinglish... scores lower" had no measurement behind it. Real count: 3 of
      106 held-out reviews. Too small for any accuracy/coverage claim -- says so instead.
    - "Short reviews... occasionally miss fields" undersold the real finding two ways: the
      abstention rate is high AND correct (95.3% of the time a null was right, the panel
      agrees the info isn't there), while the real, larger issue on short reviews is
      confident-and-wrong guesses, not silent misses. (Session 15c C2: the fields named
      in the rendered sentence are now derived from the measured per-field counts. The
      original wording blamed product/topics, but most of that was the comparator, not
      the model -- see docs/architecture/adr/0030-free-text-scorers.md.)

    Session 15c D2: the third disclosure that used to live here (the hero's "fake-review
    flag" had no measurement on this held-out set) is deleted, as its own note said to do
    once the promise went. The hero and every other surface no longer promise the flag, so
    there is nothing left to disclose about it on the page.
    """
    sr = data["short_reviews"]
    sarcasm = data["sarcasm"]
    n = sr["n_short_reviews"]
    abstention_rate = _fmt_pct(sr["abstention_correctness_rate"], 1)
    real_gap_n = sr["counts"]["real_gap"]
    wrong_committed_n = sr["counts"]["wrong_committed"]
    total_checks = sr["total_field_checks"]
    # Session 15c C2: which fields dominate is DATA, not prose. This sentence used to hard-code
    # "the product name" as the culprit; once the comparator was corrected that field fell from
    # 25 to 5 wrong-committed and the hard-coded claim became false, so name the top fields from
    # the measured per-field counts (ties broken alphabetically for determinism).
    ranked = sorted(
        ((f, c.get("wrong_committed", 0)) for f, c in sr["per_field"].items()),
        key=lambda fc: (-fc[1], fc[0]),
    )
    top_wrong_fields = ", ".join(f"`{f}` ({n})" for f, n in ranked[:3] if n > 0)
    # Session 17 (W3e): the figures exclude reviews the development process had seen and field
    # pairs where the judge panel split (the stored gold there is a default, not a label). An
    # artifact without the exclusion keys (pre-S17) renders without the qualifier.
    split_n = sr.get("n_split_pairs_excluded")
    scope = (
        f"the development process had not seen ({split_n} field checks left out where the "
        "judge panel split on the label)"
        if sr.get("excluded_exposed_reviews") and split_n is not None
        else "(all of them)"
    )
    sarcasm_n = sarcasm["n_sarcastic_or_backhanded_found"]
    sarcasm_total = data["n_fixtures_total"]

    return (
        "\n"
        '        <strong class="note-lead">Known gaps: </strong>\n'
        '        English `sentiment` and `buy_again` hedge (return "mixed"/null) far more '
        "often under the current models than the previous ones — accuracy on the answers "
        "the model DOES commit to is unchanged, but it commits less often, and flat "
        "accuracy charges that the same as a wrong answer. Hinglish shows the opposite "
        f"pattern. On the {n} short reviews (under 10 words) among the held-out reviews "
        f"{scope}, when "
        f"the model says a field is unclear, that call is right {abstention_rate} of the "
        "time — the information usually genuinely isn't in the text. The real short-review "
        "issue is different: it commits to a value that does not match the reference labels "
        f"on {wrong_committed_n} of {total_checks} field checks, most often on "
        f"{top_wrong_fields}. Only {real_gap_n} of "
        f"{total_checks} were genuine silent misses. Separately: sarcastic or backhanded "
        f"phrasing is rare in real marketplace reviews — {sarcasm_n} of {sarcasm_total} in "
        "the whole corpus (seen and unseen reviews alike) — too few to measure reliably, so we don't claim a number for it "
        "either way.\n      "
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
    """PASS/FAIL cell for site/index.html. Session 15c C8: emits semantic classes (`status`,
    `status-pass`, `status-fail`) defined in that page's own stylesheet, not Tailwind palette
    classes -- the page's brand palette has no green/red hue, so PASS vs FAIL is carried by
    the glyph + word + weight, never by colour alone.
    """
    if passed:
        return '<td class="status status-pass">&#10003; PASS</td>'
    return '<td class="status status-fail">&#10007; FAIL</td>'


def render_extraction_table_html(data: dict[str, Any]) -> str:
    """Render the single headline-accuracy `<tbody>` row for site/index.html.

    GG display rule (2026-10-05): one headline string, no per-language rows. History: Session 5
    P4 fixed a hardcoded green PASS badge on every row -- the overall status still comes from
    `data["passed"]`, never a constant.
    """
    return (
        "\n"
        '            <tr class="row-total">\n'
        "              <td>Overall extraction accuracy</td>\n"
        f'              <td class="num">{render_headline_accuracy(data)}</td>\n'
        f'              <td class="gate">&ge;{data["threshold"]:.0%}</td>\n'
        f"              {_status_badge_html(data['passed'])}\n"
        "            </tr>\n"
        "          "
    )


def render_language_table_html(data: dict[str, Any]) -> str:
    """Render the language-support `<tbody>` rows for site/docs/index.html (no scores).

    GG display rule (2026-10-05): per-language accuracy is not displayed; headline accuracy
    lives only in `render_headline_accuracy`.

    Session 15c S8b: emits semantic classes (`path`, `ci`) defined in site/docs/index.html's
    own stylesheet, not Tailwind palette classes -- the docs page shares the marketing page's
    palette, which has no blue/green/red.
    """
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
        rows.append(
            "              <tr>\n"
            f'                <td class="path">{code}</td>\n'
            f"                <td>{label}</td>\n"
            f'                <td class="ci">{script}</td>\n'
            "              </tr>"
        )
    return "\n" + "\n".join(rows) + "\n            "


def render_coverage_metrics_table_html(data: dict[str, Any]) -> str:
    """Render the coverage/accuracy-on-answered/wrong-committed `<tbody>` rows (site/).

    Same source and discipline as render_coverage_metrics_table_md -- see that function's
    docstring and ADR 0026/0027.
    """
    rows: list[str] = []
    per_field, n_pub, allr = _coverage_view(data)
    for field, info in per_field.items():
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
            "            <tr>\n"
            f'              <td class="cap">{field.replace("_", " ")}</td>\n'
            f'              <td class="num">{cov} <span class="muted">{cov_ci}</span></td>\n'
            f'              <td class="num">{acc} <span class="muted">{acc_ci}</span></td>\n'
            f'              <td class="ci">{wrong} = {wrong_rate}</td>\n'
            "            </tr>"
        )
    if allr is not None:
        # Same scope sentence as the Markdown table, inside the table so it cannot be separated
        # from the numbers it qualifies.
        rows.append(
            "            <tr>\n"
            '              <td class="muted" colspan="4">'
            f"{_coverage_exposure_sentence(data, n_pub)}.</td>\n"
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
    "known_gaps_html": lambda: render_known_gaps_html(_load_json(KNOWN_GAPS_PATH)),
}

TARGET_FILES: tuple[Path, ...] = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "eval" / "README.md",
    REPO_ROOT / "site" / "index.html",
    REPO_ROOT / "site" / "docs" / "index.html",
    REPO_ROOT / "SECURITY.md",
)


# (repo-relative file, block name) -> why a marker block exists WITHOUT a renderer. Such a block
# is exempt from check_no_hardcoded_metrics.py (which blanks every METRICS:START..END span) yet
# is never regenerated or drift-checked here, so it is a hand-typed number zone. Only add an
# entry with a reason a reviewer can check; the right fix is a renderer, not an entry.
UNRENDERED_BLOCK_ALLOWLIST: dict[tuple[str, str], str] = {
    ("README.md", "consensus_labeling"): (
        "Added by 1599de2 with no renderer; ADR 0002 says a `consensus_labeling` renderer "
        "exists in this file, but none was ever committed. eval/consensus/run_consensus.py "
        "writes the source data (eval/consensus/results/consensus_summary.json, 'for the "
        "README render block') and the hand-typed block currently matches it. REMAINS "
        "UNVERIFIED against future re-runs -- see docs/decorative-control-sweep.md."
    ),
}

_START_TOKEN_RE = re.compile(r"<!--\s*METRICS:START")


def marker_problems(rel_path: str, text: str) -> list[str]:
    """Return problems that make a file's METRICS markers silently unchecked.

    render_file() deliberately leaves an unknown block name alone, so on its own `--check`
    reports OK for a typo'd name, an unnamed `<!-- METRICS:START -->`, or a START with no END
    -- while check_no_hardcoded_metrics.py still exempts the span from its hand-typed-number
    scan. Each such span is a number nobody verifies. A block name with no renderer must be
    listed in UNRENDERED_BLOCK_ALLOWLIST with a reason; a stale allowlist entry is a problem too.
    """
    problems: list[str] = []
    matches = list(BLOCK_RE.finditer(text))
    n_starts = len(_START_TOKEN_RE.findall(text))
    if n_starts != len(matches):
        problems.append(
            f"{rel_path}: {n_starts} METRICS:START marker(s) but only {len(matches)} complete "
            "`START:<name> ... END` block(s) -- an unnamed, unterminated or nested marker is "
            "exempt from the hand-typed-number scan but never rendered or drift-checked"
        )
    seen = {m.group("name") for m in matches}
    for name in sorted(seen):
        if name not in BLOCK_RENDERERS and (rel_path, name) not in UNRENDERED_BLOCK_ALLOWLIST:
            problems.append(
                f"{rel_path}: block {name!r} has no renderer in BLOCK_RENDERERS and is not in "
                "UNRENDERED_BLOCK_ALLOWLIST -- a typo'd name is silently never checked"
            )
    for file_, name in UNRENDERED_BLOCK_ALLOWLIST:
        if file_ == rel_path and name not in seen:
            problems.append(f"{rel_path}: stale UNRENDERED_BLOCK_ALLOWLIST entry {name!r}")
    return problems


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
    any_problem = False

    for path in TARGET_FILES:
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        if not path.exists():
            # A renamed/deleted target used to be skipped silently, so --check stayed green
            # while the numbers it protected were no longer verified anywhere.
            print(f"PROBLEM: target file {rel_path} does not exist -- nothing verifies it")
            any_problem = True
            continue
        for problem in marker_problems(rel_path, path.read_text(encoding="utf-8")):
            print(f"PROBLEM: {problem}")
            any_problem = True
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

    for (file_, name), reason in UNRENDERED_BLOCK_ALLOWLIST.items():
        print(
            f"WARN: {file_} block {name!r} is hand-typed, not rendered or drift-checked: {reason}"
        )

    if check_only:
        if any_changed or any_problem:
            print(
                "\nFAIL: one or more files are stale relative to the eval JSON, or carry "
                "METRICS markers this script cannot verify (see PROBLEM lines above).\n"
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
