import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, AlertTriangle, Upload } from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import { getReviews, type Review } from '../lib/api'

// Sentiment colours
const SENTIMENT_STYLE: Record<string, string> = {
  positive: 'bg-green-light text-green',
  negative: 'bg-amber-light text-amber',
  neutral: 'bg-gray-100 text-charcoal-light',
  mixed: 'bg-purple-50 text-purple-600',
}

const SENTIMENT_LABEL: Record<string, string> = {
  positive: 'Positive', negative: 'Negative', neutral: 'Neutral', mixed: 'Mixed',
}

export default function ReviewsPage() {
  const [reviews, setReviews] = useState<Review[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [offset, setOffset] = useState(0)
  const navigate = useNavigate()
  const PAGE = 25

  async function load(reset = false) {
    const currentOffset = reset ? 0 : offset
    reset ? setLoading(true) : setLoadingMore(true)
    setError(null)
    try {
      const data = await getReviews({ limit: PAGE, offset: currentOffset })
      setReviews(prev => reset ? data.results : [...prev, ...data.results])
      setOffset(currentOffset + data.results.length)
      setHasMore(data.results.length === PAGE)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Could not load reviews'))
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }

  useEffect(() => { load(true) }, [])

  function openDetail(review: Review) {
    const hash = review.input_hash.replace('sha256:', '')
    navigate(`/reviews/${hash}`, { state: { review } })
  }

  return (
    <Layout active="reviews">
      <div className="max-w-3xl">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="font-display text-2xl text-charcoal">Your reviews</h1>
            <p className="text-sm text-charcoal-light font-sans mt-0.5">
              Click any review to see its analysis and draft a reply.
            </p>
          </div>
        </div>

        {loading && <SkeletonList />}

        {error && <ErrorBox error={error} onRetry={() => load(true)} />}

        {!loading && !error && reviews.length === 0 && (
          <div className="text-center py-16">
            <Upload size={32} className="text-charcoal-light/40 mx-auto mb-4" />
            <h2 className="font-display text-lg text-charcoal mb-2">No reviews yet</h2>
            <p className="text-sm text-charcoal-light font-sans mb-4">
              Upload a CSV of customer reviews to get started.
            </p>
            <button
              onClick={() => navigate('/upload')}
              className="inline-flex items-center gap-2 bg-green hover:bg-green-muted text-white text-sm font-sans font-medium py-2.5 px-5 rounded-lg transition-colors"
            >
              <Upload size={14} /> Upload CSV
            </button>
          </div>
        )}

        {!loading && reviews.length > 0 && (
          <div className="space-y-2">
            {reviews.map(review => (
              <button
                key={review.input_hash}
                onClick={() => openDetail(review)}
                className="w-full text-left bg-white rounded-lg border border-gray-100 shadow-card hover:shadow-card-hover hover:border-gray-200 px-5 py-4 flex items-center gap-4 transition-all group"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-sans font-medium text-charcoal text-sm truncate">
                      {review.product}
                    </span>
                    {review.sentiment && (
                      <span className={`text-xs font-sans px-2 py-0.5 rounded-full shrink-0 ${SENTIMENT_STYLE[review.sentiment] ?? 'bg-gray-100 text-charcoal-light'}`}>
                        {SENTIMENT_LABEL[review.sentiment] ?? review.sentiment}
                      </span>
                    )}
                    {review.urgency === 'high' && (
                      <span className="flex items-center gap-0.5 text-xs font-sans text-amber bg-amber-light px-2 py-0.5 rounded-full shrink-0">
                        <AlertTriangle size={10} /> Urgent
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-charcoal-light font-sans leading-relaxed truncate">
                    {review.review_text?.slice(0, 120)}{(review.review_text?.length ?? 0) > 120 ? '…' : ''}
                  </p>
                </div>
                <ChevronRight size={16} className="text-charcoal-light/40 group-hover:text-charcoal-light shrink-0 transition-colors" />
              </button>
            ))}

            {hasMore && (
              <button
                onClick={() => load(false)}
                disabled={loadingMore}
                className="w-full mt-2 py-3 text-sm font-sans text-green hover:text-green-muted border border-gray-100 rounded-lg bg-white shadow-card transition-colors disabled:opacity-50"
              >
                {loadingMore ? 'Loading…' : 'Load more reviews'}
              </button>
            )}
          </div>
        )}
      </div>
    </Layout>
  )
}

function SkeletonList() {
  return (
    <div className="space-y-2 animate-pulse">
      {[1, 2, 3, 4, 5].map(i => (
        <div key={i} className="h-16 bg-white rounded-lg border border-gray-100 shadow-card" />
      ))}
    </div>
  )
}
