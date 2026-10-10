// Pure dashboard logic: range scoping, previous-period comparison, concern classification,
// urgent queue and "new since last visit". No React, no storage, no network, so every rule
// here is unit-tested (dashboardModel.test.ts) and the view stays presentational.
//
// Time axis: every window is on `created_at` (when the review was analysed in Samidha), the
// same column the API's since/until params filter on. `review_date` (when the customer wrote
// it) is nullable and absent for most uploads, so it cannot drive recency.
import type { Review } from './api'

export const RANGES = [
  { key: '7d', label: 'Last 7 days', days: 7 },
  { key: '30d', label: 'Last 30 days', days: 30 },
  { key: 'all', label: 'All time', days: null },
] as const
export type RangeKey = (typeof RANGES)[number]['key']

const DAY_MS = 24 * 60 * 60 * 1000

// A trend needs this many reviews in BOTH the current and the previous period. Below it the
// score of a period is mostly noise (one review moves a 5-review score by 14+ points).
export const MIN_REVIEWS_FOR_TREND = 10
// A concern is only called new / rising / easing with at least this many mentions now.
export const MIN_TOPIC_MENTIONS = 3
// Rising: share of reviews mentioning the topic grew by this factor AND mentions grew by at
// least MIN_TOPIC_COUNT_CHANGE. Easing is the mirror image (factor 1/RISING_RATIO).
export const RISING_RATIO = 1.5
export const MIN_TOPIC_COUNT_CHANGE = 2
// Health delta below this many points (0-100 scale) is reported as steady regardless of noise.
export const MIN_SCORE_DELTA = 2
// Two-sided 90% normal critical value for the difference of two period means.
const Z_90 = 1.645

export const URGENT_QUEUE_PREVIEW = 5
export const NEW_LIST_PREVIEW = 5
export const TOP_CONCERNS_LIMIT = 5
// With no known previous visit (first visit, or browser storage unavailable) the
// "new" section falls back to the last 7 days instead of inventing a visit time.
export const NEW_FALLBACK_DAYS = 7

export type Band = 'healthy' | 'needs_attention' | 'at_risk'

export interface Period {
  start: number | null // inclusive; null = unbounded (all time)
  end: number // exclusive
}

export interface Periods {
  current: Period
  previous: Period | null // null for "all": there is no earlier period to compare to
}

export function periodsFor(range: RangeKey, now: number): Periods {
  const spec = RANGES.find(r => r.key === range)!
  if (spec.days === null) return { current: { start: null, end: now + 1 }, previous: null }
  const len = spec.days * DAY_MS
  return {
    current: { start: now - len, end: now + 1 },
    previous: { start: now - 2 * len, end: now - len },
  }
}

export function reviewTime(r: Pick<Review, 'created_at'>): number | null {
  const t = Date.parse(r.created_at)
  return Number.isNaN(t) ? null : t
}

export function inPeriod(reviews: Review[], p: Period): Review[] {
  return reviews.filter(r => {
    const t = reviewTime(r)
    // An unparseable timestamp is only countable when the period is unbounded.
    if (t === null) return p.start === null
    return (p.start === null || t >= p.start) && t < p.end
  })
}

// ---- Health ----

export interface Health {
  n: number
  score: number | null // rounded 0-100; null when n === 0
  band: Band | null
  positive: number
  high: number
  /** Sample variance of the per-review contribution (used for the delta noise estimate). */
  variance: number
}

// Formula 2.0 (app/api/v2/insights.py): 5/7 * S + 2/7 * U. Equivalent to the mean of a
// per-review value v = 5/7*[positive] + 2/7*[urgency != high], which is what lets us put a
// standard error on the difference between two periods.
const W_S = 5 / 7
const W_U = 2 / 7
// Band thresholds are the backend's (do not change): >= 0.75 healthy, >= 0.50 needs attention.
const BAND_HEALTHY = 0.75
const BAND_NEEDS_ATTENTION = 0.5

