import { useEffect, useState } from 'react'
import { ShieldCheck, AlertTriangle, AlertOctagon, BarChart2 } from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import { getAuthInsights, type AuthInsights } from '../lib/api'

export default function AuthenticityPage() {
  const [data, setData] = useState<AuthInsights | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const result = await getAuthInsights({ bucket: 'month' })
      setData(result)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Could not load authenticity data'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <Layout active="authenticity">
      <div className="max-w-3xl">
        <div className="mb-6">
          <h1 className="font-display text-2xl text-charcoal mb-1">Authenticity audit</h1>
          <p className="text-sm text-charcoal-light font-sans">
            Signal-based review prioritisation. Helps your moderation team focus on the right reviews first.
          </p>
        </div>

        {/* Moderation framing — prominent, always visible */}
        <div className="bg-green-light border border-green/20 rounded-xl p-4 mb-6">
          <p className="text-sm font-sans text-green-muted leading-relaxed">
            <strong className="font-semibold">How to read this:</strong> These signals help prioritise which reviews to check.
            No review is automatically removed or labelled as fake.
            "Flagged for review" means <em>worth a closer look</em>, not a verdict.
            This approach follows IS 19000:2022 guidelines for review moderation.
          </p>
        </div>

        {loading && <SkeletonAuth />}
        {error && <ErrorBox error={error} onRetry={load} />}

        {!loading && !error && data && (
          <div className="space-y-4">
            {data.total_audited === 0 ? (
              <div className="text-center py-12 bg-white rounded-xl border border-gray-100 shadow-card">
                <BarChart2 size={32} className="text-charcoal-light/40 mx-auto mb-3" />
                <h2 className="font-display text-lg text-charcoal mb-2">No audited reviews yet</h2>
                <p className="text-sm text-charcoal-light font-sans max-w-xs mx-auto">
                  Open any review and click "Run check" to start building your authenticity audit trail.
                </p>
              </div>
            ) : (
              <>
                {/* Disposition summary */}
                <div className="grid grid-cols-3 gap-3">
                  <DispositionCard
                    icon={<ShieldCheck size={18} />}
                    label="Clear"
                    count={data.dispositions.clear}
                    total={data.total_audited}
                    colorClass="text-green bg-green-light"
                    description="No signals detected"
                  />
                  <DispositionCard
                    icon={<AlertTriangle size={18} />}
                    label="Flagged for review"
                    count={data.dispositions.flagged_for_review}
                    total={data.total_audited}
                    colorClass="text-yellow-600 bg-yellow-50"
                    description="Worth a closer look"
                  />
                  <DispositionCard
                    icon={<AlertOctagon size={18} />}
                    label="Priority review"
                    count={data.dispositions.priority_review}
                    total={data.total_audited}
                    colorClass="text-amber bg-amber-light"
                    description="Warrants immediate attention"
                  />
                </div>

                {/* Overall flag rate */}
                <div className="bg-white rounded-xl border border-gray-100 shadow-card p-5">
                  <div className="flex items-baseline justify-between mb-3">
                    <h2 className="font-sans font-semibold text-charcoal text-sm">
                      Review flag rate
                    </h2>
                    <span className="font-display text-2xl text-charcoal">
                      {(data.review_flag_rate * 100).toFixed(1)}%
                    </span>
                  </div>
                  <p className="text-xs text-charcoal-light font-sans">
                    of {data.total_audited} audited reviews have at least one signal
                  </p>
                  {/* Simple bar */}
                  <div className="mt-3 h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-amber rounded-full"
                      style={{ width: `${Math.min(data.review_flag_rate * 100, 100)}%` }}
                    />
                  </div>
                </div>

                {/* Signal frequency */}
                {data.signal_frequency.length > 0 && (
                  <div className="bg-white rounded-xl border border-gray-100 shadow-card p-5">
                    <h2 className="font-sans font-semibold text-charcoal text-sm mb-4">
                      Which signals appear most
                    </h2>
                    <div className="space-y-3">
                      {data.signal_frequency.slice(0, 6).map(({ signal, count }) => {
                        const max = data.signal_frequency[0]?.count ?? 1
                        const label = SIGNAL_LABELS[signal] ?? signal.replace(/_/g, ' ')
                        return (
                          <div key={signal}>
                            <div className="flex justify-between text-xs font-sans mb-1">
                              <span className="text-charcoal">{label}</span>
                              <span className="text-charcoal-light">{count}</span>
                            </div>
                            <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                              <div
                                className="h-full bg-charcoal-light/40 rounded-full"
                                style={{ width: `${(count / max) * 100}%` }}
                              />
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Trend over time */}
                {data.flag_rate_series.length > 1 && (
                  <div className="bg-white rounded-xl border border-gray-100 shadow-card p-5">
                    <h2 className="font-sans font-semibold text-charcoal text-sm mb-4">
                      Flag rate over time
                    </h2>
                    <div className="space-y-2">
                      {data.flag_rate_series.slice(-6).map(({ period, review_flag_rate, audited }) => (
                        <div key={period} className="flex items-center gap-3 text-xs font-sans">
                          <span className="text-charcoal-light w-20 shrink-0">{period}</span>
                          <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-amber/60 rounded-full"
                              style={{ width: `${Math.min(review_flag_rate * 100, 100)}%` }}
                            />
                          </div>
                          <span className="text-charcoal-light w-12 text-right shrink-0">
                            {audited} rev.
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <p className="text-xs text-charcoal-light/60 font-sans italic text-center">
                  {data.moderation_note}
                </p>
              </>
            )}
          </div>
        )}
      </div>
    </Layout>
  )
}

const SIGNAL_LABELS: Record<string, string> = {
  disclosed_incentive: 'Disclosed incentive detected',
  rating_text_mismatch: "Rating doesn't match text",
  low_information: 'Very generic, low information',
  very_short: 'Unusually short review',
  promotional_tone: 'Promotional language',
  near_duplicate: 'Similar to another review',
  burst_pattern: 'Part of a review burst',
  templated_pattern: 'Templated content',
}

function DispositionCard({ icon, label, count, total, colorClass, description }: {
  icon: React.ReactNode; label: string; count: number; total: number
  colorClass: string; description: string
}) {
  const pct = total > 0 ? ((count / total) * 100).toFixed(0) : '0'
  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-card p-4">
      <div className={`inline-flex items-center justify-center w-8 h-8 rounded-full mb-3 ${colorClass}`}>
        {icon}
      </div>
      <p className="font-display text-2xl text-charcoal">{count}</p>
      <p className="text-xs font-sans font-medium text-charcoal mt-0.5">{label}</p>
      <p className="text-xs font-sans text-charcoal-light mt-0.5">{pct}% · {description}</p>
    </div>
  )
}

function SkeletonAuth() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="grid grid-cols-3 gap-3">
        {[1,2,3].map(i => <div key={i} className="h-32 bg-white rounded-xl border border-gray-100" />)}
      </div>
      <div className="h-24 bg-white rounded-xl border border-gray-100" />
      <div className="h-48 bg-white rounded-xl border border-gray-100" />
    </div>
  )
}
