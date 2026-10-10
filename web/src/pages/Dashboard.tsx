import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout'
import DashboardView from '../components/dashboard/DashboardView'
import { useFilterContext } from '../lib/filterContext'
import { getAccount, type Review } from '../lib/api'
import { buildDashboardModel, type RangeKey } from '../lib/dashboardModel'
import { readRange, writeRange } from '../lib/lastVisit'
import { useLastVisit } from '../lib/useLastVisit'

// Container: data comes from the shared FilterProvider, which already loads every review
// (with created_at) once. All window math is client-side over that one load, so switching
// range is instant and the sections cannot disagree (one dataset, one `now`). Preferences
// persist to localStorage; the view itself is presentational.
export default function DashboardPage({ userId }: { userId: string | null }) {
  const { allReviews, loading, loadError } = useFilterContext()
  const [usage, setUsage] = useState<{ used: number; quota: number } | null>(null)
  const [range, setRange] = useState<RangeKey>(() => readRange())

  useEffect(() => {
    getAccount()
      .then(acc => setUsage({ used: acc.usage_this_month, quota: acc.quota }))
      .catch(() => { /* non-fatal: the usage line is best-effort; Layout's banner covers the warning path */ })
  }, [])

  function changeRange(next: RangeKey) {
    setRange(next)
    writeRange(next)
  }

  return (
    <Layout active="dashboard">
      {loading || loadError ? (
        <DashboardView
          status={loading ? 'loading' : 'error'}
          errorMessage={loadError?.message}
          onRetry={() => window.location.reload()}
          range={range}
          onRangeChange={changeRange}
          model={null}
          usage={null}
          now={0}
          onUpload={() => undefined}
          onTrySample={() => undefined}
          onOpenConcern={() => undefined}
        />
      ) : (
        <LoadedDashboard
          reviews={allReviews}
          userId={userId}
          usage={usage}
          range={range}
          onRangeChange={changeRange}
        />
      )}
    </Layout>
  )
}

// Mounted only after a successful load, so the "last visit" is recorded only for a dashboard
// the user actually got to see.
function LoadedDashboard(props: {
  reviews: Review[]
  userId: string | null
  usage: { used: number; quota: number } | null
  range: RangeKey
  onRangeChange: (r: RangeKey) => void
}) {
  const navigate = useNavigate()
  const { setFilter } = useFilterContext()
  const previousVisit = useLastVisit(props.userId)
  const [now] = useState(() => Date.now()) // one clock for every section
  const model = useMemo(
    () => buildDashboardModel(props.reviews, props.range, now, previousVisit),
    [props.reviews, props.range, now, previousVisit],
  )

  return (
    <DashboardView
      status="ready"
      range={props.range}
      onRangeChange={props.onRangeChange}
      model={model}
      usage={props.usage}
      now={now}
      onUpload={() => navigate('/upload')}
      onTrySample={() => navigate('/upload?sample=1')}
      onOpenConcern={topic => {
        setFilter('topic', topic)
        navigate('/reviews')
      }}
    />
  )
}
