import { useNavigate } from 'react-router-dom'
import { ChevronRight, AlertTriangle, Upload } from 'lucide-react'
import Layout from '../components/Layout'
import FilterBar from '../components/FilterBar'
import ErrorBox from '../components/ErrorBox'
import { useFilterContext } from '../lib/filterContext'
import type { Review } from '../lib/api'

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
  const { filteredReviews, loading, loadError, hasActiveFilters, stats, setFilter } = useFilterContext()
  const navigate = useNavigate()

  function openDetail(review: Review) {
    const hash = review.input_hash.replace('sha256:', '')
    navigate(`/reviews/${hash}`, { state: { review } })
  }

  return (
    <Layout active="reviews">
      <div className="max-w-3xl">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="font-display text-2xl text-charcoal">Your reviews</h1>
            <p className="text-sm text-charcoal-light font-sans mt-0.5">
              {hasActiveFilters
                ? `${filteredReviews.length} of ${stats.total} reviews match active filters.`
                : 'Click any review to see its analysis and draft a reply.'}
            </p>
          </div>
        </div>

        {/* Quick filter chips */}
        {!loading && !loadError && stats.total > 0 && (
          <div className="flex flex-wrap gap-2 mb-4">
            <span className="text-xs font-sans text-charcoal-light self-center">Quick filter:</span>
            <button
              onClick={() => setFilter('urgency', 'high')}
              className="text-xs font-sans px-2.5 py-1 rounded-full bg-amber-light text-amber hover:ring-2 hover:ring-amber/30 transition-all"
            >
              Urgent
            </button>
            <button
              onClick={() => setFilter('sentiment', 'negative')}
              className="text-xs font-sans px-2.5 py-1 rounded-full bg-amber-light text-amber hover:ring-2 hover:ring-amber/30 transition-all"
            >
              Negative
            </button>
            <button
              onClick={() => setFilter('sentiment', 'positive')}
              className="text-xs font-sans px-2.5 py-1 rounded-full bg-green-light text-green hover:ring-2 hover:ring-green/30 transition-all"
            >
              Positive
            </button>
          </div>
        )}

        <FilterBar />

        {loading && <SkeletonList />}

        {!loading && loadError && <ErrorBox error={loadError} onRetry={() => window.location.reload()} />}

        {!loading && !loadError && filteredReviews.length === 0 && !hasActiveFilters && (
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

        {!loading && !loadError && filteredReviews.length === 0 && hasActiveFilters && (
          <div className="text-center py-12">
            <p className="text-charcoal-light font-sans text-sm">No reviews match the active filters.</p>
          </div>
        )}

        {!loading && !loadError && filteredReviews.length > 0 && (
          <div className="space-y-2">
            {filteredReviews.map(review => (
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
