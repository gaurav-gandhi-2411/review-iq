import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Upload as UploadIcon,
  Loader2,
  ArrowRight,
  Sparkles,
  Frown,
  Meh,
  Smile,
} from 'lucide-react'
import LogoMark from '../components/LogoMark'
import ErrorBox from '../components/ErrorBox'
import { demoExtract, type DemoExtraction } from '../lib/api'
import { parseCsv, detectTextColumn } from '../lib/csv'

const MAX_DEMO_ROWS = 5 // stays within /demo/extract's 5-requests-per-minute cap

type CardState =
  | { status: 'queued'; text: string }
  | { status: 'analyzing'; text: string }
  | { status: 'done'; text: string; result: DemoExtraction }
  | { status: 'error'; text: string; error: Error }

type Phase = 'idle' | 'loading-sample' | 'running' | 'error'

export default function TryPage() {
  const [phase, setPhase] = useState<Phase>('idle')
  const [cards, setCards] = useState<CardState[]>([])
  const [extraRowCount, setExtraRowCount] = useState(0)
  const [isDragOver, setIsDragOver] = useState(false)
  const [pageError, setPageError] = useState<Error | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const runOn = useCallback((texts: string[], extraCount: number) => {
    setExtraRowCount(extraCount)
    setPhase('running')
    setPageError(null)
    setCards(texts.map(text => ({ status: 'queued', text })))

    texts.forEach((text, i) => {
      setCards(prev => prev.map((c, idx) => (idx === i ? { status: 'analyzing', text } : c)))
      demoExtract(text)
        .then(result => {
          setCards(prev => prev.map((c, idx) => (idx === i ? { status: 'done', text, result } : c)))
        })
        .catch(error => {
          setCards(prev =>
            prev.map((c, idx) =>
              idx === i ? { status: 'error', text, error: error instanceof Error ? error : new Error('Failed') } : c
            )
          )
        })
    })
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setPhase('loading-sample')
    setPageError(null)
    try {
      const raw = await file.text()
      const records = parseCsv(raw)
      const col = detectTextColumn(records)
      if (!col) throw new Error("Couldn't find a review-text column in that file.")
      const texts = records.map(r => r[col]).filter(t => t.trim().length > 0)
      if (texts.length === 0) throw new Error('No review text found in that file.')
      runOn(texts.slice(0, MAX_DEMO_ROWS), Math.max(0, texts.length - MAX_DEMO_ROWS))
    } catch (err) {
      setPageError(err instanceof Error ? err : new Error('Could not read that file.'))
      setPhase('error')
    }
  }, [runOn])

  async function useSampleData() {
    setPhase('loading-sample')
    setPageError(null)
    try {
      const res = await fetch('/sample-reviews.csv')
      const raw = await res.text()
      const records = parseCsv(raw)
      const texts = records.map(r => r['review_text']).filter(t => t.trim().length > 0)
      runOn(texts.slice(0, MAX_DEMO_ROWS), Math.max(0, texts.length - MAX_DEMO_ROWS))
    } catch {
      setPageError(new Error('Could not load the sample file — try again in a moment.'))
      setPhase('error')
    }
  }

  function reset() {
    setPhase('idle')
    setCards([])
    setPageError(null)
  }

  const onDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); setIsDragOver(true) }, [])
  const onDragLeave = useCallback(() => setIsDragOver(false), [])
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f) void handleFile(f)
  }, [handleFile])

  const doneCards = cards.filter((c): c is Extract<CardState, { status: 'done' }> => c.status === 'done')
  const allSettled = cards.length > 0 && cards.every(c => c.status === 'done' || c.status === 'error')

  return (
    <div className="min-h-screen bg-cream">
      <header className="bg-white border-b border-gray-100 shadow-sm">
        <div className="max-w-3xl mx-auto px-6 h-14 flex items-center justify-between">
          <button onClick={() => navigate('/')} className="flex items-center gap-2">
            <LogoMark size={24} />
            <span className="font-display text-lg text-charcoal tracking-tight">Samidha Reviews</span>
          </button>
          <button
            onClick={() => navigate('/')}
            className="text-sm font-sans text-charcoal-light hover:text-charcoal transition-colors"
          >
            Sign in
          </button>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10">
        <h1 className="font-display text-2xl text-charcoal mb-1">See it work — no signup</h1>
        <p className="text-sm text-charcoal-light font-sans mb-8 max-w-xl">
          Drop a CSV of reviews (or use ours) and watch raw text turn into structured insight,
          right here. We analyze up to {MAX_DEMO_ROWS} rows for free, on the spot.
        </p>

        {phase === 'idle' && (
          <>
            <div
              onClick={() => fileRef.current?.click()}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              className={`cursor-pointer rounded-xl border-2 border-dashed p-10 text-center transition-all ${
                isDragOver ? 'border-green bg-green-light' : 'border-gray-200 hover:border-green/50 hover:bg-green-light/30'
              }`}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={e => { const f = e.target.files?.[0]; if (f) void handleFile(f) }}
              />
              <UploadIcon size={32} className="mx-auto text-charcoal-light/50 mb-3" />
              <p className="font-sans font-medium text-charcoal text-sm">Drop your reviews CSV here</p>
              <p className="font-sans text-xs text-charcoal-light mt-1">or click to browse</p>
            </div>

            <div className="mt-4 text-center">
              <button
                onClick={useSampleData}
                className="inline-flex items-center gap-1.5 text-sm font-sans text-green hover:text-green-muted font-medium transition-colors"
              >
                <Sparkles size={14} /> Or try our sample reviews — one click
              </button>
            </div>
          </>
        )}

        {phase === 'loading-sample' && (
          <div className="bg-white rounded-xl border border-gray-100 shadow-card p-8 text-center">
            <Loader2 size={28} className="animate-spin text-green mx-auto mb-3" />
            <p className="text-sm text-charcoal-light font-sans">Reading your file...</p>
          </div>
        )}

        {phase === 'error' && pageError && (
          <div className="space-y-4">
            <ErrorBox error={pageError} onRetry={reset} />
          </div>
        )}

        {phase === 'running' && (
          <div className="space-y-6">
            <div className="space-y-3">
              {cards.map((card, i) => (
                <DemoCard key={i} card={card} />
              ))}
            </div>

            {extraRowCount > 0 && (
              <p className="text-xs font-sans text-charcoal-light text-center">
                +{extraRowCount} more row{extraRowCount !== 1 ? 's' : ''} in your file — sign up to analyze all of them.
              </p>
            )}

            {allSettled && doneCards.length > 0 && <SummaryStrip cards={doneCards} />}

            {allSettled && (
              <div className="bg-charcoal rounded-xl p-6 text-center">
                <p className="font-display text-lg text-white mb-1">Like what you see?</p>
                <p className="text-sm font-sans text-white/70 mb-4">
                  Sign up free to analyze your full file — up to 500 reviews, saved to your own dashboard.
                </p>
                <button
                  onClick={() => navigate('/')}
                  className="inline-flex items-center gap-2 bg-green hover:bg-green-muted text-white text-sm font-sans font-medium py-2.5 px-6 rounded-lg transition-colors"
                >
                  Get your free access link <ArrowRight size={15} />
                </button>
              </div>
            )}

            {!allSettled && (
              <button
                onClick={reset}
                className="text-xs font-sans text-charcoal-light hover:text-charcoal underline underline-offset-2 transition-colors"
              >
                Start over
              </button>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

function DemoCard({ card }: { card: CardState }) {
  return (
    <div className="bg-white rounded-lg border border-gray-100 shadow-card p-4">
      <p className="font-sans text-sm text-charcoal-light italic line-clamp-2 mb-3">"{card.text}"</p>

      {card.status === 'queued' && (
        <p className="text-xs font-sans text-charcoal-light/60">Waiting...</p>
      )}

      {card.status === 'analyzing' && (
        <div className="flex items-center gap-2 text-xs font-sans text-green">
          <Loader2 size={12} className="animate-spin" /> Analyzing...
        </div>
      )}

      {card.status === 'error' && (
        <p className="text-xs font-sans text-amber">{card.error.message}</p>
      )}

      {card.status === 'done' && (
        <div className="flex flex-wrap items-center gap-2">
          <SentimentBadge sentiment={card.result.sentiment} />
          <UrgencyBadge urgency={card.result.urgency} />
          {card.result.topics.slice(0, 3).map(t => (
            <span key={t} className="text-xs font-sans px-2 py-0.5 rounded-full bg-gray-50 text-charcoal-light">
              {t.replace(/_/g, ' ')}
            </span>
          ))}
          {card.result.competitor_mentions.map(c => (
            <span key={c} className="text-xs font-sans px-2 py-0.5 rounded-full bg-amber-light text-amber">
              mentions {c}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SentimentBadge({ sentiment }: { sentiment: DemoExtraction['sentiment'] }) {
  const config = {
    positive: { icon: <Smile size={12} />, cls: 'bg-green-light text-green' },
    negative: { icon: <Frown size={12} />, cls: 'bg-amber-light text-amber' },
    mixed: { icon: <Meh size={12} />, cls: 'bg-yellow-50 text-yellow-700' },
    neutral: { icon: <Meh size={12} />, cls: 'bg-gray-50 text-charcoal-light' },
  }[sentiment]
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-sans font-medium px-2 py-0.5 rounded-full ${config.cls}`}>
      {config.icon} {sentiment}
    </span>
  )
}

function UrgencyBadge({ urgency }: { urgency: DemoExtraction['urgency'] }) {
  if (urgency === 'low') return null
  const cls = urgency === 'high' ? 'bg-amber text-white' : 'bg-amber-light text-amber'
  return (
    <span className={`text-xs font-sans font-medium px-2 py-0.5 rounded-full ${cls}`}>
      {urgency} urgency
    </span>
  )
}

function SummaryStrip({ cards }: { cards: Extract<CardState, { status: 'done' }>[] }) {
  const total = cards.length
  const bySentiment = { positive: 0, negative: 0, mixed: 0, neutral: 0 }
  for (const c of cards) bySentiment[c.result.sentiment]++

  const topicCounts = new Map<string, number>()
  for (const c of cards) for (const t of c.result.topics) topicCounts.set(t, (topicCounts.get(t) ?? 0) + 1)
  const topTopics = [...topicCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4)

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-card p-5">
      <p className="text-xs font-sans font-medium text-charcoal-light uppercase tracking-wide mb-3">
        What we found in {total} review{total !== 1 ? 's' : ''}
      </p>
      <div className="flex h-2 rounded-full overflow-hidden mb-2">
        {bySentiment.positive > 0 && <div className="bg-green" style={{ width: `${(bySentiment.positive / total) * 100}%` }} />}
        {bySentiment.mixed > 0 && <div className="bg-yellow-400" style={{ width: `${(bySentiment.mixed / total) * 100}%` }} />}
        {bySentiment.neutral > 0 && <div className="bg-gray-300" style={{ width: `${(bySentiment.neutral / total) * 100}%` }} />}
        {bySentiment.negative > 0 && <div className="bg-amber" style={{ width: `${(bySentiment.negative / total) * 100}%` }} />}
      </div>
      <p className="text-xs font-sans text-charcoal-light mb-4">
        {bySentiment.positive} positive · {bySentiment.mixed} mixed · {bySentiment.negative} negative
      </p>
      {topTopics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {topTopics.map(([topic, count]) => (
            <span key={topic} className="text-xs font-sans px-2.5 py-1 rounded-full bg-green-light text-green">
              {topic.replace(/_/g, ' ')} × {count}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
