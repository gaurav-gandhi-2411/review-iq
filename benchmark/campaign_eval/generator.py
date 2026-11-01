"""SYNTHETIC campaign generator for the pre-registered evaluation (docs/specs/campaign-detection.md).

Everything this module produces is SYNTHETIC: it tests the attacks modelled here (size, duration,
text similarity, rating skew), not unseen ones. No corpus text is copied; the templates, slot
vocabularies and paraphrase operations below are written for this module and kept domain-neutral
because the clean streams span food and skincare.

Similarity levels
  near-identical  one template per polarity, 0-2 small edits (punctuation, one swapped word)
  paraphrased     one template per polarity, 3-5 synonym / clause-reorder / filler / drop edits
  independent     each review composed separately from slot vocabularies (organic-level overlap)
Rating skew: all-1-star, all-5-star, mixed (each review independently 1 or 5, p = 0.5). Text
polarity follows each review's rating. For `mixed` with near-identical or paraphrased text there
is one template per polarity, so two groups of roughly N/2 form.
"""

from __future__ import annotations

import itertools
import random
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from app.core.detectors.campaign_signals import Review

SIZES = (5, 8, 12, 20, 35, 50)
DURATIONS_H = (1, 6, 24, 72, 168)
SIMILARITIES = ("independent", "paraphrased", "near-identical")
SKEWS = ("all-1", "all-5", "mixed")

POS_TEMPLATES = (
    "I have been using this for a few weeks now and it has exceeded every expectation I had, "
    "highly recommended to anyone looking for something reliable",
    "Absolutely love this purchase, the quality is outstanding and it arrived much sooner than "
    "promised, I will be ordering again soon",
    "Great value for the money, works exactly as described and my whole family is delighted with "
    "it, five stars from me",
    "Honestly the best one I have tried so far, it feels well made and the results speak for "
    "themselves, worth every penny",
    "Fast delivery and perfect packaging, the product itself is even better than the pictures, "
    "I keep recommending it to my friends",
    "Exactly what I was searching for, simple to use and noticeably better than the one I used "
    "before, very happy with this order",
    "Wonderful product and wonderful service, everything was spot on from checkout to delivery, "
    "I could not be more satisfied",
    "I was skeptical at first but this turned out to be fantastic, it does everything it promises "
    "and more, a definite repeat purchase",
)
NEG_TEMPLATES = (
    "I regret buying this, the quality is far below what the description promised and it stopped "
    "working properly within a few days, do not waste your money",
    "Very disappointed with this order, it arrived late and does nothing like the pictures "
    "suggest, I am asking for a refund",
    "Terrible value for the money, it feels cheap and flimsy and my whole family is unhappy with "
    "it, one star from me",
    "Honestly the worst one I have tried so far, it feels badly made and the results are a "
    "joke, avoid at all costs",
    "Slow delivery and sloppy packaging, the product itself is much worse than the pictures, I "
    "keep warning my friends about it",
    "Nothing like what I was searching for, awkward to use and noticeably worse than the one I "
    "used before, very unhappy with this order",
    "Awful product and awful service, everything went wrong from checkout to delivery, I could "
    "not be more frustrated",
    "I was hopeful at first but this turned out to be a letdown, it fails at everything it "
    "promises, a definite never again",
)

SYNONYMS = {
    "love": ("adore", "really like", "am thrilled with"),
    "great": ("excellent", "superb", "really good"),
    "best": ("finest", "top", "number one"),
    "quality": ("build", "standard", "craftsmanship"),
    "fast": ("quick", "speedy", "prompt"),
    "worth": ("deserving of", "justifying"),
    "family": ("household", "relatives"),
    "recommend": ("suggest", "vouch for"),
    "recommended": ("suggested", "something I vouch for"),
    "wonderful": ("lovely", "terrific"),
    "fantastic": ("brilliant", "amazing"),
    "disappointed": ("let down", "dissatisfied"),
    "terrible": ("dreadful", "poor"),
    "worst": ("poorest", "weakest"),
    "awful": ("dreadful", "lousy"),
    "slow": ("sluggish", "delayed"),
    "cheap": ("shoddy", "flimsy"),
    "unhappy": ("dissatisfied", "displeased"),
    "regret": ("am sorry about", "wish I had skipped"),
    "refund": ("my money back", "a full reimbursement"),
    "product": ("item", "purchase"),
    "order": ("purchase", "delivery"),
    "friends": ("colleagues", "neighbours"),
    "reliable": ("dependable", "solid"),
    "simple": ("easy", "straightforward"),
}
FILLERS = ("honestly", "to be fair", "overall", "in my experience", "frankly", "as a note")

# slot vocabularies for the independent level: (polarity) -> list of slot option tuples
_IND_POS = (
    ("I picked this up", "Bought this", "Ordered this", "Tried this", "Got this", "Purchased this"),
    (
        "after a friend mentioned it",
        "on a whim last month",
        "as a gift for my sister",
        "because the reviews looked decent",
        "during the weekend sale",
        "to replace an old one",
        "for my daily routine",
        "while travelling abroad",
        "for the new apartment",
    ),  # fmt: skip
    (
        "and the finish is lovely",
        "and it fits my needs perfectly",
        "and it lasted well so far",
        "and my kids use it daily",
        "and the price felt fair",
        "and setup took two minutes",
        "and the colour looks richer in person",
        "and nothing about it felt rushed",
        "and it smells pleasant",
        "and the texture is smooth",
    ),  # fmt: skip
    (
        "Would buy again.",
        "Pleasantly surprised.",
        "No complaints at all.",
        "Easy yes from me.",
        "Solid choice.",
        "Happy customer here.",
        "Does the job nicely.",
        "Good decision overall.",
    ),  # fmt: skip
)
_IND_NEG = (
    ("I picked this up", "Bought this", "Ordered this", "Tried this", "Got this", "Purchased this"),
    (
        "after a friend mentioned it",
        "on a whim last month",
        "as a gift for my sister",
        "because the reviews looked decent",
        "during the weekend sale",
        "to replace an old one",
        "for my daily routine",
        "while travelling abroad",
        "for the new apartment",
    ),  # fmt: skip
    (
        "and the finish is already peeling",
        "and it misses what I needed",
        "and it broke early",
        "and my kids stopped using it",
        "and the price felt unfair",
        "and setup was a hassle",
        "and the colour looks dull in person",
        "and everything about it felt rushed",
        "and it smells unpleasant",
        "and the texture is rough",
    ),  # fmt: skip
    (
        "Would not buy again.",
        "Unpleasantly surprised.",
        "Many complaints.",
        "Hard no from me.",
        "Poor choice.",
        "Unhappy customer here.",
        "Fails at the basics.",
        "Bad decision overall.",
    ),  # fmt: skip
)


