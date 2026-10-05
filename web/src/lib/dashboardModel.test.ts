import { describe, expect, it } from 'vitest'
import type { Review } from './api'
import {
  MIN_REVIEWS_FOR_TREND,
  bandFor,
  buildDashboardModel,
  classifyConcern,
  compareHealth,
  computeHealth,
  excerpt,
  formatArrival,
  inPeriod,
  newSince,
  periodsFor,
  topConcerns,
  urgentQueue,
} from './dashboardModel'

const NOW = Date.parse('2026-10-05T09:30:00Z')
const DAY = 24 * 60 * 60 * 1000

let nextId = 1
function rev(over: Partial<Review> & { ageDays?: number } = {}): Review {
  const { ageDays = 0, ...rest } = over
  const id = nextId++
  return {
    id,
    input_hash: `sha256:${id}`,
    review_text: 'text',
    product: 'P',
    stars: null,
    stars_inferred: null,
    sentiment: 'neutral',
    urgency: 'low',
    language: 'en',
    pros: [],
    cons: [],
    topics: [],
    competitor_mentions: [],
    feature_requests: [],
    buy_again: null,
    review_length_chars: 4,
    confidence: 1,
    created_at: new Date(NOW - ageDays * DAY).toISOString(),
    ...rest,
  }
}
function many(n: number, over: Partial<Review> & { ageDays?: number } = {}): Review[] {
  return Array.from({ length: n }, () => rev(over))
}

describe('range scoping', () => {
  const reviews = [rev({ ageDays: 1 }), rev({ ageDays: 6.9 }), rev({ ageDays: 7.1 }), rev({ ageDays: 20 }), rev({ ageDays: 40 })]

  it('7d keeps only reviews from the last 7 days', () => {
    expect(inPeriod(reviews, periodsFor('7d', NOW).current)).toHaveLength(2)
  })
  it('30d keeps 4 and all keeps everything', () => {
    expect(inPeriod(reviews, periodsFor('30d', NOW).current)).toHaveLength(4)
    expect(inPeriod(reviews, periodsFor('all', NOW).current)).toHaveLength(5)
  })
  it('the previous period is the equal-length window immediately before, and "all" has none', () => {
    const p = periodsFor('7d', NOW)
    expect(p.previous).toEqual({ start: NOW - 14 * DAY, end: NOW - 7 * DAY })
    expect(inPeriod(reviews, p.previous!)).toHaveLength(1) // only the 7.1d-old review
    expect(periodsFor('all', NOW).previous).toBeNull()
  })
  it('a review exactly on the boundary belongs to exactly one of current/previous', () => {
    const edge = rev({ ageDays: 7 })
    const p = periodsFor('7d', NOW)
    expect(inPeriod([edge], p.current)).toHaveLength(1)
    expect(inPeriod([edge], p.previous!)).toHaveLength(0)
  })
  it('an unparseable timestamp only counts in the unbounded range', () => {
    const bad = rev({ created_at: 'not a date' })
    expect(inPeriod([bad], periodsFor('7d', NOW).current)).toHaveLength(0)
    expect(inPeriod([bad], periodsFor('all', NOW).current)).toHaveLength(1)
  })
})

describe('health score (formula 2.0)', () => {
  it('matches 5/7*S + 2/7*U', () => {
    // 10 reviews: 6 positive, 2 high urgency -> S=0.6, U=0.8 -> 0.4286+0.2286=0.6571
    const rs = [
      ...many(6, { sentiment: 'positive' }),
      ...many(2, { sentiment: 'negative', urgency: 'high' }),
      ...many(2, { sentiment: 'neutral' }),
    ]
    const h = computeHealth(rs)
    expect(h.score).toBe(66)
    expect(h.band).toBe('needs_attention')
    expect(h.positive).toBe(6)
    expect(h.high).toBe(2)
  })
  it('has no score for zero reviews', () => {
    expect(computeHealth([])).toMatchObject({ n: 0, score: null, band: null })
  })
  it('band thresholds are unchanged: >=0.75 healthy, >=0.50 needs_attention, else at_risk', () => {
    expect(bandFor(0.75)).toBe('healthy')
    expect(bandFor(0.7499)).toBe('needs_attention')
    expect(bandFor(0.5)).toBe('needs_attention')
    expect(bandFor(0.4999)).toBe('at_risk')
  })
  it('bands from the unrounded score, so a raw 0.746 is not promoted to healthy by rounding', () => {
    // 0.746 rounds to 75 as an integer; the backend would call it needs_attention.
    expect(bandFor(0.746)).toBe('needs_attention')
  })
})

