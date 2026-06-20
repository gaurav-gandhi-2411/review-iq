"""Majority-class baseline — always predicts the mode label for each task.

This is the floor: a system that ignores the review text entirely.
The mode is computed from the gold label distribution at inference time.
"""

from __future__ import annotations

from collections import Counter


SYSTEM_ID = "majority-baseline"
SYSTEM_DESCRIPTION = (
    "Majority-class baseline: predicts the mode label from the gold distribution "
    "for each task, independent of review text. This is the floor — a system with "
    "no understanding of reviews would score at this level."
)


class MajorityBaseline:
    """Predicts the majority class for SENT, URG, LANG independently."""

    def __init__(self, gold_records: list[dict]) -> None:
        sent_counts: Counter[str] = Counter()
        urg_counts: Counter[str] = Counter()
        lang_counts: Counter[str] = Counter()
        for r in gold_records:
            g = r.get("gold", {})
            if g.get("SENT"):
                sent_counts[g["SENT"]] += 1
            if g.get("URG"):
                urg_counts[g["URG"]] += 1
            if g.get("LANG"):
                lang_counts[g["LANG"]] += 1
        self._majority_sent = sent_counts.most_common(1)[0][0] if sent_counts else "positive"
        self._majority_urg = urg_counts.most_common(1)[0][0] if urg_counts else "low"
        self._majority_lang = lang_counts.most_common(1)[0][0] if lang_counts else "en"

    def predict(self, _text: str) -> dict[str, str]:
        return {
            "SENT": self._majority_sent,
            "URG": self._majority_urg,
            "LANG": self._majority_lang,
        }

    def majority_labels(self) -> dict[str, str]:
        return {
            "SENT": self._majority_sent,
            "URG": self._majority_urg,
            "LANG": self._majority_lang,
        }
