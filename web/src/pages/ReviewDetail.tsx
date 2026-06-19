import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, Copy, Check, Loader2, RefreshCw,
  ShieldCheck, AlertTriangle, AlertOctagon,
} from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import {
  scoreAuthenticity, draftReply,
  type Review, type AuthenticityResult, type ReplyDraft, type ReplyTone,
  QuotaError, ServiceWarmingError,
} from '../lib/api'

// --- Constants ---

const TONE_LABELS: Record<ReplyTone, string> = {
  warm: 'Warm & personal',
  apologetic: 'Apologetic',
  professional: 'Professional',
  appreciative: 'Appreciative',
}

const DISPOSITION_CONFIG: Record<string, {
  label: string
  icon: typeof ShieldCheck
  className: string
  badgeClass: string
}> = {
  genuine: {
    label: 'Clear',
    icon: ShieldCheck,
    className: 'text-green',
    badgeClass: 'bg-green-light text-green',
  },
  suspicious: {
    label: 'Flagged for review',
    icon: AlertTriangle,
    className: 'text-yellow-600',
    badgeClass: 'bg-yellow-50 text-yellow-700',
  },
  likely_fake: {
    label: 'Priority review',
    icon: AlertOctagon,
    className: 'text-amber',
    badgeClass: 'bg-amber-light text-amber',
  },
}

const FLAG_LABELS: Record<string, string> = {
  incentivized_phrase: 'Disclosed incentive detected',
  rating_text_mismatch: "Rating doesn't match text sentiment",
  generic_low_info: 'Very generic, low information',
  excessive_brevity: 'Unusually short review',
  promotional_tone: 'Promotional language detected',
  near_duplicate: 'Similar to another review',
  review_burst: 'Part of a sudden review burst',
  repetitive_content: 'Templated or repeated content',
}

const SENTIMENT_LABEL: Record<string, string> = {
  positive: 'Positive', negative: 'Negative',
  neutral: 'Neutral', mixed: 'Mixed',
}
const URGENCY_LABEL: Record<string, string> = {
  low: 'Low', medium: 'Medium', high: 'High — needs attention',
}

// --- Component ---

