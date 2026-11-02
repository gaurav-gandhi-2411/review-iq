import { ArrowDownRight, ArrowUpRight, Minus, Plus } from 'lucide-react'
import {
  MIN_REVIEWS_FOR_TREND,
  MIN_TOPIC_MENTIONS,
  type Concern,
  type ConcernTrend,
} from '../../lib/dashboardModel'

const TREND: Partial<Record<ConcernTrend, { label: string; chip: string; Icon: typeof Plus }>> = {
  new: { label: 'New', chip: 'bg-ember', Icon: Plus },
  rising: { label: 'Rising', chip: 'bg-ember-tint', Icon: ArrowUpRight },
  easing: { label: 'Easing', chip: 'bg-cream-deep', Icon: ArrowDownRight },
  steady: { label: 'Steady', chip: 'bg-cream-deep', Icon: Minus },
}

interface Props {
  concerns: Concern[]
  total: number
  hasBaseline: boolean
  previousLabel: string
  onOpen: (topic: string) => void
}

export default function Concerns({ concerns, total, hasBaseline, previousLabel, onOpen }: Props) {
  return (
    <section aria-labelledby="concerns-heading">
      <h2 id="concerns-heading" className="font-display text-xl sm:text-2xl">
        What customers keep raising
      </h2>
      {concerns.length === 0 ? (
        <p className="mt-3 text-[0.9375rem] text-ink-soft">No topics were mentioned in this period.</p>
      ) : (
        <>
          <ul className="mt-3 border-t border-rule">
            {concerns.map(c => {
              const t = TREND[c.trend]
              const share = total > 0 ? Math.round((c.count / total) * 100) : 0
              return (
                <li key={c.topic} className="border-b border-rule">
                  <button
                    type="button"
                    onClick={() => onOpen(c.topic)}
                    className="flex min-h-[44px] w-full items-center gap-3 py-3 text-left hover:bg-cream-deep"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium">{c.label}</span>
                      <span className="block text-sm text-ink-soft">
                        {c.count} mention{c.count === 1 ? '' : 's'}, {share}% of reviews
                        {c.previousCount !== null && ` (before: ${c.previousCount})`}
                      </span>
                    </span>
                    {t && (
                      <span className={`inline-flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs font-medium text-ink ${t.chip}`}>
                        <t.Icon size={13} aria-hidden="true" />
                        {t.label}
                      </span>
                    )}
                    <span className="sr-only">. See these reviews.</span>
                  </button>
                </li>
              )
            })}
          </ul>
          <p className="mt-3 text-sm text-ink-soft">
            {hasBaseline
              ? `Compared with the ${previousLabel}. A concern is only labelled with ${MIN_TOPIC_MENTIONS}+ mentions and ${MIN_REVIEWS_FOR_TREND}+ reviews in both periods.`
              : 'All time has no earlier period, so trends are not shown.'}
          </p>
        </>
      )}
    </section>
  )
}
