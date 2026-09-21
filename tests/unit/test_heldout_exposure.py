"""Session 16 (V1/V2): held-out exposure detection, split-gold flagging, and the scorer/guard that
consume them. The two defects these pin: (1) 36 of 106 held-out reviews were also in prompt-visible
development sets and nothing failed; (2) a judge-panel split was stored as a default that looked
like a label."""

from __future__ import annotations

import json
from typing import Any

import pytest
from eval import heldout_exposure as hx
from eval.consensus.build_held_out_corpus import build_fixture
from eval.score_held_out_corpus_v2 import summarize
from scripts import check_no_heldout_leakage as guard
from scripts.render_metrics import render_held_out_table_md


class TestNormalizeAndIndex:
    def test_normalization_ignores_case_punctuation_and_scraper_artifact(self) -> None:
        a = "Mast product!! READ MORE"
        b = "mast   product"
        assert hx.normalize_review_text(a) == hx.normalize_review_text(b) == "mastproduct"

    def test_exposure_index_flags_dev_bench_and_both(self) -> None:
        held = {"h1": "Great sound", "h2": "Bad mic", "h3": "Ok fit", "h4": "Unseen review"}
        dev = {hx.normalize_review_text("great sound"): "eval/fixtures/hi-en/001.json"}
        bench = {
            hx.normalize_review_text("BAD MIC..."): "bench-hien-001",
            hx.normalize_review_text("ok fit"): "bench-hien-002",
        }
        dev[hx.normalize_review_text("Ok fit")] = "eval/fixtures/hi-en/002.json"
        idx = hx.exposure_index(held, dev=dev, bench=bench)
        assert idx == {
            "h1": [hx.REASON_DEV_FIXTURE],
            "h2": [hx.REASON_BENCHMARK_GOLD],
            "h3": [hx.REASON_BENCHMARK_GOLD, hx.REASON_DEV_FIXTURE],
        }
        assert "h4" not in idx

    def test_empty_text_is_never_matched(self) -> None:
        assert hx.exposure_index({"h": "!!!"}, dev={"": "x"}, bench={"": "y"}) == {}


class TestUnresolvedFields:
    def test_derived_from_agreement_for_legacy_fixtures(self) -> None:
        fx = {"labeling_meta": {"agreement_per_field": {"pros": "split", "product": "majority"}}}
        assert hx.unresolved_fields(fx) == ("pros",)

    def test_explicit_list_wins(self) -> None:
        fx = {
            "labeling_meta": {
                "agreement_per_field": {"pros": "split"},
                "unresolved_fields": ["cons"],
            }
        }
        assert hx.unresolved_fields(fx) == ("cons",)


def _consensus(**levels: str) -> dict[str, dict[str, Any]]:
    fields = [
        "product",
        "stars",
        "stars_inferred",
        "pros",
        "cons",
        "buy_again",
        "sentiment",
        "topics",
        "competitor_mentions",
        "urgency",
        "feature_requests",
        "language",
    ]
    out: dict[str, dict[str, Any]] = {}
    for f in fields:
        level = levels.get(f, "unanimous")
        out[f] = {"agreement": level, "silver": None if level == "split" else _silver(f)}
    return out


def _silver(field: str) -> Any:
    return (
        []
        if field in {"pros", "cons", "topics", "competitor_mentions", "feature_requests"}
        else ("headphones" if field == "product" else None)
    )


