"""Unit tests for scripts/render_metrics.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.render_metrics import (
    BLOCK_RENDERERS,
    render_committed_accuracy_headline_html,
    render_committed_accuracy_headline_md,
    render_coverage_metrics_table_html,
    render_extraction_table_html,
    render_extraction_table_md,
    render_file,
    render_gate_summary_md,
    render_held_out_table_md,
    render_known_gaps_html,
    render_language_table_html,
    render_portfolio_metrics_json,
)

EXTRACTION_DATA = {
    "prompt_version": "v2.3",
    "git_sha": "deadbeef" * 5,
    "generated_at": "2026-07-30T00:00:00Z",
    "mode": "direct (local LLM)",
    "tiered_routing_enabled_at_runtime": True,
    "groq_model_small": "llama-3.1-8b-instant",
    "groq_model_large": "llama-3.3-70b-versatile",
    "overall_score": 0.838,
    "overall_ci_95": {"n": 49, "lower": 0.71, "upper": 0.92},
    "threshold": 0.83,
    "passed": True,
    "per_language": {
        "en": {
            "score": 0.862,
            "n": 27,
            "ci_95": {"lower": 0.68, "upper": 0.95},
            "threshold": 0.8,
            "passed": True,
        },
        "hi": {
            "score": 0.807,
            "n": 7,
            "ci_95": {"lower": 0.44, "upper": 0.96},
            "threshold": 0.8,
            "passed": True,
        },
        "hi-en": {
            "score": 0.809,
            "n": 15,
            "ci_95": {"lower": 0.56, "upper": 0.93},
            "threshold": 0.8,
            "passed": True,
        },
    },
}


class TestRenderExtractionTableMd:
    def test_contains_all_languages_and_overall(self):
        out = render_extraction_table_md(EXTRACTION_DATA)
        assert "en |" in out
        assert "hi-en |" in out
        assert "**Overall**" in out
        assert "83.8%" in out
        assert "PASS" in out

    def test_language_order_matches_repo_convention(self):
        out = render_extraction_table_md(EXTRACTION_DATA)
        # en, then hi-en, then hi -- not alphabetical.
        assert out.index("| en |") < out.index("| hi-en |") < out.index("| hi |")

    def test_fail_status_shown_when_not_passed(self):
        data = {**EXTRACTION_DATA, "passed": False}
        out = render_extraction_table_md(data)
        assert "**FAIL**" in out or "FAIL |" in out

    def test_routing_note_present_when_tiered_routing_on(self):
        out = render_extraction_table_md(EXTRACTION_DATA)
        assert "Tiered routing is ON" in out

    def test_routing_note_absent_variant_when_off(self):
        data = {**EXTRACTION_DATA, "tiered_routing_enabled_at_runtime": False}
        out = render_extraction_table_md(data)
        assert "Tiered routing was OFF" in out


class TestAuthenticityNotPublished:
    """The fake-review flag is unmeasurable (no authenticity labels on the held-out set; the
    40-item historical set is in-sample), so nothing may render it into published copy."""

    def test_no_authenticity_block_renderer_is_registered(self):
        assert not [name for name in BLOCK_RENDERERS if "authenticity" in name.lower()]

    def test_renderer_function_is_gone(self):
        import scripts.render_metrics as rm

        assert not hasattr(rm, "render_authenticity_table_md")

    def test_readme_carries_no_authenticity_metrics_block_or_handtyped_scores(self):
        readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
        assert "METRICS:START:authenticity" not in readme
        for claim in ("precision 1.000", "recall 1.000", "F1 1.000", "F1 | 1.000"):
            assert claim not in readme, f"hand-typed authenticity claim back in README: {claim!r}"


class TestRenderGateSummaryMd:
    def test_reflects_thresholds(self):
        out = render_gate_summary_md(EXTRACTION_DATA)
        assert "80%" in out
        assert "83%" in out

    def test_per_language_thresholds_shown_independently(self):
        # Regression test (Session 5 P4, 2026-09-10): render_gate_summary_md used to read
        # a single threshold via next(iter(data["per_language"].values())) -- silently
        # correct only when every language shared one global threshold, silently WRONG
        # (reporting one language's gate as if it applied to all three) the moment
        # eval/runner.py's PER_LANG_THRESHOLD became per-language. With en=74%/hi=80%/
        # hi-en=80%, a summary that only shows "74%" or only "80%" has this bug back.
        data = {
            **EXTRACTION_DATA,
            "threshold": 0.77,
            "per_language": {
                "en": {**EXTRACTION_DATA["per_language"]["en"], "threshold": 0.74},
                "hi": {**EXTRACTION_DATA["per_language"]["hi"], "threshold": 0.80},
                "hi-en": {**EXTRACTION_DATA["per_language"]["hi-en"], "threshold": 0.80},
            },
        }
        out = render_gate_summary_md(data)
        assert "77%" in out
        assert "74%" in out
        assert "80%" in out


class TestRenderExtractionTableHtml:
    def test_renders_a_row_per_language_plus_overall(self):
        out = render_extraction_table_html(EXTRACTION_DATA)
        assert out.count("<tr") == 4  # 3 languages + overall
        assert "86.2%" in out
        assert "83.8%" in out

    def test_all_passing_renders_green_pass_everywhere(self):
        out = render_extraction_table_html(EXTRACTION_DATA)
        assert out.count("PASS") == 4
        assert "FAIL" not in out
        assert "status-fail" not in out

    def test_failing_language_renders_red_fail_not_green_pass(self):
        # Regression test (Session 5 P4, 2026-09-10): every row used to hardcode the
        # green PASS badge unconditionally, ignoring info["passed"]/data["passed"] --
        # found on review-iq's own committed site/index.html, which was claiming PASS
        # for English and Overall while both were actually below their gate.
        data = {
            **EXTRACTION_DATA,
            "overall_score": 0.776,
            "passed": False,
            "per_language": {
                **EXTRACTION_DATA["per_language"],
                "en": {**EXTRACTION_DATA["per_language"]["en"], "score": 0.750, "passed": False},
            },
        }
        out = render_extraction_table_html(data)
        assert out.count("FAIL") == 2  # en row + overall row
        assert out.count("PASS") == 2  # hi + hi-en rows only
        # Session 15c C8: FAIL is carried by the status-fail class (glyph + weight), since
        # the page palette has no red hue -- the count still proves it is per-row, not global.
        assert out.count("status-fail") == 2
        assert out.count("status-pass") == 2


class TestSiteIndexBlocksAreBrandPaletteOnly:
    """Session 15c C8: site/index.html's brand palette (design/tokens.json) has no blue, green,
    red, amber or violet hue, and the page no longer loads Tailwind. Every renderer feeding an
    index.html marker block must emit semantic classes, never Tailwind palette utilities.
    """

    FORBIDDEN = ("blue-", "green-", "red-", "amber-", "violet-", "gray-", "slate-", "indigo-")

    def _all_index_block_output(self) -> str:
        gaps = {
            "short_reviews": {
                "n_short_reviews": 27,
                "abstention_correctness_rate": 0.9329,
                "counts": {"wrong_committed": 48, "real_gap": 10},
                "total_field_checks": 324,
                "per_field": {"topics": {"wrong_committed": 11}},
            },
            "sarcasm": {"n_sarcastic_or_backhanded_found": 3},
            "n_fixtures_total": 106,
        }
        return "".join(
            (
                render_extraction_table_html(EXTRACTION_DATA),
                render_committed_accuracy_headline_html(COVERAGE_DATA),
                render_coverage_metrics_table_html(COVERAGE_DATA),
                render_known_gaps_html(gaps),
            )
        )

    def test_no_tailwind_palette_classes_in_any_index_block(self):
        out = self._all_index_block_output()
        assert [t for t in self.FORBIDDEN if t in out] == []

    def test_failing_status_uses_semantic_class_not_a_colour(self):
        data = {**EXTRACTION_DATA, "passed": False}
        out = render_extraction_table_html(data)
        assert "status-fail" in out
        assert "red-" not in out


class TestRenderLanguageTableHtml:
    def test_renders_two_rows(self):
        # Session 11 (ADR 0022): Devanagari `hi` retired from rows_spec -- EXTRACTION_DATA
        # still carries a "hi" key in per_language (harmless, unused by this renderer) so
        # other tests exercising per_language generically don't need to change too.
        out = render_language_table_html(EXTRACTION_DATA)
        assert out.count("<tr") == 2
        assert "Devanagari" not in out

    def test_all_passing_marks_every_row_as_passing(self):
        out = render_language_table_html(EXTRACTION_DATA)
        assert "status-fail" not in out
        assert "below" not in out

    def test_failing_language_is_marked_with_gate_note(self):
        # Regression test (Session 5 P4, 2026-09-10): same class of bug as
        # render_extraction_table_html -- the accuracy cell hardcoded a green class
        # unconditionally, so a failing language's score would render as if it passed.
        data = {
            **EXTRACTION_DATA,
            "per_language": {
                **EXTRACTION_DATA["per_language"],
                "en": {
                    **EXTRACTION_DATA["per_language"]["en"],
                    "score": 0.750,
                    "passed": False,
                    "threshold": 0.74,
                },
            },
        }
        out = render_language_table_html(data)
        assert "status-fail" in out
        assert "below 74% gate" in out
        # hi-en still passes and must not be collateral-marked failing. (hi is no
        # longer rendered at all -- see test_renders_two_rows.)
        assert out.count("status-pass") == 1

    def test_no_tailwind_palette_classes(self):
        # Session 15c S8b: the docs page shares the marketing page's blue-free palette.
        out = render_language_table_html(EXTRACTION_DATA)
        forbidden = ("blue-", "green-", "red-", "amber-", "violet-", "gray-", "slate-")
        assert [t for t in forbidden if t in out] == []


COVERAGE_DATA = {
    "n_fixtures": 106,
    "condition": "as_deployed",
    "per_field": {
        "sentiment": {
            "coverage": 0.774,
            "coverage_ci_95": {"lower": 0.689, "upper": 0.849},
            "accuracy_on_answered": 0.878,
            "accuracy_on_answered_ci_95": {"lower": 0.805, "upper": 0.939},
            "wrong_committed_of_answered": "10/82",
            "wrong_committed_rate": 0.122,
            "wrong_committed_rate_ci_95": {"lower": 0.061, "upper": 0.195},
        },
        "buy_again": {
            "coverage": 0.434,
            "coverage_ci_95": {"lower": 0.340, "upper": 0.528},
            "accuracy_on_answered": 0.761,
            "accuracy_on_answered_ci_95": {"lower": 0.630, "upper": 0.870},
            "wrong_committed_of_answered": "11/46",
            "wrong_committed_rate": 0.239,
            "wrong_committed_rate_ci_95": {"lower": 0.130, "upper": 0.370},
        },
    },
}


class TestRenderCommittedAccuracyHeadlineMd:
    def test_reports_both_fields_never_blended(self):
        # Session 13 P1b/ADR 0027: this claim must never collapse the two fields into one
        # blended number -- both field-specific accuracy-on-answered figures must appear.
        out = render_committed_accuracy_headline_md(COVERAGE_DATA)
        assert "87.8%" in out
        assert "76.1%" in out
        assert "sentiment" in out
        assert "buy-again" in out

    def test_does_not_conflate_abstention_with_wrong_committed(self):
        # Regression guard: an earlier draft of this renderer incorrectly described
        # abstention ("says unclear") as happening "instead of" a wrong-committed answer --
        # these are two independent, separately-measured quantities (1-coverage vs.
        # 1-accuracy_on_answered), not complements of each other. The generated sentence
        # must not claim the model "says unclear" as the outcome of NOT being accurate.
        out = render_committed_accuracy_headline_md(COVERAGE_DATA)
        assert "unclear" not in out.lower()

    def test_includes_cis(self):
        out = render_committed_accuracy_headline_md(COVERAGE_DATA)
        assert "80.5%" in out
        assert "93.9%" in out
        assert "63.0%" in out
        assert "87.0%" in out


class TestRenderCommittedAccuracyHeadlineHtml:
    def test_renders_two_cards(self):
        out = render_committed_accuracy_headline_html(COVERAGE_DATA)
        assert out.count("<div") >= 2 * 3  # 2 cards, 3 nested divs each minimum
        assert "87.8%" in out
        assert "76.1%" in out

    def test_abstention_stated_as_separate_fact_not_a_complement(self):
        # Same regression guard as the Markdown renderer, applied to the HTML card copy.
        out = render_committed_accuracy_headline_html(COVERAGE_DATA)
        assert "Separately" in out
        assert "instead of guessing" not in out


class TestRenderCoverageMetricsTableHtml:
    def test_renders_both_fields(self):
        out = render_coverage_metrics_table_html(COVERAGE_DATA)
        assert out.count("<tr") == 2
        assert "sentiment" in out
        assert "buy again" in out  # underscore replaced with space for display

    def test_wrong_committed_counts_present(self):
        out = render_coverage_metrics_table_html(COVERAGE_DATA)
        assert "10/82" in out
        assert "11/46" in out


class TestRenderFile:
    def test_replaces_known_block_and_leaves_rest_untouched(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "doc.md"
        target.write_text(
            "before\n<!-- METRICS:START:gate_summary -->OLD<!-- METRICS:END -->\nafter\n",
            encoding="utf-8",
        )
        # Point the renderer at our in-memory test data instead of the real JSON files.
        monkeypatch.setitem(BLOCK_RENDERERS, "gate_summary", lambda: "NEW")
        new_content, changed = render_file(target)
        assert changed is True
        assert (
            new_content
            == "before\n<!-- METRICS:START:gate_summary -->NEW<!-- METRICS:END -->\nafter\n"
        )

    def test_unknown_block_name_left_untouched(self, tmp_path: Path):
        target = tmp_path / "doc.md"
        original = "<!-- METRICS:START:not_a_real_block -->stuff<!-- METRICS:END -->"
        target.write_text(original, encoding="utf-8")
        new_content, changed = render_file(target)
        assert changed is False
        assert new_content == original

    def test_no_markers_means_no_change(self, tmp_path: Path):
        target = tmp_path / "doc.md"
        target.write_text("plain text, no markers here\n", encoding="utf-8")
        new_content, changed = render_file(target)
        assert changed is False


class TestRenderPortfolioMetricsJson:
    """Session 7 P1: .portfolio/metrics.json went stale a third time because it was
    hand-typed prose in a file type check_no_hardcoded_metrics.py never scans (.json).
    Fixed by making it a whole-file generated target instead, drift-checked by
    render_metrics.py --check the same way every marker-block target is."""

    def test_output_is_valid_json(self):
        out = render_portfolio_metrics_json(EXTRACTION_DATA)
        json.loads(out)  # must not raise

    def test_value_field_reflects_current_scores_not_stale_ones(self):
        # Regression test for the actual incident: this data fixture's numbers
        # (86.2/80.7/80.9/83.8) must appear -- not some other, stale hardcoded pair.
        out = render_portfolio_metrics_json(EXTRACTION_DATA)
        doc = json.loads(out)
        value = doc["metrics"][0]["value"]
        assert "83.8%" in value
        assert "86.2%" in value
        assert "80.7%" in value
        assert "80.9%" in value

    def test_gate_status_reflects_real_pass_fail_per_language(self):
        data = {
            **EXTRACTION_DATA,
            "passed": False,
            "per_language": {
                **EXTRACTION_DATA["per_language"],
                "en": {**EXTRACTION_DATA["per_language"]["en"], "passed": False},
            },
        }
        out = render_portfolio_metrics_json(data)
        doc = json.loads(out)
        gate_status = doc["metrics"][0]["gate_status"]
        assert "FAILS" in gate_status
        assert "passes" in gate_status  # hi/hi-en still pass

    def test_regenerating_twice_is_idempotent(self):
        first = render_portfolio_metrics_json(EXTRACTION_DATA)
        second = render_portfolio_metrics_json(EXTRACTION_DATA)
        assert first == second

    def test_stale_committed_file_would_be_caught_as_drift(self, tmp_path: Path):
        # Simulates the actual Session 7 incident: a committed file with the OLD
        # numbers must differ from a fresh regeneration -- this is what
        # `render_metrics.py --check` compares to decide DRIFT vs OK.
        stale_committed = json.dumps(
            {
                "version": 1,
                "metrics": [{"id": "reviewiq:extraction-eval", "value": "77.6% overall"}],
            }
        )
        fresh = render_portfolio_metrics_json(EXTRACTION_DATA)
        assert stale_committed != fresh


class TestKnownGapsNamesFieldsFromData:
    """Session 15c C2: the sentence names the top wrong-committed fields from the measured
    per-field counts. It used to hard-code "the product name", which became false once the
    comparator was corrected (product fell from 25 to 5 wrong-committed)."""

    @staticmethod
    def _data(per_field: dict[str, dict[str, int]]) -> dict:
        return {
            "n_fixtures_total": 106,
            "sarcasm": {"n_sarcastic_or_backhanded_found": 3},
            "short_reviews": {
                "n_short_reviews": 27,
                "total_field_checks": 324,
                "abstention_correctness_rate": 0.9329,
                "counts": {"wrong_committed": 48, "real_gap": 10},
                "per_field": per_field,
            },
        }

    def test_top_three_fields_by_count_named_in_order(self):
        html = render_known_gaps_html(
            self._data(
                {
                    "product": {"wrong_committed": 5},
                    "language": {"wrong_committed": 13},
                    "topics": {"wrong_committed": 11},
                    "pros": {"wrong_committed": 6},
                    "stars": {"correct_abstention": 27},
                }
            ),
        )
        assert "`language` (13), `topics` (11), `pros` (6)" in html
        assert "product name" not in html
        assert "`product` (5)" not in html  # fourth place is not named

    def test_zero_count_fields_never_named(self):
        html = render_known_gaps_html(
            self._data({"topics": {"wrong_committed": 2}, "pros": {"correct": 9}}),
        )
        assert "`topics` (2)" in html
        assert "`pros`" not in html

    def test_ties_break_alphabetically_for_determinism(self):
        html = render_known_gaps_html(
            self._data({"topics": {"wrong_committed": 4}, "cons": {"wrong_committed": 4}}),
        )
        assert html.index("`cons` (4)") < html.index("`topics` (4)")

    def test_no_fake_review_promise_or_disclosure(self):
        # Session 15c D2: the fake-review flag is no longer promised anywhere on the page, so
        # the paragraph must not name it (neither a claim nor a disclosure), while the
        # sarcasm disclosure that precedes it stays.
        html = render_known_gaps_html(self._data({"topics": {"wrong_committed": 4}}))
        lowered = html.lower()
        assert "fake" not in lowered
        assert "authentic" not in lowered
        assert "sarcastic or backhanded" in html
        assert html.rstrip().endswith("either way.")


class TestHeldOutScoringDisclosure:
    """Rule 65c: a scorer change that raises the headline is disclosed beside it, generated
    from the same artifact, together with any constant field that flatters the overall."""

    DATA = {
        "generated_at": "2026-09-20T00:00:00Z",
        "git_sha": "abcdef1234567",
        "groq_model_small": "s",
        "groq_model_large": "l",
        "n_fixtures": 106,
        "language_label_agreement": 0.48,
        "language_label_agreement_wilson_95": {"lower": 0.39, "upper": 0.575, "n": 106},
        "language_label_alpha": 0.38,
        "as_deployed": {
            "n": 106,
            "overall_score": 0.727,
            "ci_95": {"lower": 0.705, "upper": 0.749},
            "overall_score_strict_exact_match": 0.683,
        },
        "language_forced": {
            "n": 106,
            "overall_score": 0.774,
            "ci_95": {"lower": 0.754, "upper": 0.793},
        },
    }

    def test_note_shows_old_and_new_and_the_scorer_version(self):
        md = render_held_out_table_md({**self.DATA, "scorer_version": "v-test"})
        assert "scorer `v-test`" in md
        assert "68.3% then vs 72.7% now" in md

    def test_no_note_for_artifacts_without_scorer_version(self):
        md = render_held_out_table_md(self.DATA)
        assert "Scoring note" not in md

    WITH_CONSTANT = {
        **DATA,
        "constant_fields": ["stars"],
        "overall_score_excluding_constant_fields": {
            "as_deployed": 0.697,
            "language_forced": 0.749,
        },
        "overall_score_excluding_constant_fields_ci_95": {
            "as_deployed": {"lower": 0.671, "upper": 0.722, "n": 106},
            "language_forced": {"lower": 0.727, "upper": 0.772, "n": 106},
        },
    }

    def test_headline_is_the_excluding_constant_fields_figure_with_its_own_ci(self):
        md = render_held_out_table_md(self.WITH_CONSTANT)
        assert "| **As actually deployed** (real language routing) | **69.7%** " in md
        assert "[67.1%, 72.2%]" in md
        assert "| Language routing forced correct | 74.9% | [72.7%, 77.2%] | 106 |" in md
        # The all-fields CI must not be attached to the headline row.
        headline_row = next(ln for ln in md.splitlines() if "As actually deployed" in ln)
        assert "72.7%" not in headline_row
        assert "[70.5%, 74.9%]" not in headline_row

    def test_all_fields_figure_disclosed_beside_headline_with_reason(self):
        md = render_held_out_table_md(self.WITH_CONSTANT)
        assert "72.7% as deployed [70.5%, 74.9%]" in md
        assert "77.4% with language routing forced correct" in md
        assert "`stars` is null in both gold and prediction on all 106 reviews" in md
        assert "scores 106/106 trivially and carries no information" in md
        assert "inflates the overall by about 3 points" in md
        # Placement: the disclosure comes before the trailing scoring note, right after the table.
        table_end = md.index("| Language routing forced correct")
        assert table_end < md.index("Why the headline excludes") < md.index("n=106 real")

    def test_scoring_note_says_it_is_on_the_all_fields_basis(self):
        md = render_held_out_table_md({**self.WITH_CONSTANT, "scorer_version": "v-test"})
        assert "counted all fields (the same basis as the all-fields figures above)" in md
        assert "as deployed, 68.3% then vs 72.7% now" in md

    def test_constant_fields_without_ci_fail_loudly_not_fall_back_to_all_fields(self):
        # A silent fallback would publish the flattering figure as the headline.
        broken = {k: v for k, v in self.WITH_CONSTANT.items() if not k.endswith("_ci_95")}
        with pytest.raises(KeyError):
            render_held_out_table_md(broken)

    def test_no_constant_fields_headline_is_the_all_fields_figure(self):
        md = render_held_out_table_md(self.DATA)
        assert "| **As actually deployed** (real language routing) | **72.7%** " in md
        assert "Why the headline excludes" not in md


class TestHeldOutHeadlineExcludesLanguage:
    """Session 15d (D7): the headline excludes `stars` (constant) AND `language` (echo / label
    noise), with every higher figure and the reason disclosed beside it, all from the artifact."""

    WITH_ECHO = {
        **TestHeldOutScoringDisclosure.WITH_CONSTANT,
        "headline_echo_fields": ["language"],
        "headline_fields": ["product", "sentiment", "topics"],
        "overall_score_headline": {"as_deployed": 0.724, "language_forced": 0.718},
        "overall_score_headline_ci_95": {
            "as_deployed": {"lower": 0.698, "upper": 0.750, "n": 106},
            "language_forced": {"lower": 0.693, "upper": 0.742, "n": 106},
        },
        "scorer_version": "v-test",
    }

    def test_headline_rows_are_the_ex_stars_ex_language_figures_with_their_own_cis(self):
        md = render_held_out_table_md(self.WITH_ECHO)
        assert "| **As actually deployed** (real language routing) | **72.4%** " in md
        assert "| [69.8%, 75.0%] | 106 |" in md
        assert "| Language routing forced correct | 71.8% | [69.3%, 74.2%] | 106 |" in md
        headline_row = next(ln for ln in md.splitlines() if "As actually deployed" in ln)
        assert "69.7%" not in headline_row  # the stars-only figure is not the headline
        assert "72.7%" not in headline_row  # nor the all-fields one

    def test_all_fields_and_stars_only_figures_disclosed_with_reasons(self):
        md = render_held_out_table_md(self.WITH_ECHO)
        assert "**Why the headline excludes `stars` and `language`.**" in md
        assert "72.7% as deployed [70.5%, 74.9%]" in md
        assert "77.4% with language routing forced correct" in md
        assert "excluding only `stars` they score 69.7% [67.1%, 72.2%]" in md
        assert "`stars` is null in both gold and prediction on all 106 reviews" in md
        assert "the field is 100% by echo" in md
        assert "the 3 remaining informative fields" in md

    def test_definition_change_is_disclosed_with_old_and_new_headline(self):
        # Rule 65c: the change raised the as-deployed headline, so it is stated, generated.
        md = render_held_out_table_md(self.WITH_ECHO)
        assert "from 69.7% to 72.4%" in md
        assert "from 74.9% to 71.8%" in md
        assert "byte-identical" in md

    def test_agreement_is_never_called_accuracy_and_carries_ci_and_alpha(self):
        md = render_held_out_table_md(self.WITH_ECHO)
        assert (
            "agreement with the corpus label 48.0% (95% CI 39.0-57.5; label alpha 0.380, "
            "so this partly measures label noise, not detector error)"
        ) in md
        assert "detection accuracy" not in md.lower()
        assert "language detector agreed" not in md

    def test_echo_fields_without_headline_figures_fail_loudly(self):
        broken = {k: v for k, v in self.WITH_ECHO.items() if k != "overall_score_headline"}
        with pytest.raises(KeyError):
            render_held_out_table_md(broken)
