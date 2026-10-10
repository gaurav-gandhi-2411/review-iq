"""Clean-stream loading, qualification, splitting and timestamp jitter (spec sections 3 and 4).

Corpora (licensed, on disk, ASSUMED ORGANIC):
  Amazon Fine Food Reviews, CC0-1.0.
  Sephora Skincare Reviews, CC BY 4.0, Melissa Monfared (Kaggle sephora-skincare-reviews).
Both carry day-resolution dates only (every timestamp is 00:00); intraday time is jittered
U(0, 24h) with a seeded generator, an assumption stated in the spec.
"""

from __future__ import annotations

import glob
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from app.core.detectors.campaign_signals import Review

AMAZON_CSV = Path(r"D:\ml-cache\review-corpus\snap_amazon-fine-food-reviews\Reviews.csv")
SEPHORA_GLOB = r"D:\ml-cache\review-corpus\melissamonfared_sephora-skincare-reviews\reviews_*.csv"
MIN_REVIEWS = 300
MIN_MONTHS = 24
SEED = 42
DAY_S = 86400.0


@dataclass
class Stream:
    key: str
    corpus: str
    ts: np.ndarray  # float64 epoch seconds, jittered, sorted ascending
    rating: np.ndarray  # int8, aligned with ts
    text: list[str]  # aligned with ts

    def __len__(self) -> int:
        return len(self.ts)

    def reviews(self, lo: int = 0, hi: int | None = None) -> list[Review]:
        hi = len(self) if hi is None else hi
        return [
            Review(
                review_id=f"{self.key}#{i}",
                product_id=self.key,
                timestamp=datetime.fromtimestamp(float(self.ts[i]), tz=UTC),
                text=self.text[i],
                rating=int(self.rating[i]),
            )
            for i in range(lo, hi)
        ]

    def warmup_time(self, warmup_reviews: int = 100, warmup_days: int = 60) -> float:
        return max(
            float(self.ts[min(warmup_reviews, len(self) - 1)]), self.ts[0] + warmup_days * DAY_S
        )


def _qualify(df: pd.DataFrame, corpus: str) -> dict[str, Stream]:
    """df columns: pid, day (epoch seconds at midnight), rating, text. Keeps qualifying products."""
    df = df[df.text.notna() & (df.text.str.strip() != "") & df.rating.notna()]
    counts = df.groupby("pid").size()
    df = df[df.pid.isin(counts[counts >= MIN_REVIEWS].index)]
    out: dict[str, Stream] = {}
    for pid, g in df.groupby("pid", sort=True):
        g = g.sort_values("day", kind="stable")
        months = pd.to_datetime(g.day, unit="s").dt.to_period("M").nunique()
        if len(g) < MIN_REVIEWS or months < MIN_MONTHS:
            continue
        key = f"{corpus}:{pid}"
        rng = np.random.default_rng(SEED + zlib.crc32(key.encode()))
        ts = g.day.to_numpy(dtype=np.float64) + rng.uniform(0, DAY_S, size=len(g))
        order = np.argsort(ts, kind="stable")
        out[key] = Stream(
            key=key,
            corpus=corpus,
            ts=ts[order],
            rating=g.rating.to_numpy(dtype=np.int8)[order],
            text=[g.text.iloc[j] for j in order],
        )
    return out


def load_amazon() -> dict[str, Stream]:
    df = pd.read_csv(AMAZON_CSV, usecols=["ProductId", "Score", "Time", "Text"])
    df = df.rename(columns={"ProductId": "pid", "Score": "rating", "Time": "day", "Text": "text"})
    df["day"] = (df.day // 86400) * 86400  # defensive: already midnight
    return _qualify(df, "amazon")


def load_sephora() -> dict[str, Stream]:
    parts = [
        pd.read_csv(
            f, usecols=["product_id", "rating", "submission_time", "review_text"], low_memory=False
        )
        for f in sorted(glob.glob(SEPHORA_GLOB))
    ]
    df = pd.concat(parts, ignore_index=True)
    # total_seconds, not astype(int64): pandas 3 parses to datetime64[us], not [ns]
    df["day"] = (pd.to_datetime(df.submission_time) - pd.Timestamp(0)).dt.total_seconds()
    df = df.rename(columns={"product_id": "pid", "review_text": "text"})
    return _qualify(df[["pid", "rating", "day", "text"]], "sephora")


def split_keys(keys_by_corpus: dict[str, list[str]], seed: int = SEED) -> dict[str, list[str]]:
    """Per corpus: sort keys, permute with default_rng(seed), 50% tuning / 20% validation /
    30% sealed (spec section 3). Sealed products are untouched until the final run."""
    out: dict[str, list[str]] = {"tuning": [], "validation": [], "sealed": []}
    for corpus in sorted(keys_by_corpus):
        keys = sorted(keys_by_corpus[corpus])
        perm = np.random.default_rng(seed).permutation(len(keys))
        n_tune, n_val = round(0.5 * len(keys)), round(0.2 * len(keys))
        for rank, idx in enumerate(perm):
            part = (
                "tuning" if rank < n_tune else "validation" if rank < n_tune + n_val else "sealed"
            )
            out[part].append(keys[int(idx)])
    return {k: sorted(v) for k, v in out.items()}