describe('health direction vs the previous period', () => {
  const good = (n: number) => many(n, { sentiment: 'positive' })
  const bad = (n: number) => many(n, { sentiment: 'negative', urgency: 'high' })

  it('returns no-baseline when there is no previous period', () => {
    expect(compareHealth(computeHealth(good(20)), null)).toEqual({ kind: 'no-baseline' })
  })
  it('refuses to invent a trend from too few reviews, in either period', () => {
    const t1 = compareHealth(computeHealth(good(MIN_REVIEWS_FOR_TREND - 1)), computeHealth(bad(30)))
    expect(t1).toMatchObject({ kind: 'insufficient', currentN: MIN_REVIEWS_FOR_TREND - 1, previousN: 30 })
    const t2 = compareHealth(computeHealth(good(30)), computeHealth(bad(2)))
    expect(t2).toMatchObject({ kind: 'insufficient', previousN: 2 })
    expect(compareHealth(computeHealth([]), computeHealth(good(30))).kind).toBe('insufficient')
  })
  it('reports up with the point delta on a clear improvement', () => {
    const t = compareHealth(computeHealth([...good(15), ...bad(5)]), computeHealth([...good(5), ...bad(15)]))
    expect(t.kind).toBe('up')
    if (t.kind === 'up') expect(t.delta).toBeGreaterThan(0)
  })
  it('reports down on a clear decline', () => {
    const t = compareHealth(computeHealth([...good(5), ...bad(15)]), computeHealth([...good(15), ...bad(5)]))
    expect(t.kind).toBe('down')
  })
  it('reports steady when the change is within noise even if the numbers differ', () => {
    // 10 reviews each; one review flips: delta ~ 7 points but well inside 90% noise at n=10.
    const cur = [...good(6), ...many(4, { sentiment: 'neutral' })]
    const prev = [...good(5), ...many(5, { sentiment: 'neutral' })]
    const t = compareHealth(computeHealth(cur), computeHealth(prev))
    expect(t.kind).toBe('steady')
  })
  it('reports steady for an identical score', () => {
    const t = compareHealth(computeHealth(good(12)), computeHealth(good(12)))
    expect(t).toMatchObject({ kind: 'steady', delta: 0 })
  })
})

describe('concern classification', () => {
  // classifyConcern(count, total, previousCount, previousTotal)
  it('no previous period -> no-baseline', () => {
    expect(classifyConcern(8, 40, 0, null)).toBe('no-baseline')
  })
  it('too few reviews in either period -> unclear', () => {
    expect(classifyConcern(5, 40, 1, 9)).toBe('unclear')
    expect(classifyConcern(5, 9, 1, 40)).toBe('unclear')
  })
  it('fewer than 3 mentions now -> unclear', () => {
    expect(classifyConcern(2, 40, 0, 40)).toBe('unclear')
  })
  it('absent before and 3+ now -> new', () => {
    expect(classifyConcern(3, 40, 0, 40)).toBe('new')
  })
  it('share up 1.5x and +2 mentions -> rising', () => {
    expect(classifyConcern(9, 40, 4, 40)).toBe('rising')
  })
  it('share up 1.5x but only +1 mention is not rising (small-count guard)', () => {
    expect(classifyConcern(3, 20, 2, 20)).toBe('steady') // 1.5x but +1
  })
  it('compares SHARE, not raw counts, when the periods differ in size', () => {
    // count doubled (6 vs 3) but the period doubled too: same share -> steady
    expect(classifyConcern(6, 40, 3, 20)).toBe('steady')
  })
  it('share down to 2/3 and -2 mentions -> easing', () => {
    expect(classifyConcern(4, 40, 8, 40)).toBe('easing')
  })
  it('topConcerns counts each review once per topic, sorts by count, and limits', () => {
    const cur = [
      rev({ topics: ['sizing', 'sizing', 'price'] }), // duplicate topic in one review counts once
      ...many(11, { topics: ['sizing'] }),
      ...many(4, { topics: ['price'] }),
      ...many(2, { topics: ['packaging'] }),
    ]
    const prev = [...many(10, { topics: ['sizing'] }), ...many(10, { topics: [] })]
    const c = topConcerns(cur, prev, 2)
    expect(c.map(x => [x.topic, x.count])).toEqual([['sizing', 12], ['price', 5]])
    expect(c[0].label).toBe('Sizing')
    expect(c[1].trend).toBe('new') // price absent before, 5 now, both periods >= 10 reviews
  })
  it('without a previous period every concern is no-baseline and previousCount is null', () => {
    const c = topConcerns(many(12, { topics: ['sizing'] }), null)
    expect(c[0]).toMatchObject({ trend: 'no-baseline', previousCount: null })
  })
})

