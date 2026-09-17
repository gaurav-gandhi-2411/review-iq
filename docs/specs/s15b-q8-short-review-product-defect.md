# Session 15b Q8 — the short-review "wrong-committed" defect, root-caused

Source of truth: `eval/results/known_gaps_n106.json` (Session 14 P2, `eval/analyze_known_gaps.py`,
zero quota) cross-referenced against the raw fixtures in
`eval/fixtures/_held_out_hindi_hinglish/hien-*.json` and `eval/results/held_out_scoring_v2.json`.
No new LLM calls; no prompt file was read or edited while producing this analysis — reading
held-out ground truth for measurement is the corpus's intended use (identical to Session 14 P2
itself), never for prompt tuning.

**STOP — analysis and proposal only, nothing implemented.** Any fix here changes a public API
response contract (a field going from always-a-string to nullable). Per the standing autonomy
contract this needs GG's decision before code changes, independent of whether a DB migration is
also involved.

## Q8a — which fields are actually forcing a guess

The headline number (97/324 short-review field-checks confident-wrong) is real, but it collapses
three different problems into one figure. Breaking the 97 down by field
(`known_gaps_n106.json`'s `short_reviews.per_field`):

| Field | wrong_committed | correct | correct_abstention | Ever abstains in this sample? |
|---|---|---|---|---|
| `product` | 25 | 2 | 0 | **No** |
| `topics` | 22 | 2 | 2 | Rarely |
| `language` | 13 | 14 | 0 | **No** |
| `pros` | 14 | 2 | 8 | Yes |
| `stars_inferred` | 8 | 19 | 0 (already `int \| None`) | **No, in this sample** |
| `cons` | 6 | 2 | 19 | Yes |
| `sentiment` | 4 | 23 | 0 (n/a — always inferable) | n/a |
| `buy_again` | 4 | 8 | 14 | Yes |
| `urgency` | 1 | 26 | 0 (n/a — always inferable) | n/a |

Three fields never abstain in this sample: `product`, `language`, `stars_inferred`. Only one of
them is actually a schema-nullability defect.

## Q8b — should they be nullable? (per field, evidence-based, not assumed)

**`product` — YES, this is the schema defect.** `app/core/schemas.py`: `ReviewExtraction.product:
str` (required) and `ReviewExtractionLLMOutput.product: str = "unknown product"` (placeholder
fallback, not a real null). Pulling all 25 wrong-committed short-review `product` cases directly
(not just the aggregate count) shows the dominant failure mode is **not** a specific wrong guess —
it's **both the model and the held-out corpus's own ground-truth labelers independently reaching
for different placeholder strings** to express "no product is named in this text," because neither
side has a real null to reach for:

| id | ground truth | prediction | text |
|---|---|---|---|
| hien-0012 | `"product"` | `"general product"` | "Bakwas .. go for regular" |
| hien-0013 | `"product"` | `"general product"` | "Bakwas hai...READ MORE" |
| hien-0015 | `"unknown"` | `"general product"` | "Bass thoda kam hai jitna socha tha." |
| hien-0034 | `"product"` | `"general product"` | "Excellent product, Paisa wasul bhai" |
| hien-0041 | `"product"` | `"unknown product"` | "Good product..mast chis" |
| hien-0091 | `"products"` | `"unknown product"` | "Useless products hai ye" |
| hien-0099 | `"unknown"` | `"unknown product"` | "Very niceBetter back up mast hai" |
| hien-0100 | `"unknown"` | `"unknown product"` | "Voice quality aur battery backup 1 number hai....." |
| *(+7 more of the same shape: 0019, 0083, 0092, 0096, 0105, 0106, 0057)* | | | |

**15 of the 25 (60%)** are this shape: both sides correctly recognize the text names no specific
product, expressed with different non-canonical strings (`"product"` / `"unknown"` / `"products"`
vs. `"general product"` / `"unknown product"`), scored wrong purely by exact-string mismatch. This
is the schema forcing a guess the text does not support, exactly as framed in the brief — except
it's forcing *two* incompatible guesses (one from the model's fallback default, one from the
labeling panel's own ad hoc convention) that were never going to match each other.

The remaining 10 are two smaller, genuinely different problems, not nullability:
- **Normalization, not abstention** (~3 cases): hien-0010 (`"OnePlus Bluetooth"` vs `"Bluetooth
  oneplus"` — same product, word order/case), hien-0016 (`"BassDesign"` vs `"BassDesignSound"` —
  substring), hien-0090 (`"headset"` vs `"Ultimate headset Paisa"` — correct core noun, extra
  padding). Exact-match scoring penalizes correct extractions with cosmetic differences.
- **Genuine misses/hallucinations** (~5-7 cases): hien-0056 (`"unknown"` vs `"Mast"` — the model
  extracted a Hindi adjective meaning "great" as if it were a product name — a real hallucination,
  not an abstention issue), hien-0028/0043 (`"audio device"` vs `"general product"` — the panel
  inferred a device category from a contextual clue the model missed).

**`language` — NO, not a nullability question.** `language: str = "en"` (no `None` option in the
schema), but every review genuinely *is* written in some language — there is no text for which
"no language" is a correct answer, so a nullable field wouldn't fix anything. All 13
wrong-committed cases show the same pattern: `gt="hi-en"`, `pred="en"` — a systematic
under-detection of Hinglish on *short* mixed-language text specifically. This is a real, separate
model-accuracy gap (language detection on short reviews), not this Q8's schema question. Flagging
for its own follow-up, not proposing a fix here.

**`stars_inferred` — NO schema defect; it's already `int | None`.** Zero of the 8 wrong-committed
cases in this sample used the existing `None` option — every one is an off-by-one-or-two ordinal
miscalibration on ambiguous short text (`gt=3, pred=2`; `gt=5, pred=4`; `gt=5, pred=3`, etc.). The
schema already supports abstention here; the model just isn't using it on these particular short,
ambiguous cases, and isn't hitting the actual right number either. Also a real, separate
model-accuracy gap, not a schema issue.

**`topics` — NO schema defect; already `list[str] = []`.** High wrong_committed count reflects
list-based fields being harder to score by exact match (extra/missing/reworded topic strings),
not a missing null option — an empty list is already a legitimate abstention and the field uses it
occasionally in this sample (2/27).

## Q8c — proposed fix and blast radius (not implemented — STOP for GG)

**Proposed fix, `product` only:**

1. `app/core/schemas.py`: `ReviewExtraction.product: str` → `product: str | None = None`;
   `ReviewExtractionLLMOutput.product: str = "unknown product"` → `product: str | None = None`
   (drop the placeholder-string fallback entirely — that fallback string is the mechanism
   producing half of this defect).
2. Prompt/few-shot update (`app/core/prompts/`): instruct the model to return `null` for `product`
   when no product/category is named or inferable, instead of a placeholder string. This is a
   prompt change and would need its own held-out re-measurement before shipping (prompt-change
   checklist: bump version, document in `PROMPTS.md`, eval before merge — same as any other
   prompt edit).
3. Ground-truth normalization in `eval/fixtures/_held_out_hindi_hinglish/`: the panel's own ad hoc
   placeholder strings (`"product"`, `"unknown"`, `"products"`) should become `null` for
   consistency with the new schema. This is a data-quality correction, not developing against the
   held-out set — no prompt file is touched by this step — but it changes graded fixtures and
   should be reviewed as its own explicit diff, not silently folded into a scoring change (rule
   65c: never let a change that would move your own score go quiet).

**Blast radius (verified, not assumed):**

- **Database:** `public.extractions.product` is `text` with **no `NOT NULL` constraint**
  (`supabase/migrations/20260511000004_extractions_flat_columns.sql`,
  `20260710000001_review_date.sql`) — already nullable at the DB layer. This fix needs **no DB
  migration**.
- **Application code:** exactly 3 non-test files reference `.product`
  (`app/api/extract.py:76`, `app/core/storage.py:171`, `app/core/storage_pg.py:207-211`). None
  perform a null-unsafe string operation; `storage_pg.py`'s per-product grouping already has an
  `if product_override else extraction.product` fallback pattern that degrades gracefully to
  `None`. Downstream consumers of a `None` product (batch-defect/topic-spike grouping) would need
  an explicit "uncategorized" bucket — not yet checked whether one already exists; flag for the
  implementer.
- **API contract:** this is the real blast radius — `product` moves from "always present, always a
  string" to "may be `null`" in every `/v2/extract`, `/v2/ingest/csv`, and `/demo/extract` response.
  Any existing integration doing `response.product.upper()` or similar without a null check would
  break. This is why the brief marks schema/contract changes as a hard STOP rather than something
  to decide unilaterally.
- **Tests:** not exhaustively enumerated. `grep` shows ~10+ unit test files construct fixtures with
  `product="..."` — these remain valid as-is (a string is still a legal value), so no expected
  breakage; a smaller, unknown number may specifically assert `product is not None` or do
  non-null-safe operations on it and would need updating. Left as an estimate, not a verified count
  — the implementer should re-run this grep before starting.

**Recommendation:** implement the `product` fix (schema + prompt + ground-truth normalization,
re-measured against held-out before merge). Do **not** touch `language` or `stars_inferred` under
this Q8 — both are real but separate model-accuracy gaps, not schema defects, and conflating them
with this fix would violate PR composition discipline (rule 39b: one coherent change per PR).
