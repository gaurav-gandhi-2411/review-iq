// Deterministic fixture reviews for the dashboard preview (dev only) and for tests.
// Seeded LCG, fixed clock: the same arguments always produce the same reviews.
import type { Review } from '../lib/api'

export const FIXTURE_NOW = Date.parse('2026-10-05T09:30:00+05:30') // a Monday morning
const DAY = 24 * 60 * 60 * 1000
const HOUR = 60 * 60 * 1000

const PRODUCTS = ['Linen Kurta', 'Cotton Bedsheet Set', 'Steel Water Bottle', 'Block-print Dupatta']
const TOPIC_POOL = ['delivery_delay', 'sizing', 'fabric_quality', 'packaging', 'customer_service', 'price']

const URGENT_TEXTS = [
  'Ordered this as a gift three weeks ago and it still has not arrived. Support keeps sending the same copy-paste reply. I want a refund today or I will dispute the charge with my bank.',
  'The dye ran in the first wash and ruined two other items in the load. This is not acceptable for the price. Please tell me how you plan to make this right.',
  'Received a completely different product from what I ordered, and the return portal rejects my request. I have now written four times with no response.',
  'The bottle leaks inside my bag and damaged my laptop. I need someone to contact me about compensation.',
]
const NORMAL_TEXTS = [
  'Lovely fabric and the fit is true to size. Would buy again.',
  'Good value for money. Packaging could be better but the product itself is fine.',
  'Delivery took longer than promised, but the kurta is beautiful and the stitching is neat.',
  'Colour is slightly different from the photos. Still happy with it overall.',
  'Excellent quality, my mother loved it. Fast delivery too.',
  'Average. The material is thinner than I expected for this price.',
  'Size runs small, ordered a large and it fit like a medium.',
  'Customer service sorted out my exchange quickly. Thank you.',
]

function lcg(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

export interface FixtureSpec {
  seed?: number
  count: number
  /** Reviews are spread over the past `spanDays` days (older first-fill is allowed via oldestDaysAgo). */
  spanDays: number
  urgentShare: number
  positiveShare: number
  /** Extra urgent reviews injected inside the last 7 days. */
  recentUrgent?: number
  /** Topics boosted in the most recent 30 days (makes a rising trend). */
  risingTopic?: string
  startDaysAgo?: number
  /** Reviews placed after FIXTURE_PREVIOUS_VISIT (the "new since last visit" set), of which urgentNew are urgent. */
  newSinceVisit?: number
  urgentNew?: number
  /**
   * A CSV upload with no customer dates: `count` reviews all analysed inside `spanMs`, the last
   * one `endedAgoMs` before `now`. Of these, `urgent` are high urgency. They have no review_date.
   */
  bulkImport?: { count: number; urgent: number; endedAgoMs: number; spanMs: number }
}

function hex(n: number): string {
  return n.toString(16).padStart(8, '0')
}

export function makeReviews(spec: FixtureSpec, now = FIXTURE_NOW): Review[] {
  const rnd = lcg(spec.seed ?? 42)
  const out: Review[] = []
  let id = 1
  const push = (ageMs: number, urgent: boolean) => {
    const r1 = rnd()
    const sentiment: Review['sentiment'] = urgent
      ? 'negative'
      : r1 < spec.positiveShare
        ? 'positive'
        : r1 < spec.positiveShare + 0.15
          ? 'neutral'
          : 'negative'
    const topics = new Set<string>()
    topics.add(TOPIC_POOL[Math.floor(rnd() * TOPIC_POOL.length)])
    if (rnd() < 0.3) topics.add(TOPIC_POOL[Math.floor(rnd() * TOPIC_POOL.length)])
    if (spec.risingTopic && ageMs < 30 * DAY && rnd() < 0.35) topics.add(spec.risingTopic)
    const text = urgent
      ? URGENT_TEXTS[id % URGENT_TEXTS.length]
      : NORMAL_TEXTS[Math.floor(rnd() * NORMAL_TEXTS.length)]
    out.push({
      id,
      input_hash: `sha256:${hex(id * 2654435761)}${hex(id * 40503)}`,
      review_text: text,
      product: PRODUCTS[Math.floor(rnd() * PRODUCTS.length)],
      stars: null,
      stars_inferred: urgent ? 1 : sentiment === 'positive' ? 5 : 3,
      sentiment,
      urgency: urgent ? 'high' : rnd() < 0.15 ? 'medium' : 'low',
      language: 'en',
      pros: [],
      cons: [],
      topics: [...topics],
      competitor_mentions: [],
      feature_requests: [],
      buy_again: null,
      review_length_chars: text.length,
      confidence: 0.9,
      created_at: new Date(now - ageMs).toISOString(),
    })
    id++
  }
  const start = (spec.startDaysAgo ?? 0) * DAY
  for (let i = 0; i < spec.count; i++) {
    const ageMs = start + rnd() * spec.spanDays * DAY
    push(ageMs, rnd() < spec.urgentShare)
  }
  for (let i = 0; i < (spec.recentUrgent ?? 0); i++) {
    push(rnd() * 7 * DAY, true)
  }
  const sinceVisitMs = now - FIXTURE_PREVIOUS_VISIT
  for (let i = 0; i < (spec.newSinceVisit ?? 0); i++) {
    push(rnd() * sinceVisitMs * 0.95, i < (spec.urgentNew ?? 0))
  }
  if (spec.bulkImport) {
    const b = spec.bulkImport
    for (let i = 0; i < b.count; i++) {
      push(b.endedAgoMs + (b.spanMs * (b.count - 1 - i)) / Math.max(1, b.count - 1), i < b.urgent)
    }
  }
  return out.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
}

export const FIXTURES = {
  normal: () =>
    makeReviews({ count: 110, spanDays: 60, urgentShare: 0.05, positiveShare: 0.6, recentUrgent: 3, newSinceVisit: 7, urgentNew: 2, risingTopic: 'delivery_delay' }),
  'urgent-heavy': () =>
    makeReviews({ count: 70, spanDays: 45, urgentShare: 0.08, positiveShare: 0.4, recentUrgent: 14, newSinceVisit: 12, urgentNew: 6, risingTopic: 'fabric_quality' }),
  // A 140-review undated CSV analysed over 6 minutes, 3 hours ago, on top of a normal account:
  // without grouping it would read as 140 reviews that "arrived" and bury the live ones.
  'bulk-import': () =>
    makeReviews({
      count: 60, spanDays: 45, urgentShare: 0.04, positiveShare: 0.6, newSinceVisit: 4, urgentNew: 1,
      bulkImport: { count: 140, urgent: 4, endedAgoMs: 3 * HOUR, spanMs: 6 * 60 * 1000 },
    }),
  'range-empty': () =>
    makeReviews({ count: 30, spanDays: 40, urgentShare: 0.1, positiveShare: 0.6, startDaysAgo: 75 }),
  empty: () => [] as Review[],
} as const
export type FixtureName = keyof typeof FIXTURES

/** Friday 2 Oct 2026, 4:12 pm IST: "the last time they were here" before that Monday. */
export const FIXTURE_PREVIOUS_VISIT = Date.parse('2026-10-02T16:12:00+05:30')
export const FIXTURE_HOUR = HOUR
