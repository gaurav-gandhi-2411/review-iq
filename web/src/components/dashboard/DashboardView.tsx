import { RANGES, type DashboardModel, type RangeKey } from '../../lib/dashboardModel'
import RangeSelector from './RangeSelector'
import UrgentQueue from './UrgentQueue'
import NewSinceVisit from './NewSinceVisit'
import HealthPanel from './HealthPanel'
import Concerns from './Concerns'
import UsageFooter from './UsageFooter'
import { ErrorState, LoadingState, NoReviewsState, RangeEmptyState } from './States'

export interface DashboardViewProps {
  status: 'loading' | 'error' | 'ready'
  errorMessage?: string
  onRetry?: () => void
  range: RangeKey
  onRangeChange: (range: RangeKey) => void
  /** Required when status === 'ready'. */
  model: DashboardModel | null
  usage: { used: number; quota: number } | null
  now: number
  onUpload: () => void
  onTrySample: () => void
  onOpenConcern: (topic: string) => void
}

// Presentational: all data arrives as props, so every state can be rendered from fixtures
// (see src/dev/DashboardPreview.tsx) and by tests without a backend.
export default function DashboardView(p: DashboardViewProps) {
  const spec = RANGES.find(r => r.key === p.range)!
  const m = p.status === 'ready' ? p.model : null
  const previousLabel = spec.days ? `previous ${spec.days} days` : ''

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <h1 className="font-display text-3xl leading-tight sm:text-4xl">What needs you today</h1>
        <RangeSelector value={p.range} onChange={p.onRangeChange} />
      </div>

      {/* Announces range changes and load completion to screen readers. */}
      <p className="sr-only" role="status" aria-live="polite">
        {m
          ? `Showing ${spec.label.toLowerCase()}: ${m.total} review${m.total === 1 ? '' : 's'}, ${m.urgent.length} urgent.`
          : ''}
      </p>

      <div className="mt-8 space-y-8">
        {p.status === 'loading' && <LoadingState />}
        {p.status === 'error' && (
          <ErrorState message={p.errorMessage ?? 'Something went wrong.'} onRetry={p.onRetry} />
        )}
        {p.status === 'ready' && m && m.allTotal === 0 && (
          <NoReviewsState onUpload={p.onUpload} onTrySample={p.onTrySample} />
        )}
        {p.status === 'ready' && m && m.allTotal > 0 && (
          <>
            {m.total === 0 ? (
              <RangeEmptyState rangeLabel={spec.label} olderCount={m.allTotal} />
            ) : (
              <UrgentQueue reviews={m.urgent} rangeLabel={spec.label} now={p.now} importIds={m.importIds} />
            )}
            <NewSinceVisit fresh={m.fresh} now={p.now} />
            {m.total > 0 && (
              <div className="grid gap-10 lg:grid-cols-[5fr_7fr] lg:gap-12">
                <HealthPanel health={m.health} trend={m.trend} previousLabel={previousLabel} />
                <Concerns
                  concerns={m.concerns}
                  total={m.total}
                  hasBaseline={m.trend.kind !== 'no-baseline'}
                  previousLabel={previousLabel}
                  onOpen={p.onOpenConcern}
                />
              </div>
            )}
          </>
        )}
      </div>

      {p.usage && p.status === 'ready' && <UsageFooter used={p.usage.used} quota={p.usage.quota} />}
    </div>
  )
}
