import { useEffect, useState } from 'react'
import { readVisit, resolveVisit, touchVisit, writeVisit, type VisitState } from './lastVisit'

/**
 * The instant of the user's previous session on this browser, or null when unknown (first
 * visit, or localStorage unavailable). The UI must treat null as "unknown" and still render.
 *
 * Mount this only once the dashboard data has loaded successfully: the visit is recorded at
 * mount, so a failed load does not count as having "seen" the dashboard. The resolution is
 * computed once (lazy state) from storage, which keeps it stable across re-renders, and the
 * write happens in an effect. React StrictMode runs the initializer twice; both runs read the
 * same stored value and the session logic is idempotent, so the result is identical.
 *
 * While the page is open, lastSeenAt is refreshed when the tab is hidden or closed, so
 * reviews that arrive during a session are not reported as new at the next visit. sessionPrev
 * stays pinned (see lastVisit.ts), which is what keeps the section from emptying on refresh.
 */
export function useLastVisit(userId: string | null): number | null {
  const [resolution] = useState(() => (userId ? resolveVisit(readVisit(userId), Date.now()) : null))

  useEffect(() => {
    if (!userId || !resolution) return
    let state: VisitState = resolution.next
    writeVisit(userId, state)
    const save = () => {
      state = touchVisit(state, Date.now())
      writeVisit(userId, state)
    }
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') save()
    }
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', save)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', save)
      save()
    }
  }, [userId, resolution])

  return resolution?.previousVisit ?? null
}
