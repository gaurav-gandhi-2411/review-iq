import { AlertCircle, RefreshCw } from 'lucide-react'
import { ServiceWarmingError, QuotaError } from '../lib/api'

interface Props { error: Error; onRetry?: () => void }

export default function ErrorBox({ error, onRetry }: Props) {
  const isWarming = error instanceof ServiceWarmingError
  const isQuota = error instanceof QuotaError

  return (
    <div className={`rounded-lg border p-4 flex gap-3 ${
      isWarming ? 'bg-amber-light border-amber/20' : 'bg-red-50 border-red-100'
    }`}>
      <AlertCircle size={18} className={isWarming ? 'text-amber mt-0.5 shrink-0' : 'text-red-500 mt-0.5 shrink-0'} />
      <div className="flex-1">
        <p className="text-sm font-sans text-charcoal font-medium">
          {isWarming ? 'Warming up...' : isQuota ? 'Limit reached' : 'Something went wrong'}
        </p>
        <p className="text-sm font-sans text-charcoal-light mt-0.5">{error.message}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-2 flex items-center gap-1 text-xs font-sans text-green hover:text-green-muted transition-colors"
          >
            <RefreshCw size={12} /> Try again
          </button>
        )}
      </div>
    </div>
  )
}
