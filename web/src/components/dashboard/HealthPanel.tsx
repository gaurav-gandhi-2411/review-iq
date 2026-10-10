import { ArrowDownRight, ArrowUpRight, CircleCheck, Minus, TriangleAlert } from 'lucide-react'
import type { Band, Health, HealthTrend } from '../../lib/dashboardModel'

const BAND: Record<Band, { label: string; chip: string; Icon: typeof CircleCheck }> = {
  healthy: { label: 'Looking healthy', chip: 'bg-cream-deep', Icon: CircleCheck },
  needs_attention: { label: 'Needs attention', chip: 'bg-saffron-tint', Icon: TriangleAlert },
  at_risk: { label: 'At risk', chip: 'bg-ember-tint', Icon: TriangleAlert },
}

interface Props {
  health: Health
  trend: HealthTrend
  /** e.g. "previous 30 days" */
  previousLabel: string
}

export default function HealthPanel({ health, trend, previousLabel }: Props) {
  const band = health.band ? BAND[health.band] : null
  return (
    <section aria-labelledby="health-heading">
      <h2 id="health-heading" className="font-display text-xl sm:text-2xl">
        Review health
      </h2>
      {health.score === null ? (
        <p className="mt-3 text-[0.9375rem] text-ink-soft">No reviews in this period, so there is no score to show.</p>
      ) : (
        <>
          <p className="mt-2 flex items-baseline gap-2">
            <span className="font-display text-6xl leading-none" aria-label={`Score ${health.score} out of 100`}>
              {health.score}
            </span>
            <span className="text-lg text-ink-soft" aria-hidden="true">
              / 100
            </span>
          </p>
          {band && (
            <p className={`mt-3 inline-flex items-center gap-1.5 rounded px-2 py-1 text-sm font-medium text-ink ${band.chip}`}>
              <band.Icon size={15} aria-hidden="true" />
              {band.label}
            </p>
          )}
          <TrendLine trend={trend} previousLabel={previousLabel} />
          <p className="mt-3 text-sm text-ink-soft">
            From {health.n} review{health.n === 1 ? '' : 's'}: {health.positive} positive, {health.high} urgent.
            {health.n < 10 && ' That is a small sample, so treat the score as rough.'}
          </p>
        </>
      )}
    </section>
  )
}

function TrendLine({ trend, previousLabel }: { trend: HealthTrend; previousLabel: string }) {
  const base = 'mt-3 flex items-start gap-1.5 text-[0.9375rem]'
  switch (trend.kind) {
    case 'no-baseline':
      return (
        <p className={`${base} text-ink-soft`}>
          All time has no earlier period to compare with. Choose 7 or 30 days to see which way the score is heading.
        </p>
      )
    case 'insufficient':
      return (
        <p className={`${base} text-ink-soft`}>
          Not enough reviews to show a trend. It needs {trend.min} in each period; there are {trend.currentN} now and{' '}
          {trend.previousN} in the {previousLabel}.
        </p>
      )
    case 'up':
    case 'down': {
      const up = trend.kind === 'up'
      const Icon = up ? ArrowUpRight : ArrowDownRight
      const pts = Math.abs(trend.delta)
      return (
        <p className={`${base} font-medium`}>
          <Icon size={18} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span>
            {up ? 'Up' : 'Down'} {pts} point{pts === 1 ? '' : 's'}
            <span className="font-normal text-ink-soft"> from {trend.previousScore} in the {previousLabel}</span>
          </span>
        </p>
      )
    }
    case 'steady':
      return (
        <p className={`${base} font-medium`}>
          <Minus size={18} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span>
            Steady
            <span className="font-normal text-ink-soft">
              {' '}
              against {trend.previousScore} in the {previousLabel}. The change is within normal variation.
            </span>
          </span>
        </p>
      )
  }
}
