# eval/data — Real Review Data Sourcing

Scripts that download and prepare real review data for Hinglish/Hindi eval fixture creation.

## Setup

### 1. Kaggle API token (one-time, ~2 minutes)

1. Go to [kaggle.com/settings](https://www.kaggle.com/settings) → API → Create New Token
2. Download `kaggle.json` and place it at:
   - Windows: `C:\Users\<your-username>\.kaggle\kaggle.json`
   - Mac/Linux: `~/.kaggle/kaggle.json`
3. Verify: `kaggle datasets list` should return results without errors

### 2. Python deps

```bash
uv add --dev kaggle lingua-language-detector datasets pandas
```

Or if already in `pyproject.toml` dev deps:

```bash
uv sync --dev
```

---

## Scripts

### `sample_flipkart.py` — Flipkart Kaggle data

Downloads three Flipkart review datasets from Kaggle, runs language classification
(en / hi-en / hi), and writes all candidates to `flipkart_candidates.jsonl`.

```bash
uv run python eval/data/sample_flipkart.py
```

**Datasets used:**
| Kaggle ref | Rows | License |
|---|---|---|
| `niraliivaghani/flipkart-product-customer-reviews-dataset` | ~180k | DbCL-1.0 |
| `kabirnagpal/flipkart-customer-review-and-rating` | ~10k | **CC0-1.0** — resolved 2026-07-31 (Wave 1 Section H). Verified by reading the dataset page's own JSON-LD `license` block directly: `{"name":"CC0: Public Domain","url":"https://creativecommons.org/publicdomain/zero/1.0/"}`. Wired into `benchmark/vernacular_v2/ingest_and_dedupe.py`'s `SOURCES` (id `kabirnagpal_10k`). |
| `naushads/flipkart-reviews` | ~9k | **CC0-1.0** — resolved 2026-07-31, same verification method (JSON-LD license block). License is clear; **not yet wired into ingestion** — the dataset page documents its scraping methodology but not its CSV column schema, and no raw file is present in this (gitignored) worktree to inspect a header row directly. Guessing a schema was rejected per this repo's evidence-over-recall rule. See `benchmark/vernacular_v2/ingest_and_dedupe.py`'s `NAUSHADS_LICENSE_CLEARED_SCHEMA_PENDING` and ADR 0004 for the full trail. |

**Expected output:** `eval/data/flipkart_candidates.jsonl` with language labels.

**Note on Hinglish yield:** The public Kaggle Flipkart datasets are primarily English-curated.
Genuine Hinglish candidates (Latin-script code-mixed text) are typically 50–200 out of 200k rows.
This is enough to label 15 fixtures; the label-helper presents the best candidates for selection.
The plan initially estimated 600+ but this was based on raw Flipkart API data, not the cleaned
Kaggle datasets that are publicly available.

---

### `sample_amazon.py` — Amazon Reviews 2023 (HuggingFace)

Streams a sample from the McAuley Lab Amazon Reviews 2023 dataset via HuggingFace.
Used for English breadth — additional edge cases for the English eval set.

```bash
uv run python eval/data/sample_amazon.py --category All_Beauty --n 3000
```

Available categories: `All_Beauty`, `Electronics`, `Home_and_Kitchen`, `Books`, etc.
See [full list](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023).

**Expected output:** `eval/data/amazon_candidates.jsonl`

---

## Output format

Both scripts produce JSONL with one record per line:

```json
{
  "source": "flipkart/niraliivaghani/flipkart-product-customer-reviews-dataset",
  "text": "Paisa vasool! Bahut accha product hai, ekdum sahi.",
  "product": "Xiaomi Redmi Note 9",
  "rating": 5,
  "language": "hi-en",
  "char_len": 50
}
```

`language` values:
- `en` — English
- `hi-en` — Hinglish (Roman-script Hindi/English code-mix)
- `hi` — Hindi (Devanagari script)
- `other` — unclassified

---

## Next step: label-helper

After running `sample_flipkart.py`, start the labeling session:

```bash
uv run python eval/label-helper.py
```

The tool ranks the top 50 Hinglish candidates by length and diversity, then guides you
through labeling each one. State is saved after each fixture — you can stop and resume.
Target: **15 labeled Hinglish fixtures** committed to `eval/fixtures/hi-en/`.
