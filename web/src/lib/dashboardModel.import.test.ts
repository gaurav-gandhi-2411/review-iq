import { describe, expect, it } from 'vitest'
import type { Review } from './api'
import {
  IMPORT_MIN_REVIEWS,
  IMPORT_WINDOW_MS,
  REVIEW_DATE_FUTURE_TOLERANCE_MS,
  buildDashboardModel,
  detectImportEvents,
  inPeriod,
  newSince,
  periodsFor,
  recencyOf,
  reviewTime,
  urgentQueue,
} from './dashboardModel'

// S19: recency is review_date when usable, else created_at; 20+ undated reviews inside one
// 10-minute window are ONE import event. Pure functions only (no React).

const NOW = Date.parse('2026-10-05T09:30:00Z')
const MIN = 60 * 1000
const HOUR = 60 * MIN
const DAY = 24 * HOUR
const T0 = Date.parse('2026-10-05T04:00:00Z') // an import's first review

let nextId = 1
function rev(over: Partial<Review> = {}): Review {
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
    created_at: new Date(T0).toISOString(),
    ...over,
  }
}
/** n undated reviews analysed evenly from `start` to `start + spanMs` (inclusive). */
function burst(n: number, start: number, spanMs: number, over: Partial<Review> = {}): Review[] {
  return Array.from({ length: n }, (_, i) =>
    rev({ created_at: new Date(start + (n === 1 ? 0 : (spanMs * i) / (n - 1))).toISOString(), ...over }),
  )
}
const iso = (t: number) => new Date(t).toISOString()

describe('recency: review_date when usable, else created_at', () => {
  it('uses review_date when present', () => {
    const r = rev({ created_at: iso(NOW), review_date: iso(NOW - 40 * DAY) })
    expect(recencyOf(r)).toEqual({ time: NOW - 40 * DAY, source: 'review_date' })
    expect(reviewTime(r)).toBe(NOW - 40 * DAY)
  })
  it('falls back to created_at for null, undefined, empty and unparseable review_date', () => {
    for (const review_date of [null, undefined, '', 'not a date']) {
      const r = rev({ created_at: iso(NOW), review_date })
      expect(recencyOf(r)).toEqual({ time: NOW, source: 'created_at' })
    }
  })
  it('a review_date older than created_at wins (an old review uploaded today is old)', () => {
    const r = rev({ created_at: iso(NOW), review_date: iso(NOW - 400 * DAY) })
    expect(reviewTime(r)).toBe(NOW - 400 * DAY)
  })
  it('a review_date later than created_at by more than a day is a data error: created_at is used', () => {
    const bad = rev({ created_at: iso(NOW), review_date: iso(NOW + REVIEW_DATE_FUTURE_TOLERANCE_MS + 1) })
    expect(recencyOf(bad).source).toBe('created_at')
    const edge = rev({ created_at: iso(NOW), review_date: iso(NOW + REVIEW_DATE_FUTURE_TOLERANCE_MS) })
    expect(recencyOf(edge).source).toBe('review_date') // exactly one day of slack is accepted
  })
  it('drives the 7d window and the previous-period window', () => {
    const periods = periodsFor('7d', NOW)
    const wroteLongAgo = rev({ created_at: iso(NOW - 1 * HOUR), review_date: iso(NOW - 30 * DAY) })
    const wroteThisWeek = rev({ created_at: iso(NOW - 1 * HOUR), review_date: iso(NOW - 2 * DAY) })
    const previousWeek = rev({ created_at: iso(NOW - 1 * HOUR), review_date: iso(NOW - 10 * DAY) })
    const all = [wroteLongAgo, wroteThisWeek, previousWeek]
    expect(inPeriod(all, periods.current).map(r => r.id)).toEqual([wroteThisWeek.id])
    expect(inPeriod(all, periods.previous!).map(r => r.id)).toEqual([previousWeek.id])
  })
  it('orders the urgent queue by review_date, undated by created_at', () => {
    const dated = rev({ urgency: 'high', created_at: iso(NOW), review_date: iso(NOW - 5 * DAY) })
    const undatedNewer = rev({ urgency: 'high', created_at: iso(NOW - 1 * DAY) })
    const undatedOlder = rev({ urgency: 'high', created_at: iso(NOW - 8 * DAY) })
    expect(urgentQueue([undatedOlder, dated, undatedNewer]).map(r => r.id)).toEqual([
      undatedNewer.id,
      dated.id,
      undatedOlder.id,
    ])
  })
  it('"new since last visit" follows review_date: an old review analysed after the visit is not new', () => {
    const visit = NOW - 3 * DAY
    const analysedAfterVisitButWrittenLongAgo = rev({ created_at: iso(NOW - 1 * DAY), review_date: iso(NOW - 90 * DAY) })
    const writtenAfterVisit = rev({ created_at: iso(NOW - 1 * HOUR), review_date: iso(NOW - 1 * DAY) })
    const res = newSince([analysedAfterVisitButWrittenLongAgo, writtenAfterVisit], visit, NOW)
    expect(res.reviews.map(r => r.id)).toEqual([writtenAfterVisit.id])
  })
})

