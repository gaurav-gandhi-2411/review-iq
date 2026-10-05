// "New since your last visit" persistence.
//
// Per-browser, per-user: stored in localStorage, never on the server (a cross-device version
// needs a per-user last_seen column or table, i.e. a schema change that is out of scope).
//
// Why the state has two timestamps. Storing only "now at load" would make the section empty on
// refresh (the previous visit would be 3 seconds ago). Instead we track a SESSION: a run of
// visits with gaps shorter than SESSION_GAP_MS.
//   lastSeenAt  - the latest moment the user was on the dashboard (refreshed on load and when
//                 the tab is hidden/closed), so reviews that arrived while the page was open
//                 are not reported as new next time.
//   sessionPrev - lastSeenAt as it stood when THIS session began = "previous session end".
//                 It is pinned for the whole session, so refreshes and second tabs keep
//                 showing the same "since" instant.
// A visit after a gap longer than SESSION_GAP_MS starts a new session: sessionPrev becomes the
// old lastSeenAt.

export const SESSION_GAP_MS = 30 * 60 * 1000

export interface VisitState {
  lastSeenAt: number
  sessionPrev: number | null
}

export interface VisitResolution {
  /** The "since" instant to show; null when there is no known previous visit. */
  previousVisit: number | null
  /** What to persist after this load. */
  next: VisitState
}

export function resolveVisit(stored: VisitState | null, now: number): VisitResolution {
  if (stored === null) {
    return { previousVisit: null, next: { lastSeenAt: now, sessionPrev: null } }
  }
  const gap = now - stored.lastSeenAt
  // A stored time in the future (clock moved back, or a corrupt value) is not a usable
  // "previous visit": restart cleanly instead of reporting a negative gap as same-session.
  if (gap < 0) {
    return { previousVisit: null, next: { lastSeenAt: now, sessionPrev: null } }
  }
  if (gap > SESSION_GAP_MS) {
    return {
      previousVisit: stored.lastSeenAt,
      next: { lastSeenAt: now, sessionPrev: stored.lastSeenAt },
    }
  }
  return {
    previousVisit: stored.sessionPrev,
    next: { lastSeenAt: now, sessionPrev: stored.sessionPrev },
  }
}

export function touchVisit(state: VisitState, now: number): VisitState {
  return { ...state, lastSeenAt: Math.max(state.lastSeenAt, now) }
}

// ---- Storage (every access wrapped: private mode / blocked storage / quota must not break the UI) ----

const KEY_PREFIX = 'samidha:lastVisit:'

export function visitKey(userId: string): string {
  return `${KEY_PREFIX}${userId}`
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

export function readVisit(userId: string, storage: Pick<Storage, 'getItem'> | null = safeLocalStorage()): VisitState | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(visitKey(userId))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const { lastSeenAt, sessionPrev } = parsed as Record<string, unknown>
    if (!isFiniteNumber(lastSeenAt)) return null
    return { lastSeenAt, sessionPrev: isFiniteNumber(sessionPrev) ? sessionPrev : null }
  } catch {
    return null
  }
}

export function writeVisit(
  userId: string,
  state: VisitState,
  storage: Pick<Storage, 'setItem'> | null = safeLocalStorage(),
): boolean {
  if (!storage) return false
  try {
    storage.setItem(visitKey(userId), JSON.stringify(state))
    return true
  } catch {
    return false
  }
}

function safeLocalStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    // Accessing window.localStorage itself can throw (blocked site data).
    return null
  }
}

// ---- Selected range persistence (same failure policy) ----

const RANGE_KEY = 'samidha:dashboardRange'
const VALID_RANGES = ['7d', '30d', 'all'] as const

export function readRange(storage: Pick<Storage, 'getItem'> | null = safeLocalStorage()): (typeof VALID_RANGES)[number] {
  try {
    const v = storage?.getItem(RANGE_KEY)
    return (VALID_RANGES as readonly string[]).includes(v ?? '') ? (v as (typeof VALID_RANGES)[number]) : '30d'
  } catch {
    return '30d'
  }
}

export function writeRange(range: string, storage: Pick<Storage, 'setItem'> | null = safeLocalStorage()): void {
  try {
    storage?.setItem(RANGE_KEY, range)
  } catch {
    /* convenience only */
  }
}
