"""Unit tests for eval/data/sample_flipkart.py's Hinglish detector.

Session 8 P3 regression coverage: the weak-marker list used to contain "superb" and
"value for money" -- both pure standard English, not Hindi/Hinglish markers -- which
were confirmed (manual sampling) to false-positive 100% of the time they alone
triggered a hi-en classification. Both were removed; these tests lock that in.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "eval" / "data" / "sample_flipkart.py"
_spec = importlib.util.spec_from_file_location("sample_flipkart", _MODULE_PATH)
sample_flipkart = importlib.util.module_from_spec(_spec)
sys.modules["sample_flipkart"] = sample_flipkart
_spec.loader.exec_module(sample_flipkart)


class TestDetectLanguage:
    def test_pure_english_with_superb_is_not_hinglish(self):
        # Regression: "superb" alone used to trigger a false hi-en classification.
        text = "It's an awesome product, well designed, superb sound quality and bass."
        assert sample_flipkart._detect_language(text) == "en"

    def test_pure_english_value_for_money_is_not_hinglish(self):
        # Regression: the "value for money" idiom used to trigger a false hi-en
        # classification on its own.
        text = "Nice product, great value for money and good build quality overall."
        assert sample_flipkart._detect_language(text) == "en"

    def test_devanagari_script_is_hindi(self):
        assert sample_flipkart._detect_language("यह उत्पाद बहुत अच्छा है और मुझे पसंद आया") == "hi"

    def test_strong_hinglish_marker_alone_is_sufficient(self):
        assert (
            sample_flipkart._detect_language("Bilkul paisa vasool product, bahut acha hai")
            == "hi-en"
        )

    def test_real_codemixed_review_is_hinglish(self):
        # A real sample from the corpus (Session 8 P3 verification sampling).
        text = "mujhe bhot accha lga first time good from flipkart"
        assert sample_flipkart._detect_language(text) == "hi-en"

    def test_short_text_is_other(self):
        assert sample_flipkart._detect_language("ok") == "other"

    def test_plain_english_review_is_english(self):
        text = "The battery life on this phone is excellent and the camera quality is great."
        assert sample_flipkart._detect_language(text) == "en"
