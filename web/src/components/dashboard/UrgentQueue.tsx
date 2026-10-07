import { useState } from 'react'
import { CircleCheck } from 'lucide-react'
import type { Review } from '../../lib/api'
import { URGENT_QUEUE_PREVIEW } from '../../lib/dashboardModel'
import ReviewRow from './ReviewRow'

interface Props {
  reviews: Review[] // already urgent-only, newest first
  rangeLabel: string
  now: number
  /** Ids of reviews that came from a detected bulk import; they stay in the queue, labelled. */
  importIds?: ReadonlySet<number>
}

// The one dark slab on the page: the reviews that need a person, readable in place.
export default function UrgentQueue({ reviews, rangeLabel, now, importIds }: Props) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? reviews : reviews.slice(0, URGENT_QUEUE_PREVIEW)
  const hidden = reviews.length - shown.length

  return (
    <section aria-labelledby="urgent-heading" className="on-ink rounded-lg bg-ink px-5 py-6 text-cream sm:px-8 sm:py-8">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h2 id="urgent-heading" className="font-display text-3xl leading-tight sm:text-4xl">
          {reviews.length > 0 ? (
            <>
              <span className="text-saffron">{reviews.length}</span> urgent review{reviews.length === 1 ? '' : 's'}
            </>
          ) : (
            'Urgent reviews'
          )}
        </h2>
        <p className="text-sm text-cream-soft">Newest first. {rangeLabel}.</p>
      </div>

      {reviews.length === 0 ? (
        <p className="mt-5 flex items-start gap-2 text-[0.9375rem] text-cream">
          <CircleCheck size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-saffron" />
          <span>Nothing urgent. No review in this period was marked high urgency.</span>
        </p>
      ) : (
        <>
          <ul className="mt-6">
            {shown.map(r => (
              <ReviewRow key={r.id} review={r} now={now} tone="dark" fromImport={importIds?.has(r.id) ?? false} />
            ))}
          </ul>
          {reviews.length > URGENT_QUEUE_PREVIEW && (
            <button
              type="button"
              onClick={() => setExpanded(e => !e)}
              aria-expanded={expanded}
              className="mt-4 min-h-[44px] rounded-md border border-cream-soft px-4 text-sm font-medium text-cream hover:bg-ink-rule"
            >
              {expanded ? 'Show fewer' : `Show ${hidden} more urgent`}
            </button>
          )}
        </>
      )}
    </section>
  )
}
