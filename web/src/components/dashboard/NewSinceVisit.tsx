import { useState } from 'react'
import { Link } from 'react-router'
import { type NewItem, type NewSince, NEW_LIST_PREVIEW, formatArrival, formatVisit, NEW_FALLBACK_DAYS } from '../../lib/dashboardModel'
import ReviewRow from './ReviewRow'

interface Props {
  fresh: NewSince
  now: number
}

export default function NewSinceVisit({ fresh, now }: Props) {
  const [expanded, setExpanded] = useState(false)
  // The list is by item: a bulk import is ONE item however many reviews it holds; the counts
  // in the sentence below stay by review.
  const shown = expanded ? fresh.items : fresh.items.slice(0, NEW_LIST_PREVIEW)
  const hidden = fresh.items.length - shown.length
  const n = fresh.reviews.length
  const imported = fresh.items.reduce((sum, i) => sum + (i.kind === 'import' ? i.count : 0), 0)

  const heading =
    fresh.mode === 'since-visit'
      ? `Since you were last here, ${formatVisit(fresh.since)}`
      : `Arrived in the last ${NEW_FALLBACK_DAYS} days`

  return (
    <section aria-labelledby="new-heading" className="rounded-lg bg-cream-deep px-5 py-5 sm:px-8">
      <h2 id="new-heading" className="font-display text-xl leading-snug sm:text-2xl">
        {heading}
      </h2>
      <p className="mt-1 text-sm text-ink-soft">
        {n === 0
          ? 'No new reviews have arrived.'
          : `${n} new review${n === 1 ? '' : 's'}: ${fresh.urgent} urgent, ${fresh.negative} negative.`}
        {imported > 0 && ` ${imported} of them arrived in a bulk import.`}
        {fresh.mode === 'fallback' &&
          ' This browser has no earlier visit on record, so this shows the past week instead.'}
        {' '}This list does not change with the time range.
      </p>

      {n > 0 && (
        <>
          <ul className="mt-4">
            {shown.map(item =>
              item.kind === 'import' ? (
                <ImportItem key={`import-${item.key}`} item={item} now={now} />
              ) : (
                <ReviewRow key={item.review.id} review={item.review} now={now} tone="light" />
              ),
            )}
          </ul>
          {fresh.items.length > NEW_LIST_PREVIEW && (
            <button
              type="button"
              onClick={() => setExpanded(e => !e)}
              aria-expanded={expanded}
              className="mt-3 min-h-[44px] rounded-md border border-ink px-4 text-sm font-medium text-ink hover:bg-cream"
            >
              {expanded ? 'Show fewer' : `Show ${hidden} more new`}
            </button>
          )}
        </>
      )}
    </section>
  )
}

// One bulk import (20+ reviews analysed within 10 minutes, no customer dates) collapsed to one
// line. Urgent reviews inside it are NOT hidden: they stay in the urgent queue, labelled.
function ImportItem({ item, now }: { item: Extract<NewItem, { kind: 'import' }>; now: number }) {
  return (
    <li className="border-b border-rule py-4 first:pt-0 last:border-b-0 last:pb-0">
      <p className="text-[0.9375rem] font-medium text-ink">
        {item.count} reviews imported <time dateTime={new Date(item.time).toISOString()}>{formatArrival(item.time, now)}</time>
      </p>
      <p className="mt-1 max-w-prose text-sm text-ink-soft">
        {item.urgent} urgent, {item.negative} negative.
        {item.urgent > 0 && ' Urgent reviews from an import stay in the urgent queue above, marked From import.'}
      </p>
      <Link
        to="/reviews"
        className="mt-2 inline-flex min-h-[44px] items-center text-sm font-medium text-ink underline underline-offset-4 decoration-1 hover:text-ink-soft"
      >
        Browse all reviews
      </Link>
    </li>
  )
}
