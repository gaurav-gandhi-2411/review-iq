import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useLastVisit } from './useLastVisit'
import { readVisit, visitKey } from './lastVisit'

const FRIDAY = Date.parse('2026-10-02T16:12:00+05:30')
const MONDAY = Date.parse('2026-10-05T09:30:00+05:30')

describe('useLastVisit', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('reports Friday as the previous visit on Monday, and a REFRESH still reports Friday', () => {
    localStorage.setItem(visitKey('u1'), JSON.stringify({ lastSeenAt: FRIDAY, sessionPrev: null }))
    vi.setSystemTime(MONDAY)
    const first = renderHook(() => useLastVisit('u1'))
    expect(first.result.current).toBe(FRIDAY)
    first.unmount()

    vi.setSystemTime(MONDAY + 4000) // page refresh
    const second = renderHook(() => useLastVisit('u1'))
    expect(second.result.current).toBe(FRIDAY)
  })

  it('first visit: unknown (null), and the visit is recorded for next time', () => {
    vi.setSystemTime(MONDAY)
    const { result } = renderHook(() => useLastVisit('u1'))
    expect(result.current).toBeNull()
    expect(readVisit('u1')?.lastSeenAt).toBe(MONDAY)
  })

  it('scopes by user id', () => {
    localStorage.setItem(visitKey('u1'), JSON.stringify({ lastSeenAt: FRIDAY, sessionPrev: null }))
    vi.setSystemTime(MONDAY)
    expect(renderHook(() => useLastVisit('someone-else')).result.current).toBeNull()
  })

  it('renders (returns null) when localStorage throws on every access', () => {
    vi.setSystemTime(MONDAY)
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    const { result, unmount } = renderHook(() => useLastVisit('u1'))
    expect(result.current).toBeNull()
    expect(() => unmount()).not.toThrow()
  })

  it('records time spent on the page when the tab is hidden, so reviews that arrived meanwhile are not "new" next time', () => {
    vi.setSystemTime(MONDAY)
    renderHook(() => useLastVisit('u1'))
    vi.setSystemTime(MONDAY + 12 * 60 * 1000)
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(readVisit('u1')?.lastSeenAt).toBe(MONDAY + 12 * 60 * 1000)
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  })
})
