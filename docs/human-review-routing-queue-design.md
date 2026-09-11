# Human-review routing queue — design (not built)

Session 7 P2d. This is a design document only. Nothing in this file is wired to product
code; it exists so the shape of the feature is decided before anyone builds it.

## Why this is a feature, not a gap

Session 7 P2's abstention analysis (`docs/specs/wave1-coverage-abstention-analysis.md`)
found that abstentions and confident-wrong answers are not the same failure mode: an
abstention on `buy_again`/`sentiment` is a field the model explicitly declined to guess on,
while a wrong-but-committed answer (the Hindi-sentiment risk in that doc) is a field the
model got wrong with no signal that it happened. For a service reselling extraction results
to an agency that reviews reviews for brand clients, the first case is routable to a human
and the second is not — an abstention is the model correctly signaling "I don't know,"
which is exactly the behavior a human-in-the-loop product wants, not a defect to eliminate.

## Scope

Only fields the extraction schema marks as hedge-capable route: today, `buy_again` (hedge
value `null`) and `sentiment` (hedge value `"mixed"`) per the existing coverage methodology.
Any field extension here follows the same convention `eval/README.md` already uses, not a
new one.

## Queue shape

1. **Trigger**: at extraction time, any hedge-tracked field whose value equals its hedge
   value (`null` for `buy_again`, `"mixed"` for `sentiment`) is tagged
   `needs_human_review: true` on that field, alongside the extraction's normal output — the
   customer-facing extraction record is not blocked or delayed by this tag.
2. **Queue table**: a new `review_queue` row per (extraction_id, field), written at the same
   time as the extraction (append-only, same convention as `extraction_costs`/`alert_log`/
   `quota_requests` — no UPDATE grant, corrections are a new row). Columns: `extraction_id`,
   `field`, `review_status` (`pending`/`resolved`/`dismissed`), `reviewer_value`,
   `reviewed_by`, `reviewed_at`. RLS scoped to `org_id` the same as every other tenant table.
3. **Surface**: a filtered view in the existing web dashboard (`web/src/pages/Dashboard.tsx`
   already has the review-list infrastructure) showing only rows with
   `review_status = 'pending'`, one row per hedge, with the original review text alongside
   the model's abstention so a reviewer can commit a value in seconds, not re-read the whole
   pipeline's output.
4. **Committed fields bypass the queue entirely** — this is the point. Only the subset that
   actually hedged reaches a human; the majority of any extraction's fields never touch this
   table.
5. **Resolution write-back**: a resolved queue row updates nothing in the original
   `extractions` row (immutable extraction history, same principle as `extraction_costs`
   append-only) — it is a separate, queryable correction layer a downstream consumer (the
   agency's own dashboard, an export) can choose to apply or not.

## What this is NOT

- Not a confidence-score UI. Confidence scoring is a different, harder problem (calibrated
  probability estimates) this design deliberately doesn't attempt — it routes on the
  model's own binary hedge signal, which already exists and needs no new model behavior.
- Not a fix for wrong-but-committed answers (the Hindi-sentiment risk). That failure mode
  produces no signal to route on by construction — a human reviewing everything would catch
  it, but that defeats the point of automation. Out of scope here; tracked separately as the
  named risk in the coverage-abstention doc.
- Not built in this PR. No `review_queue` migration, no API endpoint, no dashboard change.

## Rollout gate

Before building: confirm with GG whether this ships as a paid tier feature (a differentiator
for agency customers who want oversight) or a free-tier default (a trust-building floor for
every customer) — that's a pricing/positioning decision, not an engineering one, and belongs
in `spec.md`'s four-line PM header before a build ticket exists.