export default function ReviewDetailPage() {
  const { reviewHash } = useParams<{ reviewHash: string }>()
  const location = useLocation()
  const navigate = useNavigate()

  // Review comes from navigation state (no extra fetch needed)
  const review: Review | undefined = location.state?.review

  const [authResult, setAuthResult] = useState<AuthenticityResult | null>(null)
  const [authLoading, setAuthLoading] = useState(false)
  const [authError, setAuthError] = useState<Error | null>(null)

  const [selectedTone, setSelectedTone] = useState<ReplyTone>('professional')
  const [draft, setDraft] = useState<ReplyDraft | null>(null)
  const [draftLoading, setDraftLoading] = useState(false)
  const [draftError, setDraftError] = useState<Error | null>(null)
  const [copied, setCopied] = useState(false)

  // If no review in state, go back
  useEffect(() => {
    if (!review) navigate('/reviews', { replace: true })
  }, [review, navigate])

  // Suppress unused param warning — reviewHash is used by the route for URL structure
  void reviewHash

  if (!review) return null

  // r is guaranteed non-null here — TypeScript can't narrow across async closures,
  // so we capture the narrowed value explicitly.
  const r = review

  async function loadAuthenticity() {
    setAuthLoading(true)
    setAuthError(null)
    try {
      const result = await scoreAuthenticity(r.review_text, r.stars)
      setAuthResult(result)
    } catch (err) {
      setAuthError(err instanceof Error ? err : new Error('Authenticity scoring failed'))
    } finally {
      setAuthLoading(false)
    }
  }

  async function handleDraftReply() {
    setDraftLoading(true)
    setDraftError(null)
    setDraft(null)
    try {
      const result = await draftReply(r.review_text, selectedTone, {
        product: r.product,
        pros: r.pros,
        cons: r.cons,
        sentiment: r.sentiment,
        urgency: r.urgency,
      })
      setDraft(result)
    } catch (err) {
      setDraftError(err instanceof Error ? err : new Error('Reply drafting failed'))
    } finally {
      setDraftLoading(false)
    }
  }

  async function copyReply() {
    if (!draft) return
    await navigator.clipboard.writeText(draft.reply_text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const isCapError = draftError instanceof QuotaError || draftError instanceof ServiceWarmingError

  return (
    <Layout active="reviews">
      <div className="max-w-2xl">
        {/* Back */}
        <button
          onClick={() => navigate('/reviews')}
          className="flex items-center gap-1.5 text-sm text-charcoal-light hover:text-charcoal font-sans mb-6 transition-colors"
        >
          <ArrowLeft size={14} /> Back to reviews
        </button>

        {/* Review text */}
        <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6 mb-4">
          <p className="text-xs font-sans text-charcoal-light uppercase tracking-wide mb-3">
            Customer review · {review.product}
          </p>
          <blockquote className="font-sans text-charcoal text-sm leading-relaxed border-l-2 border-green pl-4 italic">
            "{review.review_text}"
          </blockquote>
          {review.stars && (
            <p className="mt-3 text-xs font-sans text-charcoal-light">
              {'★'.repeat(review.stars)}{'☆'.repeat(5 - review.stars)} {review.stars}/5
            </p>
          )}
        </div>

        {/* Extraction breakdown */}
        <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6 mb-4">
          <h2 className="font-sans font-semibold text-charcoal text-sm mb-4">What we found</h2>
          <div className="grid grid-cols-2 gap-3 text-sm">
            {review.sentiment && (
              <InfoCell label="Sentiment" value={SENTIMENT_LABEL[review.sentiment] ?? review.sentiment} />
            )}
            <InfoCell
              label="Urgency"
              value={URGENCY_LABEL[review.urgency] ?? review.urgency}
              valueClass={review.urgency === 'high' ? 'text-amber font-medium' : undefined}
            />
          </div>

          {review.pros.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-sans text-charcoal-light uppercase tracking-wide mb-2">
                What they liked
              </p>
              <ul className="space-y-1">
                {review.pros.map((p, i) => (
                  <li key={i} className="text-sm font-sans text-charcoal flex gap-2">
                    <span className="text-green shrink-0">+</span> {p}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {review.cons.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-sans text-charcoal-light uppercase tracking-wide mb-2">
                What they didn't like
              </p>
              <ul className="space-y-1">
                {review.cons.map((c, i) => (
                  <li key={i} className="text-sm font-sans text-charcoal flex gap-2">
                    <span className="text-amber shrink-0">−</span> {c}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {review.topics.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-sans text-charcoal-light uppercase tracking-wide mb-2">Topics</p>
              <div className="flex flex-wrap gap-1.5">
                {review.topics.map(t => (
                  <span key={t} className="text-xs font-sans bg-gray-50 text-charcoal-light border border-gray-100 px-2 py-0.5 rounded-full">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {review.feature_requests.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-sans text-charcoal-light uppercase tracking-wide mb-2">
                Feature requests
              </p>
              <ul className="space-y-1">
                {review.feature_requests.map((f, i) => (
                  <li key={i} className="text-sm font-sans text-charcoal flex gap-2">
                    <span className="text-charcoal-light shrink-0">→</span> {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Authenticity section */}
        <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6 mb-4">
          <div className="flex items-center justify-between">
            <h2 className="font-sans font-semibold text-charcoal text-sm">Authenticity check</h2>
            {!authResult && !authLoading && (
              <button
                onClick={loadAuthenticity}
                className="text-xs font-sans text-green hover:text-green-muted transition-colors"
              >
                Run check
              </button>
            )}
          </div>

          {!authResult && !authLoading && !authError && (
            <p className="text-xs text-charcoal-light font-sans mt-2">
              Check for signals that suggest this review warrants closer review.
            </p>
          )}

          {authLoading && (
            <div className="flex items-center gap-2 mt-3">
              <Loader2 size={14} className="animate-spin text-charcoal-light" />
              <span className="text-sm font-sans text-charcoal-light">Checking signals…</span>
            </div>
          )}

          {authError && (
            <div className="mt-3">
              <ErrorBox error={authError} onRetry={loadAuthenticity} />
            </div>
          )}

          {authResult && (
            <div className="mt-3">
              {(() => {
                const cfg = DISPOSITION_CONFIG[authResult.label] ?? DISPOSITION_CONFIG.genuine
                const Icon = cfg.icon
                return (
                  <div className="flex items-center gap-2 mb-3">
                    <span className={`inline-flex items-center gap-1.5 text-sm font-sans font-medium px-3 py-1 rounded-full ${cfg.badgeClass}`}>
                      <Icon size={13} /> {cfg.label}
                    </span>
                    <span className="text-xs text-charcoal-light font-sans">
                      {Math.round(authResult.score * 100)}% genuine signal
                    </span>
                  </div>
                )
              })()}

              {authResult.flags.length > 0 && (
                <div className="space-y-1 mb-3">
                  {authResult.flags.map(flag => (
                    <div key={flag} className="flex items-center gap-2 text-xs font-sans text-charcoal-light">
                      <span className="w-1 h-1 bg-amber rounded-full shrink-0" />
                      {FLAG_LABELS[flag] ?? flag}
                    </div>
                  ))}
                </div>
              )}

              <p className="text-xs text-charcoal-light/70 font-sans italic border-t border-gray-50 pt-2">
                Signals support human moderation under IS 19000:2022. This is a priority indicator, not a verdict.
              </p>
            </div>
          )}
        </div>

        {/* Reply drafting section */}
        <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6">
          <h2 className="font-sans font-semibold text-charcoal text-sm mb-4">Draft a reply</h2>

          {/* Tone selector */}
          <div className="flex flex-wrap gap-2 mb-4">
            {(Object.keys(TONE_LABELS) as ReplyTone[]).map(tone => (
              <button
                key={tone}
                onClick={() => { setSelectedTone(tone); setDraft(null); setDraftError(null) }}
                className={`text-xs font-sans px-3 py-1.5 rounded-lg border transition-all ${
                  selectedTone === tone
                    ? 'bg-charcoal text-white border-charcoal'
                    : 'bg-white text-charcoal-light border-gray-200 hover:border-gray-300 hover:text-charcoal'
                }`}
              >
                {TONE_LABELS[tone]}
              </button>
            ))}
          </div>

          {/* Draft button */}
          {!draft && (
            <button
              onClick={handleDraftReply}
              disabled={draftLoading}
              className="w-full flex items-center justify-center gap-2 bg-green hover:bg-green-muted disabled:opacity-60 text-white text-sm font-sans font-medium py-3 px-4 rounded-lg transition-colors"
            >
              {draftLoading ? (
                <><Loader2 size={15} className="animate-spin" /> Drafting reply…</>
              ) : (
                'Draft reply'
              )}
            </button>
          )}

          {/* Graceful failure — the critical path */}
          {draftError && (
            <div className={`rounded-lg border p-4 ${isCapError ? 'bg-amber-light border-amber/20' : 'bg-red-50 border-red-100'}`}>
              <p className="text-sm font-sans font-medium text-charcoal mb-1">
                {isCapError ? 'Drafting is busy' : 'Reply drafting unavailable'}
              </p>
              <p className="text-sm font-sans text-charcoal-light">
                {isCapError
                  ? 'The reply service handles high request volume — try again in a minute.'
                  : draftError.message}
              </p>
              <button
                onClick={handleDraftReply}
                className="mt-2 flex items-center gap-1 text-xs font-sans text-green hover:text-green-muted transition-colors"
              >
                <RefreshCw size={12} /> Try again
              </button>
            </div>
          )}

          {/* Drafted reply */}
          {draft && (
            <div className="space-y-3">
              <div className="bg-gray-50 rounded-lg border border-gray-100 p-4 relative">
                <p className="text-sm font-sans text-charcoal leading-relaxed whitespace-pre-wrap">
                  {draft.reply_text}
                </p>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-xs text-charcoal-light font-sans">
                  {TONE_LABELS[draft.tone]} · {draft.language.toUpperCase()}
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => { setDraft(null); setDraftError(null) }}
                    className="text-xs font-sans text-charcoal-light hover:text-charcoal px-3 py-1.5 rounded-lg border border-gray-200 hover:border-gray-300 transition-colors"
                  >
                    Redraft
                  </button>
                  <button
                    onClick={copyReply}
                    className={`flex items-center gap-1.5 text-xs font-sans px-3 py-1.5 rounded-lg border transition-all ${
                      copied
                        ? 'bg-green text-white border-green'
                        : 'bg-charcoal text-white border-charcoal hover:bg-charcoal/90'
                    }`}
                  >
                    {copied ? <><Check size={12} /> Copied!</> : <><Copy size={12} /> Copy reply</>}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </Layout>
  )
}

function InfoCell({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <p className="text-xs font-sans text-charcoal-light mb-0.5">{label}</p>
      <p className={`text-sm font-sans font-medium text-charcoal ${valueClass ?? ''}`}>{value}</p>
    </div>
  )
}
