"""Product rule (docs/specs/campaign-detection.md, section 0): the detector flags PATTERNS
across reviews (counts, windows, shared text). It never labels an individual review as fake or
inauthentic. Every user-facing string it can emit is checked here; a new emitted string that
breaks the rule fails this file.

Surfaces covered: alert subjects (both emoji variants), alert body, every explanation the
detector can build (all 2^5 subsets of fired signals), the full to_dict() payload (keys and
values) for real scans, and every non-docstring string literal in the detector modules.
"""

from __future__ import annotations

import ast
import itertools
import random
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from app.core.alerts import engine
from app.core.alerts.rules import AlertEvent, AlertEventType
from app.core.detectors import campaign, campaign_signals
from app.core.detectors.campaign import (
    SIGNAL_BURST,
    SIGNAL_MISMATCH,
    SIGNAL_RATING,
    SIGNAL_TEMPLATE,
    SIGNAL_VERIFIED,
    CampaignParams,
    ProductStream,
    Review,
    WindowEvidence,
    build_explanation,
    flag_from_alert,
    scan_stream,
)

# Stricter than the spec's list on purpose: also bans words that imply intent or a verdict.
BANNED = re.compile(
    r"fake|fraud|\bbots?\b|inauthentic|not genuine|\bgenuine|scam|counterfeit|spam|fabricat|"
    r"astroturf|paid review|coordinated|campaign|deceptive|dishonest|not real|illegitimate",
    re.IGNORECASE,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)
VOCAB = [f"word{k}x" for k in range(400)]
TEMPLATE = "this product exceeded my expectations completely and arrived very quickly"
ALL_SIGNALS = [SIGNAL_BURST, SIGNAL_TEMPLATE, SIGNAL_RATING, SIGNAL_MISMATCH, SIGNAL_VERIFIED]


def _strings(obj: object) -> list[str]:
    """Every string in a nested payload, dict keys included."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in _strings(k) + _strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings(v)]
    return []


def _assert_clean(texts: list[str], where: str) -> None:
    assert texts, f"no strings collected from {where}"
    bad = [t for t in texts if BANNED.search(t)]
    assert not bad, f"banned wording in {where}: {bad}"


def _stream_with_pattern(rated: bool, verified: bool | None, same_text: bool) -> ProductStream:
    rng = random.Random(3)
    reviews = [
        Review(
            f"b{i}",
            "P",
            T0 + timedelta(days=i),
            " ".join(rng.choice(VOCAB) for _ in range(9)),
            rng.choice([4, 5, 5, 5]) if rated else None,
            verified,
        )
        for i in range(240)
    ]
    start = T0 + timedelta(days=200)
    reviews += [
        Review(
            f"c{i}",
            "P",
            start + timedelta(minutes=20 * i),
            TEMPLATE if same_text else " ".join(rng.choice(VOCAB) for _ in range(9)),
            1 if rated else None,
            False if verified is not None else None,
        )
        for i in range(14)
    ]
    return ProductStream(reviews, CampaignParams(use_mismatch=True))


def _flags() -> list[campaign.CampaignFlag]:
    out = []
    for rated, verified, same in itertools.product([True, False], [None, True], [True, False]):
        stream = _stream_with_pattern(rated, verified, same)
        out += [flag_from_alert(stream, "P", a) for a in scan_stream(stream)]
    return out


def test_scenarios_actually_produce_alerts() -> None:
    assert len(_flags()) >= 4, "the wording checks below would be vacuous without real alerts"


def test_explanation_wording_for_every_subset_of_fired_signals() -> None:
    ev = WindowEvidence(
        index=10, width_hours=6, n=14, expected=0.4, burst=9.0, template_k=9,
        template_members=(1, 2), rating_z=-8.0, window_mean=1.0, history_mean=4.6,
        mismatch_z=5.0, mismatch_count=4, verified_z=4.0, verified_window_share=0.1,
        verified_history_share=0.9,
    )  # fmt: skip
    texts = []
    for r in range(len(ALL_SIGNALS) + 1):
        for subset in itertools.combinations(ALL_SIGNALS, r):
            texts.append(build_explanation(ev, subset))
    _assert_clean(texts, "build_explanation")
    for w in (6, 24, 72, 168):
        _assert_clean([campaign.window_label(w)], "window_label")


def test_explanation_describes_counts_windows_and_shared_text() -> None:
    ev = WindowEvidence(
        index=10, width_hours=24, n=14, expected=1.2, burst=9.0, template_k=9,
        template_members=(), rating_z=-8.0, window_mean=1.0, history_mean=4.6,
        mismatch_z=None, mismatch_count=0, verified_z=None, verified_window_share=None,
        verified_history_share=None,
    )  # fmt: skip
    text = build_explanation(ev, [SIGNAL_BURST, SIGNAL_TEMPLATE, SIGNAL_RATING])
    assert "14 reviews in the last 24 hours" in text
    assert "9 of them share near-identical text" in text
    assert "1.0 versus 4.6" in text


def test_payload_strings_and_keys_are_clean_and_carry_no_per_review_verdict() -> None:
    allowed_top = {
        "product_id", "review_count", "window_label", "signals_fired", "explanation", "evidence",
    }  # fmt: skip
    allowed_evidence = {
        "window", "window_hours", "review_count", "expected_count", "burst_strength",
        "near_identical_group_size", "window_mean_rating", "history_mean_rating", "review_ids",
        "near_identical_review_ids",
    }  # fmt: skip
    for flag in _flags():
        d = flag.to_dict()
        assert set(d) == allowed_top
        assert set(d["evidence"]) == allowed_evidence
        _assert_clean([s for s in _strings(d) if s not in d["evidence"]["review_ids"]], "payload")
        assert all(sig in ALL_SIGNALS for sig in d["signals_fired"])


@pytest.mark.parametrize("emoji", [True, False])
def test_alert_subject_and_body_are_clean(emoji: bool) -> None:
    class _S:
        alert_subject_emoji_enabled = emoji

    texts = []
    for flag in _flags():
        event = AlertEvent(event_type=AlertEventType.FAKE_CAMPAIGN, details=flag.to_dict())
        with patch.object(engine, "get_settings", return_value=_S()):
            subject = engine._format_subject(event)
        body = engine._format_body("org", "fake_campaign:P:2024-07-18", None, event, None)
        assert flag.product_id in subject and str(flag.review_count) in subject
        texts += [subject, body]
    # the raw templates too, so a malformed event (which falls back to a template) is covered
    for table in (engine._SUBJECT_TEMPLATES, engine._SUBJECT_TEMPLATES_NO_EMOJI):
        texts.append(table[AlertEventType.FAKE_CAMPAIGN])
    _assert_clean(texts, "alert subject/body")


def test_no_individual_review_verdict_api_exists() -> None:
    public = [n for m in (campaign, campaign_signals) for n in dir(m) if not n.startswith("_")]
    assert not [n for n in public if re.search(r"fake|authentic|genuine|fraud|bot", n, re.I)]


@pytest.mark.parametrize("module", [campaign, campaign_signals])
def test_every_string_literal_in_the_detector_modules_is_clean(module: object) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    _assert_clean(literals, f"string literals of {module.__name__}")  # type: ignore[attr-defined]
