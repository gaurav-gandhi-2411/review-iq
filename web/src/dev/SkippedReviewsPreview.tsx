// DEV-ONLY: renders the real Layout + SkippedReviewsView from fixtures, no login or backend.
// Reached at /__preview/skipped?state=normal|empty|error|off|loading. Guarded in App.tsx by
// import.meta.env.DEV so it is not in the production bundle.
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import Layout from '../components/Layout'
import SkippedReviewsView, { type RowState } from '../components/SkippedReviewsView'
import { SKIPPED_FIXTURE_ROWS } from './fixtures'

export default function SkippedReviewsPreview() {
  const [params] = useSearchParams()
  const state = params.get('state') ?? 'normal'
  const [rowStates, setRowStates] = useState<Record<string, RowState>>({})
  const status = state === 'loading' || state === 'error' || state === 'off' ? state : 'ready'
  const rows = status === 'ready' && state !== 'empty' ? SKIPPED_FIXTURE_ROWS : []

  return (
    <Layout active="skipped">
      <SkippedReviewsView
        status={status}
        rows={rows}
        error={new Error('Service is busy -- try again in 30 s.')}
        onRetry={() => undefined}
        rowStates={rowStates}
        onAnalyse={id => {
          // Preview only: show the pending state, then a canned purged error.
          setRowStates(s => ({ ...s, [id]: { phase: 'pending' } }))
          setTimeout(() => {
            setRowStates(s => ({ ...s, [id]: { phase: 'error', message: "This review's data has been purged." } }))
          }, 1500)
        }}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => undefined}
      />
    </Layout>
  )
}