class TestBuilderStoresSplitDistinguishably:
    def test_split_field_is_named_and_genuine_empty_is_not(self) -> None:
        fx = build_fixture("hien-x", "text", _consensus(pros="split", product="split"), "src")
        meta = fx["labeling_meta"]
        assert meta["unresolved_fields"] == ["pros", "product"] or set(
            meta["unresolved_fields"]
        ) == {"pros", "product"}
        # `cons` is a unanimous EMPTY list: a real label, must not be flagged.
        assert fx["ground_truth"]["cons"] == [] and "cons" not in meta["unresolved_fields"]
        # And the stored defaults for the split fields are identical in form to a real empty label
        # -- which is exactly why the explicit list is required.
        assert fx["ground_truth"]["pros"] == fx["ground_truth"]["cons"] == []

    def test_field_missing_from_consensus_is_unresolved(self) -> None:
        cons = _consensus()
        del cons["topics"]
        fx = build_fixture("hien-x", "text", cons, "src")
        assert "topics" in fx["labeling_meta"]["unresolved_fields"]

    def test_explicit_list_agrees_with_derived_rule(self) -> None:
        fx = build_fixture("hien-x", "text", _consensus(cons="split"), "src")
        assert hx.unresolved_fields(fx) == tuple(sorted(fx["labeling_meta"]["unresolved_fields"]))
        legacy = {k: v for k, v in fx.items()}
        legacy["labeling_meta"] = {
            k: v for k, v in fx["labeling_meta"].items() if k != "unresolved_fields"
        }
        assert hx.unresolved_fields(legacy) == hx.unresolved_fields(fx)


class TestCommittedCorpus:
    def test_split_counts_match_the_published_audit(self) -> None:
        fixtures = hx.load_held_out_fixtures()
        counts: dict[str, int] = {}
        for fx in fixtures.values():
            for f in hx.unresolved_fields(fx):
                counts[f] = counts.get(f, 0) + 1
        assert len(fixtures) == 106
        # Published audit: pros 30, product 26, topics 25, cons 21. hien-0065 / hien-0070 have
        # their `product` gold removed from scoring pending adjudication (S16 V2d): 26 + 2.
        assert {k: counts[k] for k in ("pros", "product", "topics", "cons")} == {
            "pros": 30,
            "product": 28,
            "topics": 25,
            "cons": 21,
        }

    def test_ledger_matches_computed_exposure_exactly(self) -> None:
        ledger = json.loads(hx.ACK_PATH.read_text(encoding="utf-8"))["acknowledged"]
        assert set(ledger) == set(hx.held_out_exposure())
        assert len(ledger) == 36
        assert sum("few_shot_source" in v for v in ledger.values()) == 4