describe('import events: 20+ undated reviews within one 10-minute window', () => {
  it('constants are what the header documents', () => {
    expect(IMPORT_MIN_REVIEWS).toBe(20)
    expect(IMPORT_WINDOW_MS).toBe(10 * MIN)
  })

  it('19 reviews are not an import; 20 are', () => {
    expect(detectImportEvents(burst(19, T0, 5 * MIN))).toEqual([])
    const events = detectImportEvents(burst(20, T0, 5 * MIN))
    expect(events).toHaveLength(1)
    expect(events[0].reviews).toHaveLength(20)
  })

  it('window edge: first-to-last of exactly 10:00 groups, 10:00.001 does not (20 reviews)', () => {
    expect(detectImportEvents(burst(20, T0, 10 * MIN))).toHaveLength(1)
    expect(detectImportEvents(burst(20, T0, 10 * MIN + 1))).toEqual([])
  })

  it('minute-granularity edge: 10:00 to 10:10 groups, 10:00 to 10:11 does not', () => {
    const at = (m: number) => Date.parse('2026-10-05T10:00:00Z') + m * MIN
    const ten = [...burst(19, at(0), 0), ...burst(1, at(10), 0)]
    const eleven = [...burst(19, at(0), 0), ...burst(1, at(11), 0)]
    expect(detectImportEvents(ten)).toHaveLength(1)
    expect(detectImportEvents(eleven)).toEqual([])
  })

  it('a 30-review import straddling a 10-minute edge is ONE event of 30, not 20 + 10', () => {
    // 30 reviews, one per 30 s: 14.5 minutes end to end. Every 10-minute window holds 21-ish.
    const rs = burst(30, T0, 29 * 30 * 1000)
    const events = detectImportEvents(rs)
    expect(events).toHaveLength(1)
    expect(events[0].reviews).toHaveLength(30)
    expect(events[0].end - events[0].start).toBe(29 * 30 * 1000)
  })

  it('two bursts separated by more than the window are two events', () => {
    const a = burst(25, T0, 2 * MIN)
    const b = burst(22, T0 + 2 * HOUR, 2 * MIN)
    const events = detectImportEvents([...b, ...a])
    expect(events.map(e => e.reviews.length)).toEqual([25, 22])
  })

  it('reviews with a usable review_date are never grouped; mixed dated and undated', () => {
    const dated = burst(30, T0, 2 * MIN, { review_date: iso(NOW - 60 * DAY) })
    expect(detectImportEvents(dated)).toEqual([])
    // 12 undated + 30 dated in the same minutes: the undated 12 alone are below the threshold.
    expect(detectImportEvents([...dated, ...burst(12, T0, 2 * MIN)])).toEqual([])
    // 20 undated + 30 dated: only the 20 undated form the event.
    const events = detectImportEvents([...dated, ...burst(20, T0, 2 * MIN)])
    expect(events).toHaveLength(1)
    expect(events[0].reviews).toHaveLength(20)
    expect(events[0].reviews.every(r => !r.review_date)).toBe(true)
  })

  it('a review_date later than created_at is untrusted, so that review still counts as undated', () => {
    const rs = burst(20, T0, 2 * MIN, { review_date: iso(T0 + 30 * DAY) })
    expect(detectImportEvents(rs)).toHaveLength(1)
  })

  it('is deterministic: input order does not matter, ties on created_at break by id', () => {
    const rs = burst(24, T0, 0) // all at the same instant
    const forward = detectImportEvents(rs)
    const reversed = detectImportEvents([...rs].reverse())
    expect(forward).toEqual(reversed)
    expect(forward[0].key).toBe(Math.min(...rs.map(r => r.id)))
  })

  it('ignores reviews whose created_at is unparseable', () => {
    const rs = [...burst(19, T0, MIN), rev({ created_at: 'garbage' })]
    expect(detectImportEvents(rs)).toEqual([])
  })
})

