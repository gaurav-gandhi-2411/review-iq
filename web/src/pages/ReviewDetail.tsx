import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router'
import {
  ArrowLeft, Copy, Check, Loader2, RefreshCw,
} from 'lucide-react'
import Layout from '../components/Layout'
import {
  draftReply,
  type Review, type ReplyDraft, type ReplyTone,
  QuotaError, ServiceWarmingError, ReplyDraftingDisabledError,
} from '../lib/api'
import { useFilterContext } from '../lib/filterContext'
import { describeWait } from '../lib/retryAfter'

// --- Constants ---

const TONE_LABELS: Record<ReplyTone, string> = {
  warm: 'Warm & personal',
  apologetic: 'Apologetic',
  professional: 'Professional',
  appreciative: 'Appreciative',
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
  const { setFilter } = useFilterContext()

  // Review comes from navigation state (no extra fetch needed)
  const review: Review | undefined = location.state?.review

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

  const isDisabled = draftError instanceof ReplyDraftingDisabledError
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
              <InfoCell
                label="Sentiment"
                value={SENTIMENT_LABEL[review.sentiment] ?? review.sentiment}
                onClick={() => { setFilter('sentiment', review.sentiment!); navigate('/reviews') }}
              />
            )}
            <InfoCell
              label="Urgency"
              value={URGENCY_LABEL[review.urgency] ?? review.urgency}
              valueClass={review.urgency === 'high' ? 'text-amber font-medium' : undefined}
              onClick={() => { setFilter('urgency', review.urgency); navigate('/reviews') }}
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
                  <button
                    key={t}
                    onClick={() => { setFilter('topic', t); navigate('/reviews') }}
                    className="text-xs font-sans bg-gray-50 text-charcoal-light border border-gray-100 px-2 py-0.5 rounded-full hover:bg-green-light hover:text-green hover:border-green/20 transition-all"
                  >
                    {t}
                  </button>
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
          {!draft && !isDisabled && (
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
          {isDisabled && (
            <div role="status" className="rounded-lg border p-4 bg-amber-light border-amber/20">
              <p className="text-sm font-sans font-medium text-charcoal mb-1">
                Reply drafting is temporarily unavailable
              </p>
              <p className="text-sm font-sans text-charcoal-light">
                We have paused AI reply drafting while we improve its accuracy. Your reviews and
                insights are unaffected.
              </p>
            </div>
          )}

          {draftError && !isDisabled && (
            <div className={`rounded-lg border p-4 ${isCapError ? 'bg-amber-light border-amber/20' : 'bg-red-50 border-red-100'}`}>
              <p className="text-sm font-sans font-medium text-charcoal mb-1">
                {isCapError ? 'Drafting is busy' : 'Reply drafting unavailable'}
              </p>
              <p className="text-sm font-sans text-charcoal-light">
                {draftError instanceof ServiceWarmingError
                  ? `The reply service is at capacity — ${describeWait(draftError.retryAfterSeconds)}.`
                  : isCapError
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

function InfoCell({
  label,
  value,
  valueClass,
  onClick,
}: {
  label: string
  value: string
  valueClass?: string
  onClick?: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`bg-gray-50 rounded-lg p-3 text-left w-full ${onClick ? 'hover:bg-green-light/50 hover:ring-1 hover:ring-green/20 transition-all cursor-pointer' : 'cursor-default'}`}
    >
      <p className="text-xs font-sans text-charcoal-light mb-0.5">{label}</p>
      <p className={`text-sm font-sans font-medium text-charcoal ${valueClass ?? ''}`}>{value}</p>
    </button>
  )
}
