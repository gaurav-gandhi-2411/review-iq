import { useCallback, useEffect, useState } from 'react'
import Layout from '../components/Layout'
import SkippedReviewsView, { type RowState } from '../components/SkippedReviewsView'
import {
  ClassifiedReviewsDisabledError,
  listClassifiedReviews,
  openClassifiedReview,
  type ClassifiedReview,
} from '../lib/api'
import { openErrorMessage } from '../lib/skippedReviews'

const PAGE_SIZE = 50

export default function SkippedReviewsPage() {
  const [status, setStatus] = useState<'loading' | 'ready' | 'error' | 'off'>('loading')
  const [error, setError] = useState<Error | null>(null)
  const [rows, setRows] = useState<ClassifiedReview[]>([])
  const [lastCount, setLastCount] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)
  const [rowStates, setRowStates] = useState<Record<string, RowState>>({})

  const loadFirst = useCallback(() => {
    setStatus('loading')
    listClassifiedReviews(PAGE_SIZE, 0)
      .then(res => {
        setRows(res.results)
        setLastCount(res.count)
        setStatus('ready')
      })
      .catch((err: unknown) => {
        if (err instanceof ClassifiedReviewsDisabledError) {
          setStatus('off')
        } else {
          setError(err instanceof Error ? err : new Error('Could not load reviews.'))
          setStatus('error')
        }
      })
  }, [])

  useEffect(() => { loadFirst() }, [loadFirst])

  async function loadMore() {
    setLoadingMore(true)
    setLoadMoreError(null)
    try {
      const res = await listClassifiedReviews(PAGE_SIZE, rows.length)
      setRows(prev => {
        const seen = new Set(prev.map(r => r.review_id))
        return [...prev, ...res.results.filter(r => !seen.has(r.review_id))]
      })
      setLastCount(res.count)
    } catch {
      setLoadMoreError('Could not load more reviews. Try again.')
    } finally {
      setLoadingMore(false)
    }
  }

  async function analyse(reviewId: string) {
    setRowStates(s => ({ ...s, [reviewId]: { phase: 'pending' } }))
    try {
      const result = await openClassifiedReview(reviewId)
      setRowStates(s => ({ ...s, [reviewId]: { phase: 'done', result } }))
    } catch (err) {
      setRowStates(s => ({ ...s, [reviewId]: { phase: 'error', message: openErrorMessage(err) } }))
    }
  }

  return (
    <Layout active="skipped">
      <SkippedReviewsView
        status={status}
        rows={rows}
        error={error}
        onRetry={loadFirst}
        rowStates={rowStates}
        onAnalyse={analyse}
        hasMore={lastCount === PAGE_SIZE}
        loadingMore={loadingMore}
        loadMoreError={loadMoreError}
        onLoadMore={loadMore}
      />
    </Layout>
  )
}