describe('new since last visit with an import', () => {
  const visit = T0 - 2 * HOUR

  it('shows an import as one item with the right count, between loose reviews by time', () => {
    const live = rev({ created_at: iso(T0 + 3 * HOUR), sentiment: 'negative' })
    const old = rev({ created_at: iso(T0 - 1 * HOUR) })
    const imp = burst(40, T0, 6 * MIN, { urgency: 'low', sentiment: 'positive' })
    imp[3].urgency = 'high'
    imp[4].sentiment = 'negative'
    const res = newSince([...imp, live, old], visit, NOW)
    expect(res.reviews).toHaveLength(42) // counts stay by review
    expect(res.items.map(i => i.kind)).toEqual(['review', 'import', 'review'])
    const item = res.items[1]
    expect(item.kind).toBe('import')
    if (item.kind === 'import') {
      expect(item.count).toBe(40)
      expect(item.urgent).toBe(1)
      expect(item.negative).toBe(1)
      expect(item.time).toBe(T0 + 6 * MIN) // when the import finished
    }
  })

  it('an import that straddles the visit instant counts only its members after the visit', () => {
    const start = Date.parse('2026-10-05T04:00:00Z')
    const imp = burst(30, start, 29 * 30 * 1000) // one review per 30 s, 14.5 minutes end to end
    const midVisit = start + 435 * 1000 // members 0-14 at/before, 15-29 after
    const res = newSince(imp, midVisit, NOW)
    expect(res.items).toHaveLength(1)
    const item = res.items[0]
    expect(item.kind === 'import' && item.count).toBe(15)
    expect(res.reviews).toHaveLength(15)
  })

  it('without an import nothing changes: one item per review', () => {
    const rs = burst(19, T0, 5 * MIN)
    const res = newSince(rs, visit, NOW)
    expect(res.items).toHaveLength(19)
    expect(res.items.every(i => i.kind === 'review')).toBe(true)
  })
})

describe('buildDashboardModel with an import', () => {
  it('keeps urgent import reviews in the urgent queue, newest first by created_at, and flags them', () => {
    const imp = burst(25, T0, 5 * MIN)
    imp[0].urgency = 'high'
    imp[24].urgency = 'high'
    const live = rev({ urgency: 'high', created_at: iso(T0 + 2 * HOUR) })
    const m = buildDashboardModel([...imp, live], '7d', NOW, T0 - 2 * HOUR)
    expect(m.urgent.map(r => r.id)).toEqual([live.id, imp[24].id, imp[0].id])
    expect(m.importIds.has(imp[0].id)).toBe(true)
    expect(m.importIds.has(imp[24].id)).toBe(true)
    expect(m.importIds.has(live.id)).toBe(false)
    expect(m.fresh.items.filter(i => i.kind === 'import')).toHaveLength(1)
  })

  it('import detection looks at the whole history, not only the selected range', () => {
    const old = burst(21, NOW - 40 * DAY, 3 * MIN)
    const m = buildDashboardModel(old, '7d', NOW, null)
    expect(m.total).toBe(0)
    expect(m.importIds.size).toBe(21)
  })
})
