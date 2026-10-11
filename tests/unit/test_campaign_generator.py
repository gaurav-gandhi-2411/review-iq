"""Tests for the SYNTHETIC campaign generator (benchmark/campaign_eval/generator.py)."""

from __future__ import annotations

import itertools
import statistics
from datetime import UTC, datetime, timedelta

import pytest
from app.core.detectors.campaign_signals import CampaignParams, _shingles, jaccard
from benchmark.campaign_eval.generator import (
    NEG_TEMPLATES,
    POS_TEMPLATES,
    CampaignConfig,
    all_configs,
    generate_campaign,
    split_configs,
)

T = datetime(2024, 1, 1, tzinfo=UTC)
_ANY_LEN = CampaignParams(min_words_template=1, min_chars_template=1)


def _pair_sims(sim: str, skew: str = "all-5", seeds: int = 15) -> list[float]:
    out: list[float] = []
    for seed in range(seeds):
        c = generate_campaign(CampaignConfig(20, 24, sim, skew), T, seed)
        sh = [_shingles(r.text, _ANY_LEN) for r in c]
        out += [jaccard(a, b) for a, b in itertools.combinations(sh, 2) if a and b]
    return out


def test_grid_and_split_are_the_pre_registered_sizes_and_disjoint() -> None:
    assert len(all_configs()) == 270
    parts = split_configs(42)
    assert [len(parts[k]) for k in ("sealed", "validation", "tuning")] == [90, 54, 126]
    ids = [c.config_id for k in parts for c in parts[k]]
    assert len(ids) == len(set(ids)) == 270
    again = split_configs(42)
    assert [c.config_id for c in again["sealed"]] == [c.config_id for c in parts["sealed"]]
    # every (size, similarity) cell contributes 5 sealed configurations
    cells = {(c.size, c.similarity) for c in parts["sealed"]}
    assert len(cells) == 18


@pytest.mark.parametrize("skew,ratings", [("all-1", {1}), ("all-5", {5}), ("mixed", {1, 5})])
def test_ratings_follow_skew_and_times_fit_the_duration(skew: str, ratings: set[int]) -> None:
    c = generate_campaign(CampaignConfig(35, 6, "paraphrased", skew), T, 7)
    assert len(c) == 35 and {r.rating for r in c} == ratings
    ts = [r.timestamp for r in c]
    assert ts == sorted(ts) and ts[0] >= T and ts[-1] <= T + timedelta(hours=6)


def test_generation_is_deterministic_and_seed_sensitive() -> None:
    cfg = CampaignConfig(12, 24, "paraphrased", "mixed")
    a, b = generate_campaign(cfg, T, 1), generate_campaign(cfg, T, 1)
    assert [(r.text, r.rating, r.timestamp) for r in a] == [
        (r.text, r.rating, r.timestamp) for r in b
    ]
    c = generate_campaign(cfg, T, 2)
    assert [r.text for r in a] != [r.text for r in c]


def test_similarity_levels_are_ordered_and_in_the_documented_bands() -> None:
    ind, par, nid = (_pair_sims(s) for s in ("independent", "paraphrased", "near-identical"))
    assert statistics.mean(ind) < 0.2
    assert sum(j >= 0.6 for j in ind) / len(ind) < 0.01  # independent text barely ever clusters
    assert 0.3 <= statistics.mean(par) <= 0.7
    assert statistics.mean(nid) > 0.8 and min(nid) > 0.5
    assert statistics.mean(ind) < statistics.mean(par) < statistics.mean(nid)


def test_mixed_skew_templates_split_by_polarity() -> None:
    c = generate_campaign(CampaignConfig(50, 24, "near-identical", "mixed"), T, 3)
    pos = [r.text for r in c if r.rating == 5]
    neg = [r.text for r in c if r.rating == 1]
    assert pos and neg
    sp = [_shingles(t, _ANY_LEN) for t in pos]
    sn = [_shingles(t, _ANY_LEN) for t in neg]
    assert min(jaccard(a, b) for a in sp for b in sn if a and b) < 0.3  # groups do not merge


def test_template_pools_have_no_duplicates() -> None:
    assert len(set(POS_TEMPLATES)) == len(POS_TEMPLATES) == len(NEG_TEMPLATES)
    assert len(set(NEG_TEMPLATES)) == len(NEG_TEMPLATES)
