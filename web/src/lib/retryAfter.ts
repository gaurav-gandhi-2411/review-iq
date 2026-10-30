// Pure helpers (no imports) so they can be unit-tested with `node --test` without a bundler.

/** Parse a Retry-After header value (delta-seconds form) into whole seconds, or null. */
export function parseRetryAfter(value: string | null | undefined): number | null {
  if (value == null) return null
  const trimmed = value.trim()
  if (!/^\d+$/.test(trimmed)) return null // HTTP-date form is not emitted by this API
  const secs = Number(trimmed)
  return secs > 0 ? secs : null
}

/** "try again in 45 s" / "try again in 6 min" -- the user-facing wait phrase. */
export function describeWait(seconds: number | null): string {
  if (seconds == null) return 'try again in a minute'
  if (seconds < 90) return `try again in ${seconds} s`
  return `try again in ${Math.ceil(seconds / 60)} min`
}
