// Account housekeeping, not decision data: kept to one quiet line at the bottom. It is a
// calendar-month quota, so it deliberately ignores the time-range selector (and says so).
export default function UsageFooter({ used, quota }: { used: number; quota: number }) {
  const ratio = quota > 0 ? used / quota : 0
  const near = ratio >= 0.8
  return (
    <footer className="mt-12 border-t border-rule pt-4 text-sm text-ink-soft">
      <p>
        Monthly usage: {used.toLocaleString()} of {quota.toLocaleString()} reviews analysed this calendar month
        {near && <strong className="font-semibold text-ink">, close to your limit</strong>}. Not affected by the time
        range.
      </p>
      <div className="mt-2 h-1.5 max-w-xs overflow-hidden rounded-full bg-cream-deep" aria-hidden="true">
        <div className={`h-full ${near ? 'bg-ember' : 'bg-ink'}`} style={{ width: `${Math.min(ratio * 100, 100)}%` }} />
      </div>
    </footer>
  )
}