/**
 * Band from the integer counts, EXACTLY. score = (5*positive + 2*(n - high)) / (7*n), so
 * score >= 3/4  <=>  4*(5*positive + 2*(n - high)) >= 21*n   and
 * score >= 1/2  <=>  2*(5*positive + 2*(n - high)) >= 7*n.
 * No rounding and no floating point before the comparison: rounding first (the old 4 dp / the
 * older integer) promoted a raw 0.74995 to 0.75 and showed "healthy" while the backend (which
 * uses exact fractions, app/api/v2/insights.py health_band) says needs_attention.
 */
export function bandForCounts(positive: number, high: number, n: number): Band {
  const numerator = 5 * positive + 2 * (n - high)
  if (4 * numerator >= 21 * n) return 'healthy'
  if (2 * numerator >= 7 * n) return 'needs_attention'
  return 'at_risk'
}

/** Band for a raw float score, for callers that only have the float. Never rounds; prefer
 * bandForCounts, which is exact (a float can sit one ulp off a threshold). */
export function bandFor(rawScore: number): Band {
  if (rawScore >= BAND_HEALTHY) return 'healthy'
  if (rawScore >= BAND_NEEDS_ATTENTION) return 'needs_attention'
  return 'at_risk'
}

export function computeHealth(reviews: Review[]): Health {
  const n = reviews.length
  if (n === 0) return { n, score: null, band: null, positive: 0, high: 0, variance: 0 }
  let positive = 0
  let high = 0
  const values: number[] = []
  for (const r of reviews) {
    const isPos = r.sentiment === 'positive'
    const isHigh = r.urgency === 'high'
    if (isPos) positive++
    if (isHigh) high++
    values.push(W_S * (isPos ? 1 : 0) + W_U * (isHigh ? 0 : 1))
  }
  const raw = (W_S * positive) / n + (W_U * (n - high)) / n
  const mean = values.reduce((a, b) => a + b, 0) / n
  const variance = n > 1 ? values.reduce((a, v) => a + (v - mean) ** 2, 0) / (n - 1) : 0
  return {
    n,
    score: Math.round(raw * 100),
    band: bandForCounts(positive, high, n),
    positive,
    high,
    variance,
  }
}

export type HealthTrend =
  | { kind: 'no-baseline' } // "all time": no previous period exists
  | { kind: 'insufficient'; currentN: number; previousN: number; min: number }
  | { kind: 'up' | 'down' | 'steady'; delta: number; previousScore: number }

export function compareHealth(current: Health, previous: Health | null): HealthTrend {
  if (previous === null) return { kind: 'no-baseline' }
  if (
    current.n < MIN_REVIEWS_FOR_TREND ||
    previous.n < MIN_REVIEWS_FOR_TREND ||
    current.score === null ||
    previous.score === null
  ) {
    return { kind: 'insufficient', currentN: current.n, previousN: previous.n, min: MIN_REVIEWS_FOR_TREND }
  }
  const delta = current.score - previous.score
  const se = Math.sqrt(current.variance / current.n + previous.variance / previous.n) * 100
  const significant = Math.abs(delta) >= MIN_SCORE_DELTA && Math.abs(delta) > Z_90 * se
  const kind = !significant ? 'steady' : delta > 0 ? 'up' : 'down'
  return { kind, delta, previousScore: previous.score }
}

// ---- Concerns ----

export type ConcernTrend = 'new' | 'rising' | 'easing' | 'steady' | 'unclear' | 'no-baseline'

export interface Concern {
  topic: string
  label: string
  count: number
  previousCount: number | null
  trend: ConcernTrend
}

