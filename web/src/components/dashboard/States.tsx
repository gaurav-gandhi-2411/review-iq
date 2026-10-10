import { RefreshCw, Sparkles, Upload } from 'lucide-react'

export function LoadingState() {
  return (
    <div role="status" aria-busy="true" className="animate-pulse space-y-6">
      <span className="sr-only">Loading your reviews</span>
      <div className="h-64 rounded-lg bg-ink/90" />
      <div className="h-24 rounded-lg bg-cream-deep" />
      <div className="grid gap-8 lg:grid-cols-[5fr_7fr]">
        <div className="h-48 rounded-lg bg-cream-deep" />
        <div className="h-48 rounded-lg bg-cream-deep" />
      </div>
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-ink bg-ember-tint px-5 py-5">
      <h2 className="font-display text-xl">We could not load your reviews</h2>
      <p className="mt-1 text-[0.9375rem]">{message}</p>
      <p className="mt-1 text-sm text-ink-soft">Your reviews are safe. Try again in a moment.</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 inline-flex min-h-[44px] items-center gap-2 rounded-md bg-ink px-4 text-sm font-medium text-cream hover:bg-ink-rule"
        >
          <RefreshCw size={14} aria-hidden="true" /> Try again
        </button>
      )}
    </div>
  )
}

export function NoReviewsState({ onUpload, onTrySample }: { onUpload: () => void; onTrySample: () => void }) {
  return (
    <div className="py-10">
      <h2 className="font-display text-2xl">Nothing to review yet</h2>
      <p className="mt-2 max-w-md text-[0.9375rem] text-ink-soft">
        Upload a CSV of customer reviews. Urgent ones will appear here first, newest at the top.
      </p>
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onUpload}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-saffron px-5 text-sm font-medium text-ink hover:bg-ember"
        >
          <Upload size={15} aria-hidden="true" /> Upload your first reviews
        </button>
        <button
          type="button"
          onClick={onTrySample}
          className="inline-flex min-h-[44px] items-center gap-1.5 text-sm font-medium underline underline-offset-4"
        >
          <Sparkles size={14} aria-hidden="true" /> Try sample data
        </button>
      </div>
    </div>
  )
}

export function RangeEmptyState({ rangeLabel, olderCount }: { rangeLabel: string; olderCount: number }) {
  return (
    <p className="rounded-lg bg-cream-deep px-5 py-4 text-[0.9375rem]">
      No reviews arrived in this period ({rangeLabel.toLowerCase()}). You have {olderCount} older review
      {olderCount === 1 ? '' : 's'}; choose a longer range to see {olderCount === 1 ? 'it' : 'them'}.
    </p>
  )
}
