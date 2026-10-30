"""Dataset adapters. Every adapter returns Example rows; the engine never sees dataset specifics.

Raw files live outside the repo (ENGINE_DATA_DIR, default ../engine-data): CLINC150 data_full.json,
BANKING77 train/test csv, MASSIVE 1.0 jsonl per locale. See docs/specs/classification-engine.md for
the verified licenses (all CC BY: attribution required).
"""

from __future__ import annotations

import csv
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(
    os.environ.get("ENGINE_DATA_DIR", Path(__file__).resolve().parents[2] / "engine-data")
)
SEED = 42


@dataclass(frozen=True)
class Example:
    text: str
    label: str
    lang: str = "en"


@dataclass
class Splits:
    train: list[Example]
    val: list[Example]
    test: list[Example]
    labels: list[str]
    parent: dict[str, str]  # label -> parent (domain/scenario); empty when no hierarchy
    extra: dict[str, list[Example]]  # e.g. CLINC out-of-scope sets


def load_clinc() -> Splits:
    raw = json.loads((DATA_DIR / "clinc" / "data_full.json").read_text(encoding="utf-8"))
    domains = json.loads((DATA_DIR / "clinc" / "domains.json").read_text(encoding="utf-8"))
    parent = {intent: dom for dom, intents in domains.items() for intent in intents}

    def rows(key: str) -> list[Example]:
        return [Example(t, label) for t, label in raw[key]]

    train, val, test = rows("train"), rows("val"), rows("test")
    labels = sorted({e.label for e in train})
    extra = {k: rows(k) for k in ("oos_train", "oos_val", "oos_test")}
    return Splits(train, val, test, labels, parent, extra)


def load_banking77() -> Splits:
    def read(name: str) -> list[Example]:
        with (DATA_DIR / "banking77" / name).open(encoding="utf-8", newline="") as fh:
            return [Example(r["text"], r["category"]) for r in csv.DictReader(fh)]

    train_all, test = read("train.csv"), read("test.csv")
    labels = sorted({e.label for e in train_all})
    # No official validation split: hold out 10% of train, stratified, seed 42 (spec section 4).
    rng = random.Random(SEED)
    by_label: dict[str, list[Example]] = {}
    for e in train_all:
        by_label.setdefault(e.label, []).append(e)
    train: list[Example] = []
    val: list[Example] = []
    for lab in labels:
        items = by_label[lab][:]
        rng.shuffle(items)
        k = max(1, round(0.1 * len(items)))
        val += items[:k]
        train += items[k:]
    return Splits(train, val, test, labels, {}, {})


def load_massive(locales: list[str]) -> Splits:
    base = DATA_DIR / "massive" / "1.0" / "data"
    train: list[Example] = []
    val: list[Example] = []
    test: list[Example] = []
    parent: dict[str, str] = {}
    for loc in locales:
        for line in (base / f"{loc}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            ex = Example(r["utt"], r["intent"], loc)
            parent[r["intent"]] = r["scenario"]
            {"train": train, "dev": val, "test": test}[r["partition"]].append(ex)
    labels = sorted({e.label for e in train})
    return Splits(train, val, test, labels, parent, {})


def holdout_classes(labels: list[str], k: int) -> list[str]:
    """Pre-registered rule: random.Random(42).sample(sorted(labels), k)."""
    return sorted(random.Random(SEED).sample(sorted(labels), k))


def stratified_subsample(rows: list[Example], n: int, seed: int = SEED) -> list[Example]:
    """About n rows, round-robin over labels so every label is covered before any repeats."""
    rng = random.Random(seed)
    by_label: dict[str, list[Example]] = {}
    for e in rows:
        by_label.setdefault(e.label, []).append(e)
    for items in by_label.values():
        rng.shuffle(items)
    out: list[Example] = []
    i = 0
    while len(out) < n and any(i < len(v) for v in by_label.values()):
        for lab in sorted(by_label):
            if i < len(by_label[lab]) and len(out) < n:
                out.append(by_label[lab][i])
        i += 1
    return out