class TestGuardFailsClosed:
    def test_new_overlap_is_a_problem(self) -> None:
        problems = guard.find_exposure_problems({"h1": [hx.REASON_DEV_FIXTURE]}, {})
        assert len(problems) == 1 and "h1" in problems[0] and "not held out" in problems[0]

    def test_stale_ledger_entry_is_a_problem(self) -> None:
        problems = guard.find_exposure_problems({}, {"h9": {"reasons": []}})
        assert len(problems) == 1 and "stale" in problems[0]

    def test_matching_ledger_is_clean(self) -> None:
        assert guard.find_exposure_problems({"h1": ["x"]}, {"h1": {}}) == []

    def test_main_fails_when_a_ledger_entry_is_removed(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Induced failure on the real corpus: drop one entry -> the build must go red.
        ledger = json.loads(hx.ACK_PATH.read_text(encoding="utf-8"))
        ledger["acknowledged"].pop(next(iter(ledger["acknowledged"])))
        bad = tmp_path / "ack.json"
        bad.write_text(json.dumps(ledger), encoding="utf-8")
        monkeypatch.setattr(hx, "ACK_PATH", bad)
        assert guard.main() == 1
        assert "not held out" in capsys.readouterr().err

    def test_main_fails_closed_when_the_ledger_is_missing(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hx, "ACK_PATH", tmp_path / "absent.json")
        assert guard.main() == 1

    def test_main_passes_on_the_committed_repo(self) -> None:
        assert guard.main() == 0


def _rec(
    rid: str,
    scores: dict[str, float],
    *,
    unresolved: tuple[str, ...] = (),
    exposure: tuple[str, ...] = (),
) -> dict[str, Any]:
    cond = {"field_scores": scores, "overall_score": 0.0, "overall_score_strict": 0.0}
    return {
        "id": rid,
        "gt_language": "hi-en",
        "detected_language": "hi-en",
        "as_deployed": cond,
        "language_forced": cond,
        "unresolved_fields": list(unresolved),
        "exposure": list(exposure),
    }


# `product` scores 1.0 where the gold is a split default (abstained correctly by luck) and
# `pros` scores 0.0 where the gold is an empty default: excluding them must move product DOWN and
# pros UP, and the headline must be the unexposed + split-excluded cell.
RECORDS = [
    _rec(
        "a",
        {"stars": 1.0, "product": 1.0, "pros": 0.0, "sentiment": 1.0},
        unresolved=("product", "pros"),
    ),
    _rec("b", {"stars": 1.0, "product": 0.0, "pros": 1.0, "sentiment": 1.0}),
    _rec(
        "c",
        {"stars": 1.0, "product": 1.0, "pros": 1.0, "sentiment": 0.0},
        exposure=("benchmark_gold",),
    ),
    _rec("d", {"stars": 1.0, "product": 0.0, "pros": 0.0, "sentiment": 1.0}),
]


class TestSummarizeGrid:
    def test_grid_cells_and_published_headline(self) -> None:
        out = summarize(RECORDS)
        g = out["headline_grid"]
        assert g["all_reviews_all_pairs"]["n"] == 4
        assert g["unexposed_all_pairs"]["n"] == 3
        assert g["unexposed_all_pairs"]["n_reviews_excluded_as_exposed"] == 1
        assert g["all_reviews_split_excluded"]["n_split_pairs_excluded"] == 2
        published = g["unexposed_split_excluded"]["as_deployed"]["score"]
        assert out["overall_score_headline"]["as_deployed"] == published
        assert out["overall_score_headline_ci_95"]["as_deployed"]["n"] == 3
        assert out["headline_policy"]["cell"] == "unexposed_split_excluded"
        # a: only `sentiment` survives (1.0); b: (0+1+1)/3; d: (0+0+1)/3
        expected = (1.0 + 2 / 3 + 1 / 3) / 3
        assert published == pytest.approx(expected)

    def test_split_exclusion_is_symmetric_per_field(self) -> None:
        eff = summarize(RECORDS)["per_field_split_effect"]
        assert eff["product"]["score_excluding_split"] < eff["product"]["score_all_pairs"]
        assert eff["pros"]["score_excluding_split"] > eff["pros"]["score_all_pairs"]
        assert eff["sentiment"]["n_split_gold"] == 0

    def test_exposure_sensitivity_reports_a_signed_difference_with_ci(self) -> None:
        s = summarize(RECORDS)["exposure_sensitivity"]
        assert s["n_exposed"] == 1 and s["n_unexposed"] == 3
        lo, hi = s["difference_ci_95"]["lower"], s["difference_ci_95"]["upper"]
        assert lo <= s["difference"] <= hi

    def test_records_without_the_new_keys_still_summarize(self) -> None:
        legacy = [
            {k: v for k, v in r.items() if k not in ("unresolved_fields", "exposure")}
            for r in RECORDS
        ]
        out = summarize(legacy)
        assert (
            out["headline_grid"]["all_reviews_all_pairs"]
            == out["headline_grid"]["unexposed_split_excluded"]
        )


class TestRendererShowsEveryCellAndTheDownField:
    def test_render(self) -> None:
        data = summarize(RECORDS)
        data.update(
            {
                "generated_at": "2026-09-21T00:00:00Z",
                "git_sha": "deadbeefdeadbeef",
                "groq_model_small": "s",
                "groq_model_large": "l",
                "language_label_agreement": 0.5,
                "language_label_agreement_wilson_95": {"lower": 0.4, "upper": 0.6, "n": 4},
                "language_label_alpha": 0.38,
                "language_label_alpha_source": "x",
                "n_fixtures": 4,
            }
        )
        md = render_held_out_table_md(data)
        for cell_label in ("| all | all |", "| all | split excluded |", "| unseen only | all |"):
            assert cell_label in md
        assert "unseen only (headline)" in md
        assert "| `product` | 1 |" in md  # the field whose score goes down is listed
        assert "never seen" not in md.lower()
