import { useNavigate } from 'react-router-dom'
import { Upload, AlertTriangle, Heart } from 'lucide-react'
import Layout from '../components/Layout'
import FilterBar from '../components/FilterBar'
import ErrorBox from '../components/ErrorBox'
import { useFilterContext } from '../lib/filterContext'

export default function DashboardPage() {
  const { stats, loading, loadError, setFilter, filteredReviews, hasActiveFilters } = useFilterContext()
  const navigate = useNavigate()

  const total = stats.total
  const s_score = total > 0 ? stats.positiveCount / total : 0
  const u_score = total > 0 ? 1 - stats.highUrgencyCount / total : 1
  // auth component approximated as 1.0 (authenticity page has its own view)
  const score = Math.round((0.50 * s_score + 0.20 * u_score + 0.30 * 1.0) * 100)
  const band: 'healthy' | 'needs_attention' | 'at_risk' =
    score >= 75 ? 'healthy' : score >= 50 ? 'needs_attention' : 'at_risk'

  return (
    <Layout active="dashboard">
      <div className="max-w-3xl">
        <h1 className="font-display text-2xl text-charcoal mb-1">What customers are saying</h1>
        <p className="text-sm text-charcoal-light font-sans mb-6">
          {hasActiveFilters
            ? `Showing ${total} filtered review${total !== 1 ? 's' : ''}.`
            : 'Based on your uploaded reviews.'}
        </p>

        <FilterBar />

        {loading && <SkeletonDashboard />}

        {!loading && loadError && (
          <ErrorBox error={loadError} onRetry={() => window.location.reload()} />
        )}

        {!loading && !loadError && total === 0 && !hasActiveFilters && (
          <EmptyState onUpload={() => navigate('/upload')} />
        )}

        {!loading && !loadError && total === 0 && hasActiveFilters && (
          <div className="text-center py-12">
            <p className="text-charcoal-light font-sans text-sm">No reviews match the active filters.</p>
          </div>
        )}

        {!loading && !loadError && total > 0 && (
          <div className="space-y-6">
            {/* Health score card */}
            <HealthCard
              score={score}
              band={band}
              positiveCount={stats.positiveCount}
              total={total}
              highUrgencyCount={stats.highUrgencyCount}
              onFilterSentiment={(v) => setFilter('sentiment', v)}
              onFilterUrgency={(v) => setFilter('urgency', v)}
            />

            {/* Top concern themes */}
            {stats.topTopics.length > 0 && (
              <div>
                <h2 className="font-sans font-semibold text-charcoal text-base mb-3">
                  Top concerns from customers
                </h2>
                <div className="space-y-3">
                  {stats.topTopics.map((t, i) => (
                    <TopicCard
                      key={t.topic}
                      topic={t.topic}
                      count={t.count}
                      rank={i + 1}
                      onClick={() => setFilter('topic', t.topic)}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* See all reviews CTA */}
            <button
              onClick={() => navigate('/reviews')}
              className="w-full text-left bg-white rounded-xl border border-gray-100 shadow-card p-5 flex items-center justify-between hover:border-gray-200 hover:shadow-card-hover transition-all group"
            >
              <div>
                <p className="font-sans font-medium text-charcoal text-sm">See all reviews</p>
                <p className="font-sans text-xs text-charcoal-light mt-0.5">
                  Browse individual reviews, view analysis and draft replies.
                </p>
              </div>
              <span className="text-sm font-sans text-green group-hover:text-green-muted transition-colors">
                {hasActiveFilters ? `See ${total} filtered reviews` : 'See all reviews'} →
              </span>
            </button>

            {/* Upload more CTA */}
            <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6 flex items-center justify-between">
              <div>
                <p className="font-sans font-medium text-charcoal text-sm">Add more reviews</p>
                <p className="font-sans text-xs text-charcoal-light mt-0.5">
                  {filteredReviews.length !== total
                    ? `${filteredReviews.length} of ${total} reviews showing`
                    : `${total} reviews analysed so far`}
                </p>
              </div>
              <button
                onClick={() => navigate('/upload')}
                className="flex items-center gap-2 bg-charcoal hover:bg-charcoal/90 text-white text-sm font-sans font-medium py-2 px-4 rounded-lg transition-colors"
              >
                <Upload size={14} /> Upload CSV
              </button>
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}

function HealthCard({
  score,
  band,
  positiveCount,
  total,
  highUrgencyCount,
  onFilterSentiment,
  onFilterUrgency,
}: {
  score: number
  band: 'healthy' | 'needs_attention' | 'at_risk'
  positiveCount: number
  total: number
  highUrgencyCount: number
  onFilterSentiment: (v: string) => void
  onFilterUrgency: (v: string) => void
}) {
  void total // used by caller for context; individual count is displayed instead

  const bandLabel = {
    healthy: 'Looking healthy',
    needs_attention: 'Needs attention',
    at_risk: 'At risk',
  }[band]

  const bandColor = {
    healthy: 'text-green',
    needs_attention: 'text-yellow-600',
    at_risk: 'text-amber',
  }[band]

  const bandBg = {
    healthy: 'bg-green-light',
    needs_attention: 'bg-yellow-50',
    at_risk: 'bg-amber-light',
  }[band]

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-sans text-charcoal-light uppercase tracking-wide mb-1">Review Health Score</p>
          <div className="flex items-baseline gap-2">
            <span className="font-display text-5xl text-charcoal">{score}</span>
            <span className="text-xl text-charcoal-light font-sans">/100</span>
          </div>
          <span className={`inline-block mt-2 text-xs font-sans font-medium px-2.5 py-1 rounded-full ${bandBg} ${bandColor}`}>
            {bandLabel}
          </span>
        </div>
        <div className="text-right space-y-3">
          <MetricPill
            icon={<Heart size={12} />}
            label="Positive sentiment"
            value={`${positiveCount} reviews`}
            color="green"
            onClick={() => onFilterSentiment('positive')}
          />
          <MetricPill
            icon={<AlertTriangle size={12} />}
            label="Urgent issues"
            value={`${highUrgencyCount} reviews`}
            color={highUrgencyCount > 5 ? 'amber' : 'neutral'}
            onClick={() => onFilterUrgency('high')}
          />
        </div>
      </div>
    </div>
  )
}

function MetricPill({
  icon,
  label,
  value,
  color,
  onClick,
}: {
  icon: React.ReactNode
  label: string
  value: string
  color: 'green' | 'amber' | 'neutral'
  onClick?: () => void
}) {
  const colorClass = {
    green: 'text-green bg-green-light',
    amber: 'text-amber bg-amber-light',
    neutral: 'text-charcoal-light bg-gray-50',
  }[color]

  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 text-xs font-sans px-2.5 py-1 rounded-full transition-all ${colorClass} ${onClick ? 'hover:ring-2 hover:ring-offset-1 hover:ring-current/30 cursor-pointer' : 'cursor-default'}`}
    >
      {icon}
      <span className="font-medium">{value}</span>
      <span className="opacity-70">· {label}</span>
    </button>
  )
}

function TopicCard({
  topic,
  count,
  rank,
  onClick,
}: {
  topic: string
  count: number
  rank: number
  onClick: () => void
}) {
  const label = topic.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

  return (
    <button
      onClick={onClick}
      className="w-full text-left bg-white rounded-lg border border-gray-100 shadow-card px-5 py-4 flex items-center gap-4 hover:border-green/30 hover:shadow-card-hover transition-all group"
    >
      <span className="font-display text-2xl text-charcoal-light/40 w-6 shrink-0">{rank}</span>
      <div className="flex-1 min-w-0">
        <p className="font-sans font-medium text-charcoal text-sm">{label}</p>
        <p className="font-sans text-xs text-charcoal-light mt-0.5">
          {count} mention{count !== 1 ? 's' : ''}
        </p>
      </div>
      <span className="text-xs font-sans text-green opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        Filter →
      </span>
    </button>
  )
}

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="text-center py-16">
      <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-green-light mb-6">
        <Upload size={24} className="text-green" />
      </div>
      <h2 className="font-display text-xl text-charcoal mb-2">Your dashboard is ready</h2>
      <p className="text-sm text-charcoal-light font-sans max-w-xs mx-auto leading-relaxed mb-6">
        Upload your first batch of customer reviews to see what people actually think about your products.
      </p>
      <button
        onClick={onUpload}
        className="inline-flex items-center gap-2 bg-green hover:bg-green-muted text-white text-sm font-sans font-medium py-3 px-6 rounded-lg transition-colors"
      >
        <Upload size={15} /> Upload your first reviews
      </button>
    </div>
  )
}

function SkeletonDashboard() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-36 bg-white rounded-xl border border-gray-100 shadow-card" />
      <div className="h-4 w-48 bg-gray-100 rounded" />
      {[1, 2, 3].map(i => (
        <div key={i} className="h-16 bg-white rounded-lg border border-gray-100 shadow-card" />
      ))}
    </div>
  )
}
