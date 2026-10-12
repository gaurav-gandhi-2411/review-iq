// DEV-ONLY: renders the real Layout + DashboardView from fixtures in every state, so layout can
// be inspected (and screenshotted at 360/768/1280) without a login or a backend.
// Reached at /__preview/dashboard?state=normal|urgent-heavy|range-empty|empty|loading|error
// and &range=7d|30d|all. Guarded in App.tsx by import.meta.env.DEV so it is not in the
// production bundle (verified by grepping dist/ for FIXTURE_NOW's marker string in CI-less build).
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import Layout from '../components/Layout'
import DashboardView from '../components/dashboard/DashboardView'
import { buildDashboardModel, type RangeKey } from '../lib/dashboardModel'
import { FIXTURES, FIXTURE_NOW, FIXTURE_PREVIOUS_VISIT, type FixtureName } from './fixtures'

export default function DashboardPreview() {
  const [params] = useSearchParams()
  const state = params.get('state') ?? 'normal'
  const initialRange = (['7d', '30d', 'all'].includes(params.get('range') ?? '') ? params.get('range') : '30d') as RangeKey
  const [range, setRange] = useState<RangeKey>(initialRange)
  const noVisit = params.get('visit') === 'none'

  const reviews = useMemo(() => {
    const key: FixtureName = state in FIXTURES ? (state as FixtureName) : 'normal'
    return FIXTURES[key]()
  }, [state])
  const model = useMemo(
    () => buildDashboardModel(reviews, range, FIXTURE_NOW, noVisit ? null : FIXTURE_PREVIOUS_VISIT),
    [reviews, range, noVisit],
  )

  return (
    <Layout active="dashboard">
      <DashboardView
        status={state === 'loading' ? 'loading' : state === 'error' ? 'error' : 'ready'}
        errorMessage="Service is warming up. Please try again in 30 seconds."
        onRetry={() => undefined}
        range={range}
        onRangeChange={setRange}
        model={model}
        usage={{ used: 0, quota: 50 }}
        now={FIXTURE_NOW}
        onUpload={() => undefined}
        onTrySample={() => undefined}
        onOpenConcern={() => undefined}
      />
    </Layout>
  )
}
