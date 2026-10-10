import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  SESSION_GAP_MS,
  readRange,
  readVisit,
  resolveVisit,
  touchVisit,
  visitKey,
  writeRange,
  writeVisit,
} from './lastVisit'

const T0 = Date.parse('2026-10-02T16:12:00+05:30') // Friday
const MONDAY = Date.parse('2026-10-05T09:30:00+05:30')
const MIN = 60 * 1000

describe('resolveVisit', () => {
  it('first ever visit: nothing to compare against, and the visit is recorded', () => {
    const r = resolveVisit(null, MONDAY)
    expect(r.previousVisit).toBeNull()
    expect(r.next).toEqual({ lastSeenAt: MONDAY, sessionPrev: null })
  })

  it('a visit after a long gap reports the previous session end as the since-instant', () => {
    const r = resolveVisit({ lastSeenAt: T0, sessionPrev: null }, MONDAY)
    expect(r.previousVisit).toBe(T0)
    expect(r.next).toEqual({ lastSeenAt: MONDAY, sessionPrev: T0 })
  })

  it('REFRESH does not empty the section: the same session keeps the pinned since-instant', () => {
    const first = resolveVisit({ lastSeenAt: T0, sessionPrev: null }, MONDAY)
    // refresh 3 seconds later, reading what the first load persisted
    const refresh = resolveVisit(first.next, MONDAY + 3000)
    expect(refresh.previousVisit).toBe(T0)
    // and again after a few more minutes of activity (lastSeenAt advanced by touch)
    const touched = touchVisit(refresh.next, MONDAY + 20 * MIN)
    expect(resolveVisit(touched, MONDAY + 25 * MIN).previousVisit).toBe(T0)
  })

  it('a second tab inside the session sees the same since-instant', () => {
    const first = resolveVisit({ lastSeenAt: T0, sessionPrev: null }, MONDAY)
    expect(resolveVisit(first.next, MONDAY + 5 * MIN).previousVisit).toBe(T0)
  })

  it('after the session gap passes, the NEXT visit sees the end of the previous session', () => {
    const first = resolveVisit({ lastSeenAt: T0, sessionPrev: null }, MONDAY)
    const touched = touchVisit(first.next, MONDAY + 10 * MIN)
    const later = MONDAY + 10 * MIN + SESSION_GAP_MS + 1
    const next = resolveVisit(touched, later)
    expect(next.previousVisit).toBe(MONDAY + 10 * MIN)
    expect(next.next.sessionPrev).toBe(MONDAY + 10 * MIN)
  })

  it('a stored time in the future (clock moved back) is not trusted', () => {
    const r = resolveVisit({ lastSeenAt: MONDAY + 1000, sessionPrev: T0 }, MONDAY)
    expect(r.previousVisit).toBeNull()
    expect(r.next).toEqual({ lastSeenAt: MONDAY, sessionPrev: null })
  })

  it('touchVisit never moves lastSeenAt backwards and keeps sessionPrev', () => {
    expect(touchVisit({ lastSeenAt: 100, sessionPrev: 5 }, 50)).toEqual({ lastSeenAt: 100, sessionPrev: 5 })
    expect(touchVisit({ lastSeenAt: 100, sessionPrev: 5 }, 200)).toEqual({ lastSeenAt: 200, sessionPrev: 5 })
  })
})

describe('storage wrappers', () => {
  beforeEach(() => localStorage.clear())

  it('round-trips per user id and does not leak between users', () => {
    writeVisit('user-a', { lastSeenAt: 1, sessionPrev: null })
    writeVisit('user-b', { lastSeenAt: 2, sessionPrev: 1 })
    expect(readVisit('user-a')).toEqual({ lastSeenAt: 1, sessionPrev: null })
    expect(readVisit('user-b')).toEqual({ lastSeenAt: 2, sessionPrev: 1 })
    expect(readVisit('user-c')).toBeNull()
    expect(localStorage.getItem(visitKey('user-a'))).not.toBeNull()
  })

  it('treats corrupt stored data as no data', () => {
    localStorage.setItem(visitKey('u'), '{not json')
    expect(readVisit('u')).toBeNull()
    localStorage.setItem(visitKey('u'), JSON.stringify({ lastSeenAt: 'yesterday' }))
    expect(readVisit('u')).toBeNull()
    localStorage.setItem(visitKey('u'), 'null')
    expect(readVisit('u')).toBeNull()
  })

  it('survives a getItem that throws (blocked storage)', () => {
    const broken = { getItem: vi.fn(() => { throw new Error('SecurityError') }) }
    expect(readVisit('u', broken)).toBeNull()
    expect(readRange(broken)).toBe('30d')
  })

  it('survives a setItem that throws (quota / private mode) and reports failure', () => {
    const broken = { setItem: vi.fn(() => { throw new Error('QuotaExceededError') }) }
    expect(writeVisit('u', { lastSeenAt: 1, sessionPrev: null }, broken)).toBe(false)
    expect(() => writeRange('7d', broken)).not.toThrow()
  })

  it('works with no storage at all (null)', () => {
    expect(readVisit('u', null)).toBeNull()
    expect(writeVisit('u', { lastSeenAt: 1, sessionPrev: null }, null)).toBe(false)
    expect(readRange(null)).toBe('30d')
    expect(() => writeRange('7d', null)).not.toThrow()
  })

  it('with failing storage the flow still resolves to "unknown", never throws', () => {
    const broken = { getItem: () => { throw new Error('x') }, setItem: () => { throw new Error('x') } }
    const r = resolveVisit(readVisit('u', broken), MONDAY)
    expect(r.previousVisit).toBeNull()
    expect(writeVisit('u', r.next, broken)).toBe(false)
  })

  it('range: persists valid values and rejects junk', () => {
    expect(readRange()).toBe('30d')
    writeRange('7d')
    expect(readRange()).toBe('7d')
    localStorage.setItem('samidha:dashboardRange', 'forever')
    expect(readRange()).toBe('30d')
  })
})
