import { BffError, QuotaError, ServiceWarmingError, type ClassifiedReview } from './api'

/** Build-time flag: only the exact string 'true' enables the view; unset/anything else is OFF. */
export function skippedReviewsEnabled(value: unknown = import.meta.env.VITE_ENABLE_SKIPPED_REVIEWS): boolean {
  return value === 'true'
}

/** Per-row message for a failed open. 429 surfaces as QuotaError, 502/503 as ServiceWarmingError. */
export function openErrorMessage(err: unknown): string {
  if (err instanceof BffError && err.status === 410) return "This review's data has been purged."
  if (err instanceof QuotaError) return 'Monthly review limit reached. Opening a review counts against it.'
  if (err instanceof ServiceWarmingError) return err.message
  return 'Could not analyse this review. Try again.'
}

/** "sentiment: positive" chips; labels the encoder left null are dropped. */
export function encoderChips(encoder: ClassifiedReview['encoder']): string[] {
  return Object.entries(encoder)
    .filter(([, label]) => label)
    .map(([task, label]) => `${task.replace(/_/g, ' ')}: ${label}`)
}

const DATE = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' })

export function formatCreated(iso: string | null): string | null {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : DATE.format(t)
}
