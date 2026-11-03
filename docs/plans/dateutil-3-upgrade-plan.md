# python-dateutil 2.9.0.post0 → 3.0.0 upgrade plan (#654)

Status: PARKED (major version). Written S35, 2026-11-03.

## What uses dateutil

- **App code (deployed):** exactly one module, `app/core/csv_ingest.py`. It uses only `dateutil.parser.parse` (with `dayfirst`) and
  `ParserError`, to read `review_date` from uploaded CSVs. Nothing in `app/` imports `dateutil.tz` or `dateutil.zoneinfo`
  (VERIFIED, `grep -rn "dateutil|tz.gettz|zoneinfo" app`).
- **Transitive:** `pandas` and `kaggle` depend on it (`uv.lock`). Neither is imported by `app/` (VERIFIED: no `pandas` import in `app/`).
  They run in eval and data scripts only.

## What 3.0.0 changes (release notes on #654)

1. `tz.tzfile` rewritten from a `zoneinfo` backport; the UTC offset of nonexistent (gap) times follows PEP 495.
2. The bundled IANA database is gone; zone data comes from the system or the new `tzdata` dependency.
3. Fixed-offset `tzfile` zones now report offsets for `time` objects (they become aware).
4. `dateutil.zoneinfo` and `TZPATHS` are deprecated or removed; relative `gettz` keys outside `TZPATH` raise a warning-as-error.
5. The parser is not listed as changed.

None of 1-4 touches `parser.parse` as the app calls it. Risk to the deployed path: LOW (BELIEVED, from the notes plus the test below).

## Evidence so far

- `tests/unit/test_csv_ingest.py` and `tests/unit/test_v2_ingest.py` under dateutil 3.0.0 in a throwaway uv environment
  (`uv run --with python-dateutil==3.0.0 python -m pytest ...`): 44 passed; the same 44 pass on 2.9.0.post0 (VERIFIED, S35).
- Not yet covered: a parser run over real CSV date shapes beyond the unit fixtures, and the pandas paths in eval scripts.

## Steps to merge

1. Update #654 from main; CI must pass (`lint-and-test` runs the full unit suite against the new `uv.lock`).
2. Differential check, no network: parse every distinct `review_date` string shape in the committed CSV fixtures and synthetic testbed
   with 2.9 and with 3.0 in two throwaway environments; require identical `datetime` results. Commit the script and its output under
   `reports/deps/`.
3. Confirm `tzdata` lands in `uv.lock` and in the image (`uv sync --frozen --no-dev`), since 3.0 depends on it.
4. Merge (routine deploy). After the deploy: `/health` 200, then one CSV-ingest smoke on a test org; rollback is
   `gcloud run services update-traffic review-iq --to-revisions=<previous>=100`.

## Rollback

Revert the squash commit (restores 2.9.0.post0 in `uv.lock`); the next deploy restores the old image, or route traffic back to the
previous revision immediately.
