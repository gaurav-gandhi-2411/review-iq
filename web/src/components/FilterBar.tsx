import { X } from 'lucide-react'
import { useFilterContext, type FilterKey } from '../lib/filterContext'

const FILTER_LABELS: Record<FilterKey, string> = {
  urgency: 'Urgency',
  sentiment: 'Sentiment',
  topic: 'Topic',
  language: 'Language',
}

const VALUE_LABELS: Record<string, string> = {
  high: 'High', medium: 'Medium', low: 'Low',
  positive: 'Positive', negative: 'Negative', neutral: 'Neutral', mixed: 'Mixed',
  en: 'English', hi: 'Hindi', 'hi-en': 'Hindi/English',
}

export default function FilterBar() {
  const { filters, setFilter, clearFilters, hasActiveFilters } = useFilterContext()

  if (!hasActiveFilters) return null

  const activeEntries = (Object.entries(filters) as [FilterKey, string | null][]).filter(([, v]) => v !== null)

  return (
    <div className="flex items-center gap-2 flex-wrap mb-4">
      <span className="text-xs font-sans text-charcoal-light">Filtered by:</span>
      {activeEntries.map(([key, value]) => (
        <span
          key={key}
          className="inline-flex items-center gap-1.5 bg-green-light text-green text-xs font-sans px-2.5 py-1 rounded-full"
        >
          <span className="font-medium">{FILTER_LABELS[key]}:</span>
          {VALUE_LABELS[value!] ?? value}
          <button
            onClick={() => setFilter(key, null)}
            className="hover:text-green-muted transition-colors ml-0.5"
          >
            <X size={11} />
          </button>
        </span>
      ))}
      <button
        onClick={clearFilters}
        className="text-xs font-sans text-charcoal-light hover:text-charcoal underline underline-offset-2 transition-colors"
      >
        Clear all
      </button>
    </div>
  )
}