export function topicLabel(topic: string): string {
  return topic.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function topicCounts(reviews: Review[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const r of reviews) {
    for (const t of new Set(r.topics ?? [])) counts.set(t, (counts.get(t) ?? 0) + 1)
  }
  return counts
}

export function classifyConcern(
  count: number,
  total: number,
  previousCount: number,
  previousTotal: number | null,
): ConcernTrend {
  if (previousTotal === null) return 'no-baseline'
  if (previousTotal < MIN_REVIEWS_FOR_TREND || total < MIN_REVIEWS_FOR_TREND) return 'unclear'
  if (count < MIN_TOPIC_MENTIONS) return 'unclear'
  if (previousCount === 0) return 'new'
  const ratio = count / total / (previousCount / previousTotal)
  if (ratio >= RISING_RATIO && count - previousCount >= MIN_TOPIC_COUNT_CHANGE) return 'rising'
  if (ratio <= 1 / RISING_RATIO && previousCount - count >= MIN_TOPIC_COUNT_CHANGE) return 'easing'
  return 'steady'
}

export function topConcerns(
  current: Review[],
  previous: Review[] | null,
  limit = TOP_CONCERNS_LIMIT,
): Concern[] {
  const cur = topicCounts(current)
  const prev = previous ? topicCounts(previous) : null
  return [...cur.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([topic, count]) => {
      const previousCount = prev ? (prev.get(topic) ?? 0) : null
      return {
        topic,
        label: topicLabel(topic),
        count,
        previousCount,
        trend: classifyConcern(count, current.length, previousCount ?? 0, previous ? previous.length : null),
      }
    })
}

// ---- Urgent queue / new arrivals ----

/** "Needs a human" = urgency is high. There is no resolved/handled state in the data, so a
 * high-urgency review stays in the queue for as long as it is inside the selected range. */
export function needsHuman(r: Review): boolean {
  return r.urgency === 'high'
}

export function newestFirst(reviews: Review[]): Review[] {
  return [...reviews].sort((a, b) => {
    const ta = reviewTime(a) ?? -Infinity
    const tb = reviewTime(b) ?? -Infinity
    return tb - ta || b.id - a.id
  })
}

export function urgentQueue(reviewsInRange: Review[]): Review[] {
  return newestFirst(reviewsInRange.filter(needsHuman))
}

export interface NewSince {
  mode: 'since-visit' | 'fallback'
  /** The instant the window starts (previous session's last-seen time, or now - 7d). */
  since: number
  reviews: Review[] // newest first
  urgent: number
  negative: number
}

export function newSince(reviews: Review[], previousVisit: number | null, now: number): NewSince {
  const mode = previousVisit === null ? 'fallback' : 'since-visit'
  const since = previousVisit ?? now - NEW_FALLBACK_DAYS * DAY_MS
  const fresh = newestFirst(
    reviews.filter(r => {
      const t = reviewTime(r)
      return t !== null && t > since && t <= now
    }),
  )
  return {
    mode,
    since,
    reviews: fresh,
    urgent: fresh.filter(needsHuman).length,
    negative: fresh.filter(r => r.sentiment === 'negative').length,
  }
}

// ---- Whole-page model ----

export interface DashboardModel {
  range: RangeKey
  total: number // reviews in the selected range
  allTotal: number // reviews ever, regardless of range
  health: Health
  trend: HealthTrend
  concerns: Concern[]
  urgent: Review[]
  fresh: NewSince
  positive: number
  negative: number
}

export function buildDashboardModel(
  reviews: Review[],
  range: RangeKey,
  now: number,
  previousVisit: number | null,
): DashboardModel {
  const periods = periodsFor(range, now)
  const cur = inPeriod(reviews, periods.current)
  const prev = periods.previous ? inPeriod(reviews, periods.previous) : null
  const health = computeHealth(cur)
  return {
    range,
    total: cur.length,
    allTotal: reviews.length,
    health,
    trend: compareHealth(health, prev ? computeHealth(prev) : null),
    concerns: topConcerns(cur, prev),
    urgent: urgentQueue(cur),
    fresh: newSince(reviews, previousVisit, now),
    positive: health.positive,
    negative: cur.filter(r => r.sentiment === 'negative').length,
  }
}

// ---- Formatting ----

export function excerpt(text: string, max = 220): string {
  const t = text.replace(/\s+/g, ' ').trim()
  if (t.length <= max) return t
  const cut = t.slice(0, max)
  const lastSpace = cut.lastIndexOf(' ')
  return (lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut) + '…'
}

export function formatArrival(ts: number | null, now: number): string {
  if (ts === null) return 'date unknown'
  const diff = now - ts
  if (diff < 0) return 'just now'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`
  return new Date(ts).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatVisit(ts: number): string {
  return new Date(ts).toLocaleString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}
