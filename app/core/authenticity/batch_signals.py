from __future__ import annotations

from datetime import datetime

from app.core.authenticity.schema import AuthenticityFlag


def _shingle(text: str, k: int = 3) -> frozenset[tuple[str, ...]]:
    """Return the set of word k-shingles from text (lowercased, stripped).

    Example: "the quick brown fox" with k=3 →
        {("the", "quick", "brown"), ("quick", "brown", "fox")}
    """
    words = text.lower().split()
    if len(words) < k:
        # Return single shingle of all available words to allow partial matching
        return frozenset([tuple(words)]) if words else frozenset()
    return frozenset(tuple(words[i : i + k]) for i in range(len(words) - k + 1))


def jaccard(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    """Standard Jaccard similarity between two shingle sets.

    Returns 0.0 if both sets are empty.
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return intersection / union


def find_near_duplicates(
    texts: list[str],
    threshold: float = 0.60,
    k: int = 3,
) -> list[tuple[int, int, float]]:
    """O(n^2) shingle+Jaccard duplicate detection over `texts`.

    Returns a list of (i, j, similarity) for pairs with similarity >= threshold.
    i < j is always true.
    """
    shingles = [_shingle(t, k) for t in texts]
    results: list[tuple[int, int, float]] = []
    n = len(shingles)
    for i in range(n):
        for j in range(i + 1, n):
            sim = jaccard(shingles[i], shingles[j])
            if sim >= threshold:
                results.append((i, j, sim))
    return results


def detect_burst(
    dates: list[datetime | None],
    window_days: int = 3,
    min_count: int = 5,
) -> list[tuple[datetime, datetime, int]]:
    """Find time windows containing a suspicious cluster of reviews.

    Given a list of review timestamps (None entries skipped), returns all
    contiguous windows of `window_days` days that contain >= min_count reviews.

    Returns list of (window_start, window_end, count).
    If all dates are None, returns [].
    """
    from datetime import timedelta

    valid: list[datetime] = sorted(d for d in dates if d is not None)
    if not valid:
        return []

    results: list[tuple[datetime, datetime, int]] = []
    seen_windows: set[tuple[datetime, datetime]] = set()
    delta = timedelta(days=window_days)

    for anchor in valid:
        window_end = anchor + delta
        count = sum(1 for d in valid if anchor <= d <= window_end)
        key = (anchor, window_end)
        if count >= min_count and key not in seen_windows:
            seen_windows.add(key)
            results.append((anchor, window_end, count))

    return results


def score_batch(
    texts: list[str],
    dates: list[datetime | None] | None = None,
    *,
    duplicate_threshold: float = 0.60,
    burst_window_days: int = 3,
    burst_min_count: int = 5,
) -> dict[int, list[AuthenticityFlag]]:
    """Run batch-level signals over all texts.

    Returns a dict mapping review index → list of batch-level AuthenticityFlags.
    Flags assigned:
        NEAR_DUPLICATE: both members of each duplicate pair receive this flag.
        REVIEW_BURST: all reviews whose date falls in any detected burst window.
    """
    result: dict[int, list[AuthenticityFlag]] = {i: [] for i in range(len(texts))}

    # Near-duplicate detection
    for i, j, _sim in find_near_duplicates(texts, threshold=duplicate_threshold):
        if AuthenticityFlag.NEAR_DUPLICATE not in result[i]:
            result[i].append(AuthenticityFlag.NEAR_DUPLICATE)
        if AuthenticityFlag.NEAR_DUPLICATE not in result[j]:
            result[j].append(AuthenticityFlag.NEAR_DUPLICATE)

    # Burst detection
    if dates is not None:
        burst_windows = detect_burst(
            dates,
            window_days=burst_window_days,
            min_count=burst_min_count,
        )
        if burst_windows:
            from datetime import timedelta

            for idx, date in enumerate(dates):
                if date is None:
                    continue
                for window_start, _window_end, _count in burst_windows:
                    window_end_dt = window_start + timedelta(days=burst_window_days)
                    if window_start <= date <= window_end_dt:
                        if AuthenticityFlag.REVIEW_BURST not in result[idx]:
                            result[idx].append(AuthenticityFlag.REVIEW_BURST)
                        break

    return result
