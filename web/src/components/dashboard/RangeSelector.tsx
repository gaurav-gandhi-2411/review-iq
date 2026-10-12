import { RANGES, type RangeKey } from '../../lib/dashboardModel'

interface Props {
  value: RangeKey
  onChange: (range: RangeKey) => void
}

// A native radio group: arrow keys move the selection, Tab enters/leaves the group, and the
// selected state is announced by the browser. The visible segment is the <label>.
export default function RangeSelector({ value, onChange }: Props) {
  return (
    <fieldset className="min-w-0">
      <legend className="sr-only">Time range</legend>
      <div className="inline-flex max-w-full rounded-md border border-ink bg-cream p-0.5">
        {RANGES.map(r => (
          <label key={r.key} className="relative cursor-pointer">
            <input
              type="radio"
              name="dashboard-range"
              value={r.key}
              checked={value === r.key}
              onChange={() => onChange(r.key)}
              className="peer sr-only"
            />
            <span className="block rounded px-3 py-2 text-sm font-medium text-ink-soft transition-colors peer-checked:bg-ink peer-checked:text-cream peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ink hover:text-ink peer-checked:hover:text-cream">
              {r.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