@dataclass(frozen=True)
class CampaignConfig:
    size: int
    duration_h: int
    similarity: str
    skew: str

    @property
    def config_id(self) -> str:
        return f"n{self.size}-d{self.duration_h}h-{self.similarity}-{self.skew}"

    @property
    def type_id(self) -> str:
        return f"{self.similarity}/{self.skew}"


def all_configs() -> list[CampaignConfig]:
    return [
        CampaignConfig(n, d, sim, sk)
        for n, sim, d, sk in itertools.product(SIZES, SIMILARITIES, DURATIONS_H, SKEWS)
    ]


def split_configs(seed: int = 42) -> dict[str, list[CampaignConfig]]:
    """Per (size, similarity) cell permute the 15 (duration, skew) combinations with
    default_rng(seed): first 5 SEALED, next 3 VALIDATION, last 7 TUNING (spec section 4)."""
    rng = np.random.default_rng(seed)
    out: dict[str, list[CampaignConfig]] = {"sealed": [], "validation": [], "tuning": []}
    for n, sim in itertools.product(SIZES, SIMILARITIES):
        cell = [CampaignConfig(n, d, sim, sk) for d, sk in itertools.product(DURATIONS_H, SKEWS)]
        perm = rng.permutation(len(cell))
        for rank, idx in enumerate(perm):
            part = "sealed" if rank < 5 else "validation" if rank < 8 else "tuning"
            out[part].append(cell[int(idx)])
    return out


def _edit_word(words: list[str], rng: random.Random) -> bool:
    cands = [i for i, w in enumerate(words) if w.lower().strip(",.") in SYNONYMS]
    if not cands:
        return False
    i = rng.choice(cands)
    core = words[i].lower().strip(",.")
    tail = words[i][len(words[i].rstrip(",.")) :]
    words[i] = rng.choice(SYNONYMS[core]) + tail
    return True


def _near_identical(template: str, rng: random.Random) -> str:
    words = template.split()
    for _ in range(rng.randint(0, 2)):
        op = rng.choice(("syn", "punct", "filler"))
        if op == "syn":
            _edit_word(words, rng)
        elif op == "punct":
            words[-1] = words[-1].rstrip(".!") + rng.choice((".", "!", ""))
        else:
            words.append(rng.choice(("overall", "really", "truly")))
    return " ".join(words)


def _paraphrase(template: str, rng: random.Random) -> str:
    clauses = [c.strip() for c in template.split(",")]
    for _ in range(rng.randint(3, 5)):
        op = rng.choice(("syn", "syn", "reorder", "filler", "drop"))
        if op == "syn":
            k = rng.randrange(len(clauses))
            words = clauses[k].split(" ")
            if _edit_word(words, rng):
                clauses[k] = " ".join(words)
        elif op == "reorder" and len(clauses) > 1:
            rng.shuffle(clauses)
        elif op == "filler":
            clauses.insert(rng.randrange(len(clauses) + 1), rng.choice(FILLERS))
        elif op == "drop" and len(clauses) > 2:
            clauses.pop(rng.randrange(len(clauses)))
    return ", ".join(clauses)


def _independent(positive: bool, rng: random.Random) -> str:
    slots = _IND_POS if positive else _IND_NEG
    parts = [rng.choice(s) for s in slots]
    return f"{parts[0]} {parts[1]} {parts[2]}. {parts[3]}"


def generate_campaign(
    cfg: CampaignConfig, start: datetime, seed: int, product_id: str = "P"
) -> list[Review]:
    """One campaign: `cfg.size` reviews spread uniformly over `cfg.duration_h` hours from `start`."""
    rng = random.Random(seed)
    pos_template = rng.choice(POS_TEMPLATES)
    neg_template = rng.choice(NEG_TEMPLATES)
    offsets = sorted(rng.uniform(0, cfg.duration_h * 3600) for _ in range(cfg.size))
    out = []
    for i, off in enumerate(offsets):
        if cfg.skew == "all-1":
            rating = 1
        elif cfg.skew == "all-5":
            rating = 5
        else:
            rating = rng.choice((1, 5))
        positive = rating == 5
        if cfg.similarity == "near-identical":
            text = _near_identical(pos_template if positive else neg_template, rng)
        elif cfg.similarity == "paraphrased":
            text = _paraphrase(pos_template if positive else neg_template, rng)
        else:
            text = _independent(positive, rng)
        out.append(
            Review(
                review_id=f"inj-{cfg.config_id}-{i}",
                product_id=product_id,
                timestamp=start + timedelta(seconds=off),
                text=text,
                rating=rating,
            )
        )
    return out


def injection_seed(stream_key: str, config_id: str, base: int = 42) -> int:
    return base + zlib.crc32(f"{stream_key}|{config_id}".encode())


def utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)
