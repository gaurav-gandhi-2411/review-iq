// Context files export both the Provider component and a hook — this is the idiomatic
// React pattern and cannot be split without circular dependency. Disable the fast-refresh
// rule for this file only.
/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useState, useMemo } from 'react'
import { getReviews, type Review } from './api'

export type FilterKey = 'urgency' | 'sentiment' | 'topic' | 'language'

export interface ActiveFilters {
  urgency: string | null
  sentiment: string | null
  topic: string | null
  language: string | null
}

export interface FilterStats {
  total: number
  positiveCount: number
  negativeCount: number
  neutralCount: number
  mixedCount: number
  highUrgencyCount: number
  topTopics: Array<{ topic: string; count: number }>
}

interface FilterContextValue {
  allReviews: Review[]
  filteredReviews: Review[]
  filters: ActiveFilters
  loading: boolean
  loadError: Error | null
  setFilter: (key: FilterKey, value: string | null) => void
  clearFilters: () => void
  hasActiveFilters: boolean
  stats: FilterStats
}

const EMPTY_FILTERS: ActiveFilters = { urgency: null, sentiment: null, topic: null, language: null }

const FilterContext = createContext<FilterContextValue | null>(null)

async function loadAllReviews(): Promise<Review[]> {
  const all: Review[] = []
  let offset = 0
  const BATCH = 200
  while (true) {
    const data = await getReviews({ limit: BATCH, offset })
    all.push(...data.results)
    if (data.results.length < BATCH) break
    offset += data.results.length
  }
  return all
}

function computeStats(reviews: Review[]): FilterStats {
  const total = reviews.length
  const positiveCount = reviews.filter(r => r.sentiment === 'positive').length
  const negativeCount = reviews.filter(r => r.sentiment === 'negative').length
  const neutralCount = reviews.filter(r => r.sentiment === 'neutral').length
  const mixedCount = reviews.filter(r => r.sentiment === 'mixed').length
  const highUrgencyCount = reviews.filter(r => r.urgency === 'high').length

  const topicCounts: Record<string, number> = {}
  for (const r of reviews) {
    for (const t of r.topics) {
      topicCounts[t] = (topicCounts[t] ?? 0) + 1
    }
  }
  const topTopics = Object.entries(topicCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([topic, count]) => ({ topic, count }))

  return { total, positiveCount, negativeCount, neutralCount, mixedCount, highUrgencyCount, topTopics }
}

export function FilterProvider({ children }: { children: React.ReactNode }) {
  const [allReviews, setAllReviews] = useState<Review[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<Error | null>(null)
  const [filters, setFilters] = useState<ActiveFilters>(EMPTY_FILTERS)

  useEffect(() => {
    loadAllReviews()
      .then(reviews => { setAllReviews(reviews); setLoading(false) })
      .catch(err => {
        setLoadError(err instanceof Error ? err : new Error('Failed to load reviews'))
        setLoading(false)
      })
  }, [])

  const filteredReviews = useMemo(() => {
    return allReviews.filter(r => {
      if (filters.urgency && r.urgency !== filters.urgency) return false
      if (filters.sentiment && r.sentiment !== filters.sentiment) return false
      if (filters.topic && !r.topics.includes(filters.topic)) return false
      if (filters.language && r.language !== filters.language) return false
      return true
    })
  }, [allReviews, filters])

  const stats = useMemo(() => computeStats(filteredReviews), [filteredReviews])

  const hasActiveFilters = Object.values(filters).some(v => v !== null)

  function setFilter(key: FilterKey, value: string | null) {
    setFilters(prev => ({ ...prev, [key]: value }))
  }

  function clearFilters() {
    setFilters(EMPTY_FILTERS)
  }

  return (
    <FilterContext.Provider
      value={{ allReviews, filteredReviews, filters, loading, loadError, setFilter, clearFilters, hasActiveFilters, stats }}
    >
      {children}
    </FilterContext.Provider>
  )
}

export function useFilterContext() {
  const ctx = useContext(FilterContext)
  if (!ctx) throw new Error('useFilterContext must be used within FilterProvider')
  return ctx
}