describe('urgent queue', () => {
  it('contains only high-urgency reviews, newest first, ties broken by id', () => {
    const a = rev({ urgency: 'high', ageDays: 3 })
    const b = rev({ urgency: 'high', ageDays: 1 })
    const c = rev({ urgency: 'medium', ageDays: 0.5 })
    const d = rev({ urgency: 'high', ageDays: 1, created_at: b.created_at })
    const q = urgentQueue([a, c, b, d])
    expect(q.map(r => r.id)).toEqual([d.id, b.id, a.id])
  })
  it('is empty when nothing is urgent', () => {
    expect(urgentQueue(many(5, { urgency: 'low' }))).toEqual([])
  })
})

describe('new since last visit', () => {
  it('returns reviews strictly after the previous visit, newest first, with counts', () => {
    const since = NOW - 3 * DAY
    const old = rev({ ageDays: 4 })
    const n1 = rev({ ageDays: 2, urgency: 'high', sentiment: 'negative' })
    const n2 = rev({ ageDays: 0.1, sentiment: 'negative' })
    const n3 = rev({ ageDays: 1, sentiment: 'positive' })
    const res = newSince([old, n1, n2, n3], since, NOW)
    expect(res.mode).toBe('since-visit')
    expect(res.reviews.map(r => r.id)).toEqual([n2.id, n3.id, n1.id])
    expect(res.urgent).toBe(1)
    expect(res.negative).toBe(2)
  })
  it('a review that arrived exactly at the visit instant is not new', () => {
    const since = NOW - DAY
    expect(newSince([rev({ created_at: new Date(since).toISOString() })], since, NOW).reviews).toHaveLength(0)
  })
  it('falls back to the last 7 days (and says so) with no known previous visit', () => {
    const res = newSince([rev({ ageDays: 2 }), rev({ ageDays: 9 })], null, NOW)
    expect(res.mode).toBe('fallback')
    expect(res.reviews).toHaveLength(1)
  })
  it('is independent of the selected range', () => {
    const reviews = [rev({ ageDays: 2 }), rev({ ageDays: 20 })]
    const visit = NOW - 30 * DAY
    expect(buildDashboardModel(reviews, '7d', NOW, visit).fresh.reviews).toHaveLength(2)
    expect(buildDashboardModel(reviews, 'all', NOW, visit).fresh.reviews).toHaveLength(2)
  })
})

describe('buildDashboardModel', () => {
  it('scopes every metric to the selected range', () => {
    const reviews = [
      ...many(4, { ageDays: 1, sentiment: 'positive', topics: ['sizing'] }),
      rev({ ageDays: 2, urgency: 'high', sentiment: 'negative' }),
      ...many(10, { ageDays: 20, sentiment: 'negative', topics: ['price'] }),
    ]
    const m7 = buildDashboardModel(reviews, '7d', NOW, null)
    expect(m7.total).toBe(5)
    expect(m7.allTotal).toBe(15)
    expect(m7.urgent).toHaveLength(1)
    expect(m7.concerns.map(c => c.topic)).toEqual(['sizing'])
    const mAll = buildDashboardModel(reviews, 'all', NOW, null)
    expect(mAll.total).toBe(15)
    expect(mAll.concerns.map(c => c.topic)).toEqual(['price', 'sizing'])
    expect(mAll.trend.kind).toBe('no-baseline')
  })
})

describe('formatting', () => {
  it('excerpt collapses whitespace and truncates on a word boundary', () => {
    expect(excerpt('  a   b\n c ')).toBe('a b c')
    const long = 'word '.repeat(100)
    const e = excerpt(long, 50)
    expect(e.length).toBeLessThanOrEqual(51)
    expect(e.endsWith('…')).toBe(true)
  })
  it('formatArrival gives relative text, then a date', () => {
    expect(formatArrival(NOW - 5 * 60000, NOW)).toBe('5 min ago')
    expect(formatArrival(NOW - 3 * 3600000, NOW)).toBe('3 h ago')
    expect(formatArrival(NOW - DAY, NOW)).toBe('1 day ago')
    expect(formatArrival(null, NOW)).toBe('date unknown')
    expect(formatArrival(NOW - 30 * DAY, NOW)).toMatch(/2026/)
  })
})
