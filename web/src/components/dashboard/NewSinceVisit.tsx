import { useState } from 'react'
import { type NewSince, NEW_LIST_PREVIEW, formatVisit, NEW_FALLBACK_DAYS } from '../../lib/dashboardModel'
import ReviewRow from './ReviewRow'

interface Props {
  fresh: NewSince
  now: number
}

export default function NewSinceVisit({ fresh, now }: Props) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? fresh.reviews : fresh.reviews.slice(0, NEW_LIST_PREVIEW)
  const hidden = fresh.reviews.length - shown.length
  const n = fresh.reviews.length

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
        {fresh.mode === 'fallback' &&
          ' This browser has no earlier visit on record, so this shows the past week instead.'}
        {' '}This list does not change with the time range.
      </p>

      {n > 0 && (
        <>
          <ul className="mt-4">
            {shown.map(r => (
              <ReviewRow key={r.id} review={r} now={now} tone="light" />
            ))}
          </ul>
          {n > NEW_LIST_PREVIEW && (
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
