"""Unit tests for app.core.language.detect_language."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.language import detect_language

FIXTURES_ROOT = Path(__file__).parent.parent.parent / "eval" / "fixtures"


# ---------------------------------------------------------------------------
# Explicit per-case tests
# ---------------------------------------------------------------------------


class TestDevanagariDetection:
    def test_devanagari_script(self) -> None:
        # Pure Hindi Devanagari
        assert detect_language("यह प्रोडक्ट बहुत अच्छा है") == "hi"

    def test_mixed_devanagari_roman(self) -> None:
        # Devanagari present → always "hi"
        assert detect_language("Boat earphone बहुत अच्छा है") == "hi"

    def test_devanagari_short(self) -> None:
        assert detect_language("अच्छा") == "hi"


class TestHinglishDetection:
    def test_strong_marker_bahut(self) -> None:
        assert detect_language("Bahut accha product hai yaar!") == "hi-en"

    def test_strong_marker_nahi(self) -> None:
        assert detect_language("Quality bilkul nahi hai") == "hi-en"

    def test_strong_marker_paisa_vasool(self) -> None:
        assert detect_language("Paisa vasool product, ekdum sahi") == "hi-en"

    def test_strong_marker_bakwaas(self) -> None:
        assert detect_language("Bilkul bakwaas product hai") == "hi-en"

    def test_multiple_weak_markers(self) -> None:
        # hai + toh + bhi → 3 weak markers
        assert detect_language("Sound quality hai toh aur bhi features add karo") == "hi-en"

    def test_hinglish_review_sample(self) -> None:
        text = (
            "Superb earphone i like it.... ye earphone apple k earphone ko "
            "competition de dega..... Not better than apple earphone... "
            "but km v nai hai apple k earphone se...."
        )
        assert detect_language(text) == "hi-en"


class TestEnglishDetection:
    def test_plain_english(self) -> None:
        assert detect_language("Great product, fast shipping!") == "en"

    def test_english_review_long(self) -> None:
        text = (
            "The suction is incredible—it picked up a penny from under the rug! "
            "And it's super quiet. But the battery life is a joke. It died after 15 minutes."
        )
        assert detect_language(text) == "en"

    def test_english_with_numbers(self) -> None:
        assert detect_language("Paid $300 for this and it broke in 2 weeks") == "en"

    def test_english_technical_review(self) -> None:
        assert detect_language("Excellent noise cancellation, Bluetooth 5.0, 30hr battery") == "en"


class TestVernacularBenchmarkFix:
    """Pins the 2026-07-08 _STRONG_HINGLISH fix from the vernacular silver benchmark.

    Evidence: 58 real Flipkart reviews (benchmark/vernacular_v2/) were routed to the
    English prompt path because these spelling variants were absent from the detector.
    Two-directional requirement: recall must go up on real misses WITHOUT creating new
    false positives on genuine English reviews (see negative-control class below).
    """

    def test_wasool_variant(self) -> None:
        text = (
            "Just amazing, go for it. The air is so fresh and cool as well as with "
            "good air throw. Paisa wasool buy."
        )
        assert detect_language(text) == "hi-en"

    def test_wasul_variant(self) -> None:
        assert detect_language("Awesome, PESA wasul") == "hi-en"

    def test_washul_variant(self) -> None:
        text = "very nice cooler.very smooth and fast. no sound. cooling fast. paisa washul."
        assert detect_language(text) == "hi-en"

    def test_faltu(self) -> None:
        assert detect_language("I'm not happy, faltu quality.") == "hi-en"

    def test_ghatiya(self) -> None:
        text = (
            "ghatiya tv bluetooth doesnt work with soundbarvideo shuts without "
            "reasonno proper testing done by xiaomipathetic purchase dont buy"
        )
        assert detect_language(text) == "hi-en"

    def test_sasta_tikau_sunder_combo(self) -> None:
        assert detect_language("Sasta, sunder, tikau as per name really good") == "hi-en"

    def test_milega(self) -> None:
        text = (
            "at this price kya milega 18000 is worth buy value for money trend "
            "of family changing each room seperate"
        )
        assert detect_language(text) == "hi-en"


class TestVernacularBenchmarkNoOverRouting:
    """No-over-routing guarantee: the fix must not flag genuine English as hi-en/hi.

    Sample drawn from the same benchmark's 49-review negative-control set (slice="en",
    multi-LLM silver consensus LANG="en") — all 49 were re-verified to still detect as
    "en" after the fix; this class pins a representative subset so a future regex change
    that starts over-routing English fails CI immediately.
    """

    def test_emoji_only_review(self) -> None:
        assert detect_language("Awesome looking great product  ????????") == "en"

    def test_perfume_review(self) -> None:
        text = (
            "Smell's very nice... Perfume spray bottle was a very unique, but its "
            "not long lasting its very average"
        )
        assert detect_language(text) == "en"

    def test_short_typo_review(self) -> None:
        assert detect_language("nice bokk") == "en"

    def test_autocut_review(self) -> None:
        text = (
            "autocut button stopped working within 2 weeks of use now the device "
            "has no local control as it heats"
        )
        assert detect_language(text) == "en"

    def test_kid_love_review(self) -> None:
        assert detect_language("Absolutely worth it.My kid love this product") == "en"

    def test_beautiful_review(self) -> None:
        assert detect_language("it is soo beautiful and i love it soo much") == "en"

    def test_wasted_product_short(self) -> None:
        assert detect_language("Wasted product") == "en"


class TestEdgeCases:
    def test_too_short_returns_other(self) -> None:
        assert detect_language("ok") == "other"

    def test_empty_string_returns_other(self) -> None:
        assert detect_language("") == "other"

    def test_whitespace_only_returns_other(self) -> None:
        assert detect_language("   ") == "other"

    def test_three_char_returns_other(self) -> None:
        assert detect_language("yes") == "other"

    def test_five_char_english(self) -> None:
        # Just above min threshold
        result = detect_language("hello")
        assert result in ("en", "other")  # lingua may be unsure


# ---------------------------------------------------------------------------
# Accuracy test against real fixture texts
# ---------------------------------------------------------------------------


def _load_fixture_texts(subdir: str | None, expected_lang: str) -> list[tuple[str, str]]:
    """Load (review_text, expected_lang) pairs from fixture files."""
    if subdir is None:
        # Flat root — English fixtures
        paths = sorted(FIXTURES_ROOT.glob("*.json"))
    else:
        paths = sorted((FIXTURES_ROOT / subdir).glob("*.json"))

    samples = []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            text = data.get("review_text", "")
            if len(text) >= 5:
                samples.append((text, expected_lang))
        except Exception:
            pass
    return samples


class TestAccuracyOnFixtures:
    """Accuracy gate: >=95% correct detection across all language categories."""

    def _run_accuracy(self, samples: list[tuple[str, str]]) -> float:
        correct = sum(1 for text, lang in samples if detect_language(text) == lang)
        return correct / len(samples) if samples else 0.0

    def test_english_fixture_accuracy(self) -> None:
        samples = _load_fixture_texts(None, "en")
        assert len(samples) >= 10, "Need >=10 English samples"
        acc = self._run_accuracy(samples)
        assert acc >= 0.90, f"English detection accuracy {acc:.0%} < 90% (samples={len(samples)})"

    def test_hindi_fixture_accuracy(self) -> None:
        samples = _load_fixture_texts("hi", "hi")
        assert len(samples) >= 4, "Need >=4 Hindi samples"
        acc = self._run_accuracy(samples)
        assert acc >= 0.95, f"Hindi detection accuracy {acc:.0%} < 95% (samples={len(samples)})"

    def test_hinglish_fixture_accuracy(self) -> None:
        samples = _load_fixture_texts("hi-en", "hi-en")
        assert len(samples) >= 10, "Need >=10 Hinglish samples"
        acc = self._run_accuracy(samples)
        assert acc >= 0.75, f"Hinglish detection accuracy {acc:.0%} < 75% (samples={len(samples)})"

    def test_overall_accuracy_across_all_languages(self) -> None:
        en = _load_fixture_texts(None, "en")
        hi = _load_fixture_texts("hi", "hi")
        hi_en = _load_fixture_texts("hi-en", "hi-en")
        all_samples = en + hi + hi_en
        assert len(all_samples) >= 30, f"Need >=30 total samples, got {len(all_samples)}"
        acc = self._run_accuracy(all_samples)
        assert acc >= 0.85, f"Overall detection accuracy {acc:.0%} < 85% (n={len(all_samples)})"
