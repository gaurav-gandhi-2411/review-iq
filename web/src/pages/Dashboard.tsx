import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, TrendingDown, Minus, Upload, AlertTriangle, Heart, ShieldCheck } from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import { getHealthScore, getTrends, type HealthScore, type TrendTheme } from '../lib/api'

type LoadState = 'loading' | 'empty' | 'loaded' | 'error'

export default function DashboardPage() {
  const [state, setState] = useState<LoadState>('loading')
  const [health, setHealth] = useState<HealthScore | null>(null)
  const [themes, setThemes] = useState<TrendTheme[]>([])
  const [error, setError] = useState<Error | null>(null)
  const navigate = useNavigate()

  async function load() {
    setState('loading')
    setError(null)
    try {
      const [h, t] = await Promise.all([getHealthScore(), getTrends(5)])
      if (h.total_extractions === 0) {
        setState('empty')
        return
      }
      setHealth(h)
      setThemes(t.themes)
      setState('loaded')
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Could not load your dashboard'))
      setState('error')
    }
  }

  useEffect(() => { load() }, [])

  return (
    <Layout active="dashboard">
      <div className="max-w-3xl">
        <h1 className="font-display text-2xl text-charcoal mb-1">What customers are saying</h1>
        <p className="text-sm text-charcoal-light font-sans mb-8">
          Based on your uploaded reviews.
        </p>

        {state === 'loading' && <SkeletonDashboard />}

        {state === 'error' && error && (
          <ErrorBox error={error} onRetry={load} />
        )}

        {state === 'empty' && <EmptyState onUpload={() => navigate('/upload')} />}

        {state === 'loaded' && health && (
          <div className="space-y-6">
            {/* Health score card */}
            <HealthCard health={health} />

            {/* Top complaint themes */}
            {themes.length > 0 && (
              <div>
                <h2 className="font-sans font-semibold text-charcoal text-base mb-3">
                  Top concerns from customers
                </h2>
                <div className="space-y-3">
                  {themes.map((theme, i) => (
                    <ThemeCard key={theme.theme} theme={theme} rank={i + 1} />
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
                See all reviews →
              </span>
            </button>

            {/* Upload more CTA */}
            <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6 flex items-center justify-between">
              <div>
                <p className="font-sans font-medium text-charcoal text-sm">Add more reviews</p>
                <p className="font-sans text-xs text-charcoal-light mt-0.5">
                  {health.total_extractions} reviews analysed so far
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

function HealthCard({ health }: { health: HealthScore }) {
  const score = Math.round(health.score * 100)
  const band = health.band

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
            value={`${Math.round(health.components.sentiment.score * 100)}%`}
            color="green"
          />
          <MetricPill
            icon={<AlertTriangle size={12} />}
            label="Urgent issues"
            value={`${health.components.urgency.high_urgency_count} reviews`}
            color={health.components.urgency.high_urgency_count > 5 ? 'amber' : 'neutral'}
          />
          <MetricPill
            icon={<ShieldCheck size={12} />}
            label="Flagged for review"
            value={`${health.components.authenticity.priority_review_count} reviews`}
            color={health.components.authenticity.priority_review_count > 0 ? 'amber' : 'neutral'}
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
}: {
  icon: React.ReactNode
  label: string
  value: string
  color: 'green' | 'amber' | 'neutral'
}) {
  const colorClass = {
    green: 'text-green bg-green-light',
    amber: 'text-amber bg-amber-light',
    neutral: 'text-charcoal-light bg-gray-50',
  }[color]

  return (
    <div className={`flex items-center gap-1.5 text-xs font-sans px-2.5 py-1 rounded-full ${colorClass}`}>
      {icon}
      <span className="font-medium">{value}</span>
      <span className="opacity-70">· {label}</span>
    </div>
  )
}

function ThemeCard({ theme, rank }: { theme: TrendTheme; rank: number }) {
  const pct = theme.pct_change
  const delta = theme.delta_last

  const TrendIcon = delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus
  const trendColor = delta > 0 ? 'text-amber' : delta < 0 ? 'text-green' : 'text-charcoal-light'

  // Plain-English theme name: capitalise and clean up underscore-separated labels
  const label = theme.theme
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())

  return (
    <div className="bg-white rounded-lg border border-gray-100 shadow-card px-5 py-4 flex items-center gap-4">
      <span className="font-display text-2xl text-charcoal-light/40 w-6 shrink-0">
        {rank}
      </span>
      <div className="flex-1 min-w-0">
        <p className="font-sans font-medium text-charcoal text-sm">{label}</p>
        <p className="font-sans text-xs text-charcoal-light mt-0.5">
          {theme.total} mention{theme.total !== 1 ? 's' : ''} total
        </p>
      </div>
      <div className={`flex items-center gap-1 text-xs font-sans ${trendColor} shrink-0`}>
        <TrendIcon size={14} />
        {pct !== null
          ? `${delta > 0 ? '+' : ''}${Math.round(pct * 100)}% this month`
          : delta !== 0
          ? `${delta > 0 ? '+' : ''}${delta} this month`
          : 'Stable'}
      </div>
    </div>
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
