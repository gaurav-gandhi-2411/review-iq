import { useEffect, useState } from 'react'
import { Flag, Copy, Check, ShieldCheck, AlertTriangle, AlertOctagon } from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import { getFlaggedReviews, type FlaggedReview, type AuthLabel } from '../lib/api'

const LABEL_CONFIG: Record<AuthLabel, { label: string; icon: typeof ShieldCheck; badgeClass: string }> = {
  genuine: { label: 'Clear', icon: ShieldCheck, badgeClass: 'bg-green-light text-green' },
  suspicious: { label: 'Flagged for review', icon: AlertTriangle, badgeClass: 'bg-yellow-50 text-yellow-700' },
  likely_fake: { label: 'Priority review', icon: AlertOctagon, badgeClass: 'bg-amber-light text-amber' },
}

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export default function FlaggedReviewsPage() {
  const [reviews, setReviews] = useState<FlaggedReview[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [copiedHash, setCopiedHash] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const data = await getFlaggedReviews()
      setReviews(data.results)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Could not load flagged reviews'))
    } finally {
      setLoading(false)
    }
  }

  // eslint-disable-next-line react-hooks/set-state-in-effect -- async data-loading in effect is intentional
  useEffect(() => { load() }, [])

  async function copyHash(hash: string) {
    await navigator.clipboard.writeText(hash)
    setCopiedHash(hash)
    setTimeout(() => setCopiedHash(null), 2000)
  }

  return (
    <Layout active="flagged">
      <div className="max-w-3xl">
        <div className="mb-6">
          <h1 className="font-display text-2xl text-charcoal mb-1">Flagged reviews</h1>
          <p className="text-sm text-charcoal-light font-sans">
            Reviews with authenticity signals worth a closer look, most recent first.
          </p>
        </div>

        {loading && <SkeletonList />}
        {!loading && error && <ErrorBox error={error} onRetry={load} />}

        {!loading && !error && reviews && reviews.length === 0 && (
          <div className="text-center py-16">
            <Flag size={32} className="text-charcoal-light/40 mx-auto mb-4" />
            <h2 className="font-display text-lg text-charcoal mb-2">No flagged reviews</h2>
            <p className="text-sm text-charcoal-light font-sans">
              Nothing here yet — reviews are flagged as authenticity checks are run.
            </p>
          </div>
        )}

        {!loading && !error && reviews && reviews.length > 0 && (
          <div className="bg-white rounded-xl border border-gray-100 shadow-card overflow-hidden">
            <table className="w-full text-sm font-sans">
              <thead>
                <tr className="border-b border-gray-100 text-left">
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Review</th>
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Status</th>
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Score</th>
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Signals</th>
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Flagged</th>
                </tr>
              </thead>
              <tbody>
                {reviews.map(r => {
                  const cfg = LABEL_CONFIG[r.label] ?? LABEL_CONFIG.suspicious
                  const Icon = cfg.icon
                  return (
                    <tr
                      key={r.review_hash}
                      className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors"
                    >
                      <td className="px-5 py-3">
                        <span className="text-charcoal font-mono text-xs">{r.review_hash.slice(0, 10)}…</span>
                        <button
                          onClick={() => copyHash(r.review_hash)}
                          className="ml-1.5 inline-flex align-middle text-charcoal-light hover:text-charcoal transition-colors"
                          aria-label="Copy review hash"
                        >
                          {copiedHash === r.review_hash ? <Check size={12} /> : <Copy size={12} />}
                        </button>
                      </td>
                      <td className="px-5 py-3">
                        <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full ${cfg.badgeClass}`}>
                          <Icon size={12} /> {cfg.label}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-charcoal-light">{Math.round(r.score * 100)}%</td>
                      <td className="px-5 py-3 text-charcoal-light text-xs">
                        {r.flags.length > 0 ? r.flags.join(', ') : '—'}
                      </td>
                      <td className="px-5 py-3 text-charcoal-light text-xs whitespace-nowrap">
                        {timeAgo(r.created_at)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
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
        <div key={i} className="h-12 bg-white rounded-lg border border-gray-100 shadow-card" />
      ))}
    </div>
  )
}
