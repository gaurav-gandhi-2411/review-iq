import { Link } from 'react-router'
import { Loader2 } from 'lucide-react'
import ErrorBox from './ErrorBox'
import type { ClassifiedReview, OpenedExtraction } from '../lib/api'
import { encoderChips, formatCreated } from '../lib/skippedReviews'

export type RowState =
  | { phase: 'idle' }
  | { phase: 'pending' }
  | { phase: 'error'; message: string }
  | { phase: 'done'; result: OpenedExtraction }

export interface SkippedReviewsViewProps {
  status: 'loading' | 'ready' | 'error' | 'off'
  rows: ClassifiedReview[]
  error?: Error | null
  onRetry?: () => void
  rowStates: Record<string, RowState>
  onAnalyse: (reviewId: string) => void
  hasMore: boolean
  loadingMore: boolean
  loadMoreError?: string | null
  onLoadMore: () => void
}

export default function SkippedReviewsView(props: SkippedReviewsViewProps) {
  const { status, rows, error, onRetry, rowStates, onAnalyse, hasMore, loadingMore, loadMoreError, onLoadMore } = props
  return (
    <div className="max-w-3xl">
      <h1 className="font-display text-2xl">Not yet analysed</h1>
      <p className="mt-1 text-[0.9375rem] text-ink-soft">
        These reviews were triaged by the fast classifier and not sent for full analysis. Open one to analyse it;
        this counts against your monthly quota.
      </p>

      <div className="mt-6">
        {status === 'loading' && (
          <div role="status" aria-busy="true" className="animate-pulse space-y-3">
            <span className="sr-only">Loading reviews</span>
            {[1, 2, 3].map(i => <div key={i} className="h-24 rounded-lg bg-cream-deep" />)}
          </div>
        )}

        {status === 'error' && <ErrorBox error={error ?? new Error('Could not load reviews.')} onRetry={onRetry} />}

        {status === 'off' && (
          <div role="status" className="rounded-lg border border-rule px-5 py-5">
            <h2 className="font-display text-lg">This view is not enabled</h2>
            <p className="mt-1 text-sm text-ink-soft">
              Nothing is wrong. Reviews that need no full analysis are not being set aside for your account yet.
            </p>
          </div>
        )}

        {status === 'ready' && rows.length === 0 && (
          <div className="py-10">
            <h2 className="font-display text-xl">Nothing waiting</h2>
            <p className="mt-1 text-[0.9375rem] text-ink-soft">Every review has been analysed.</p>
          </div>
        )}

        {status === 'ready' && rows.length > 0 && (
          <>
            <ul className="divide-y divide-rule border-y border-rule">
              {rows.map(row => (
                <Row key={row.review_id} row={row} state={rowStates[row.review_id] ?? { phase: 'idle' }} onAnalyse={onAnalyse} />
              ))}
            </ul>
            {loadMoreError && <p role="alert" className="mt-3 text-sm">{loadMoreError}</p>}
            {hasMore && (
              <button
                type="button"
                onClick={onLoadMore}
                disabled={loadingMore}
                className="mt-4 inline-flex min-h-[44px] items-center gap-2 rounded-md border border-ink px-5 text-sm font-medium text-ink hover:bg-cream-deep disabled:opacity-50"
              >
                {loadingMore && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Row({ row, state, onAnalyse }: { row: ClassifiedReview; state: RowState; onAnalyse: (id: string) => void }) {
  const product = row.product?.trim() || 'Unnamed product'
  const created = formatCreated(row.created_at)
  const chips = encoderChips(row.encoder)
  const pending = state.phase === 'pending'
  return (
    <li className="py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-medium">{product}</span>
        {created && <time dateTime={row.created_at ?? undefined} className="text-ink-soft">{created}</time>}
      </div>
      {chips.length > 0 && (
        <ul aria-label="Classifier labels" className="mt-2 flex flex-wrap gap-1.5">
          {chips.map(c => <li key={c} className="rounded bg-cream-deep px-1.5 py-0.5 text-xs font-medium">{c}</li>)}
        </ul>
      )}
      <p className="mt-2 break-words text-[0.9375rem] text-ink-soft">{row.text_preview}</p>

      {state.phase === 'done' ? (
        <div className="mt-3 rounded-md border border-rule bg-cream-deep px-4 py-3 text-sm">
          <p className="font-medium">
            Analysed: {state.result.sentiment ?? 'sentiment unknown'}, {state.result.urgency} urgency
          </p>
          {state.result.pros.length > 0 && <p className="mt-1">Pros: {state.result.pros.join('; ')}</p>}
          {state.result.cons.length > 0 && <p className="mt-1">Cons: {state.result.cons.join('; ')}</p>}
          {state.result.topics.length > 0 && <p className="mt-1">Topics: {state.result.topics.join(', ')}</p>}
          <Link to="/reviews" className="mt-2 inline-block underline underline-offset-2">Go to your reviews</Link>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => onAnalyse(row.review_id)}
            disabled={pending}
            aria-label={`Analyse review of ${product}`}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-saffron px-5 text-sm font-medium text-ink hover:bg-ember disabled:opacity-60"
          >
            {pending && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
            {pending ? 'Analysing…' : 'Analyse'}
          </button>
          {state.phase === 'error' && <p role="alert" className="text-sm">{state.message}</p>}
        </div>
      )}
    </li>
  )
}
