"""Wave 1 Section H: per-record license provenance on benchmark/vernacular_v2/ingest_and_dedupe.py.

Uses tiny synthetic CSVs (not the real ~245K-row Kaggle corpus, which lives in the
gitignored data/raw/ and is never committed) to verify the loader logic in isolation.
"""

from __future__ import annotations

import csv
from pathlib import Path

from benchmark.vernacular_v2.ingest_and_dedupe import SOURCES, load_source


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def test_every_registered_source_has_license_and_kaggle_ref() -> None:
    """Every source in the executable SOURCES list carries license provenance —
    the field ingest writes into every output record."""
    for src in SOURCES:
        assert src["license"], f"{src['id']} missing license"
        assert src["kaggle_ref"], f"{src['id']} missing kaggle_ref"
        assert "/" in src["kaggle_ref"], f"{src['id']} kaggle_ref should be owner/dataset"


def test_kabirnagpal_source_is_registered_cc0() -> None:
    by_id = {src["id"]: src for src in SOURCES}
    assert "kabirnagpal_10k" in by_id
    src = by_id["kabirnagpal_10k"]
    assert src["license"] == "CC0-1.0"
    assert src["kaggle_ref"] == "kabirnagpal/flipkart-customer-review-and-rating"


def test_load_source_standard_schema_writes_license_per_record(tmp_path: Path) -> None:
    """The 3 original sources (summary-column schema) — license/kaggle_ref land on
    every output record, not just as a lookup in SOURCES."""
    csv_path = tmp_path / "standard.csv"
    _write_csv(
        csv_path,
        ["ProductName", "Rate", "Review", "Summary"],
        [["Widget", "5", "Nice", "Really great product, works as expected."]],
    )
    src = {
        "id": "test_standard",
        "path": csv_path,
        "cols": {
            "product": "ProductName",
            "rate": "Rate",
            "review": "Review",
            "summary": "Summary",
        },
        "license": "ODbL-1.0",
        "kaggle_ref": "someone/some-dataset",
    }
    rows = load_source(src)
    assert len(rows) == 1
    assert rows[0]["license"] == "ODbL-1.0"
    assert rows[0]["kaggle_ref"] == "someone/some-dataset"
    assert rows[0]["text"] == "Really great product, works as expected."
    assert rows[0]["product_name"] == "Widget"


def test_load_source_kabirnagpal_schema_text_col_override(tmp_path: Path) -> None:
    """kabirnagpal's 2-column schema (Review IS the free-text body, no Summary column,
    single static product) — text_col + static_product_name overrides."""
    csv_path = tmp_path / "kabirnagpal.csv"
    _write_csv(
        csv_path,
        ["Review", "Rating"],
        [["Sound quality is excellent for the price, battery lasts 2 days.", "5"]],
    )
    src = {
        "id": "test_kabirnagpal",
        "path": csv_path,
        "cols": {"review": "Review", "rate": "Rating"},
        "text_col": "review",
        "static_product_name": "boAt Rockerz 400 (Bluetooth headset)",
        "license": "CC0-1.0",
        "kaggle_ref": "kabirnagpal/flipkart-customer-review-and-rating",
    }
    rows = load_source(src)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["text"] == "Sound quality is excellent for the price, battery lasts 2 days."
    assert rec["product_name"] == "boAt Rockerz 400 (Bluetooth headset)"
    assert rec["license"] == "CC0-1.0"
    assert rec["kaggle_ref"] == "kabirnagpal/flipkart-customer-review-and-rating"


def test_load_source_skips_short_or_empty_text(tmp_path: Path) -> None:
    csv_path = tmp_path / "short.csv"
    _write_csv(
        csv_path,
        ["Review", "Rating"],
        [["ok", "5"], ["", "3"], ["Genuinely long enough review body.", "4"]],
    )
    src = {
        "id": "test_short",
        "path": csv_path,
        "cols": {"review": "Review", "rate": "Rating"},
        "text_col": "review",
        "license": "CC0-1.0",
        "kaggle_ref": "x/y",
    }
    rows = load_source(src)
    # "ok" (2 chars) and "" are both dropped by the len(body) < 3 guard.
    assert len(rows) == 1
    assert rows[0]["text"] == "Genuinely long enough review body."
