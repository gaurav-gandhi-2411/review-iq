import { useState } from 'react'
import { AlertCircle, RefreshCw, ArrowUpRight, CheckCircle2 } from 'lucide-react'
import { ServiceWarmingError, QuotaError, requestQuotaIncrease } from '../lib/api'

interface Props { error: Error; onRetry?: () => void }

export default function ErrorBox({ error, onRetry }: Props) {
  const isWarming = error instanceof ServiceWarmingError
  const isQuota = error instanceof QuotaError
  const [requestState, setRequestState] = useState<'idle' | 'sending' | 'sent'>('idle')

  async function handleRequestMore() {
    setRequestState('sending')
    try {
      await requestQuotaIncrease('Requested from error box')
      setRequestState('sent')
    } catch {
      setRequestState('idle')
    }
  }

  return (
    <div className={`rounded-lg border p-4 flex gap-3 ${
      isWarming ? 'bg-amber-light border-amber/20' : 'bg-red-50 border-red-100'
    }`}>
      <AlertCircle size={18} className={isWarming ? 'text-amber mt-0.5 shrink-0' : 'text-red-500 mt-0.5 shrink-0'} />
      <div className="flex-1">
        <p className="text-sm font-sans text-charcoal font-medium">
          {isWarming ? 'Warming up...' : isQuota ? 'Monthly limit reached' : 'Something went wrong'}
        </p>
        <p className="text-sm font-sans text-charcoal-light mt-0.5">
          {isQuota
            ? 'New uploads are paused until next month. Your existing reviews and insights are still available.'
            : error.message}
        </p>
        <div className="mt-2 flex items-center gap-3">
          {isQuota && requestState !== 'sent' && (
            <button
              onClick={handleRequestMore}
              disabled={requestState === 'sending'}
              className="flex items-center gap-1 text-xs font-sans text-green hover:text-green-muted font-medium transition-colors disabled:opacity-50"
            >
              <ArrowUpRight size={12} />
              {requestState === 'sending' ? 'Sending…' : 'Request higher limit'}
            </button>
          )}
          {isQuota && requestState === 'sent' && (
            <span className="flex items-center gap-1 text-xs font-sans text-green">
              <CheckCircle2 size={12} /> Request sent — we’ll be in touch.
            </span>
          )}
          {onRetry && !isQuota && (
            <button
              onClick={onRetry}
              className="flex items-center gap-1 text-xs font-sans text-green hover:text-green-muted transition-colors"
            >
              <RefreshCw size={12} /> Try again
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
