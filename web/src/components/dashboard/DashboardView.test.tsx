import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import DashboardView, { type DashboardViewProps } from './DashboardView'
import { buildDashboardModel, type RangeKey } from '../../lib/dashboardModel'
import { makeReviews, FIXTURE_NOW, FIXTURE_PREVIOUS_VISIT } from '../../dev/fixtures'

function setup(over: Partial<DashboardViewProps> = {}) {
  const props: DashboardViewProps = {
    status: 'ready',
    range: '30d',
    onRangeChange: vi.fn(),
    model: null,
    usage: { used: 0, quota: 50 },
    now: FIXTURE_NOW,
    onUpload: vi.fn(),
    onTrySample: vi.fn(),
    onOpenConcern: vi.fn(),
    ...over,
  }
  render(
    <MemoryRouter>
      <DashboardView {...props} />
    </MemoryRouter>,
  )
  return props
}
const model = (reviews: ReturnType<typeof makeReviews>, range: RangeKey = '30d', visit: number | null = FIXTURE_PREVIOUS_VISIT) =>
  buildDashboardModel(reviews, range, FIXTURE_NOW, visit)

describe('DashboardView states', () => {
  it('loading: a busy status region, no data sections', () => {
    setup({ status: 'loading' })
    expect(screen.getByRole('status', { name: '' , busy: true })).toBeInTheDocument()
    expect(screen.getByText('Loading your reviews')).toBeInTheDocument()
    expect(screen.queryByText(/urgent review/i)).not.toBeInTheDocument()
  })

  it('error: an alert with the message and a working retry', () => {
    const onRetry = vi.fn()
    setup({ status: 'error', errorMessage: 'Service is warming up.', onRetry })
    const alert = screen.getByRole('alert')
    expect(within(alert).getByText('Service is warming up.')).toBeInTheDocument()
    fireEvent.click(within(alert).getByRole('button', { name: /try again/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('empty (no reviews at all): upload and sample actions, no score', () => {
    const props = setup({ model: model([]) })
    expect(screen.getByText('Nothing to review yet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /upload your first reviews/i }))
    expect(props.onUpload).toHaveBeenCalled()
    expect(screen.queryByText(/Review health/)).not.toBeInTheDocument()
  })

  it('range-empty: explains that older reviews exist instead of showing zeros', () => {
    const reviews = makeReviews({ count: 12, spanDays: 10, urgentShare: 0, positiveShare: 0.5, startDaysAgo: 80 })
    setup({ range: '7d', model: model(reviews, '7d') })
    expect(screen.getByText(/No reviews arrived in this period/)).toBeInTheDocument()
    expect(screen.getByText(/12 older reviews/)).toBeInTheDocument()
  })
})

describe('urgent queue', () => {
  const reviews = makeReviews({ count: 60, spanDays: 20, urgentShare: 0.1, positiveShare: 0.5, recentUrgent: 8 })

  it('comes before everything else and renders readable, linked reviews inline', () => {
    setup({ model: model(reviews) })
    const headings = screen.getAllByRole('heading', { level: 2 }).map(h => h.textContent)
    expect(headings[0]).toMatch(/urgent reviews?$/)
    expect(headings.some(h => /Since you were last here/.test(h ?? ''))).toBe(true)
    // Monthly usage is NOT before the queue: it is in the footer.
    const footer = screen.getByText(/Monthly usage/).closest('footer')
    expect(footer).not.toBeNull()
    const queue = screen.getByRole('region', { name: /urgent review/i })
    const links = within(queue).getAllByRole('link', { name: /Read and draft a reply/ })
    expect(links.length).toBeGreaterThan(0)
    expect(links[0].getAttribute('href')).toMatch(/^\/reviews\/[0-9a-f]+/)
    expect(within(queue).getAllByText(/Urgent/).length).toBeGreaterThan(0)
  })

  it('lists newest first', () => {
    const m = model(reviews)
    setup({ model: m })
    const queue = screen.getByRole('region', { name: /urgent review/i })
    const times = within(queue)
      .getAllByRole('listitem')
      .map(li => li.querySelector('time')!.getAttribute('datetime')!)
    expect(times.length).toBeGreaterThan(1)
    expect([...times].sort().reverse()).toEqual(times)
  })

  it('shows 5, then expands inline to the rest', () => {
    const m = model(reviews)
    expect(m.urgent.length).toBeGreaterThan(5)
    setup({ model: m })
    const queue = screen.getByRole('region', { name: /urgent review/i })
    expect(within(queue).getAllByRole('listitem')).toHaveLength(5)
    const btn = within(queue).getByRole('button', { name: /more urgent/i })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(btn)
    expect(within(queue).getAllByRole('listitem')).toHaveLength(m.urgent.length)
  })

  it('empty state when nothing is urgent', () => {
    const calm = makeReviews({ count: 30, spanDays: 20, urgentShare: 0, positiveShare: 0.7 })
    setup({ model: model(calm) })
    expect(screen.getByText(/Nothing urgent/)).toBeInTheDocument()
  })
})

describe('time range selector', () => {
  it('is a labelled radio group; choosing a range calls back, and the choice is exposed as checked', () => {
    const reviews = makeReviews({ count: 40, spanDays: 20, urgentShare: 0.1, positiveShare: 0.5 })
    const props = setup({ range: '30d', model: model(reviews) })
    const group = screen.getByRole('group', { name: 'Time range' })
    const radios = within(group).getAllByRole('radio')
    expect(radios.map(r => (r as HTMLInputElement).value)).toEqual(['7d', '30d', 'all'])
    expect(within(group).getByRole('radio', { name: 'Last 30 days' })).toBeChecked()
    fireEvent.click(within(group).getByRole('radio', { name: 'Last 7 days' }))
    expect(props.onRangeChange).toHaveBeenCalledWith('7d')
  })

  it('announces the scoped result politely for screen readers', () => {
    const reviews = makeReviews({ count: 40, spanDays: 20, urgentShare: 0.1, positiveShare: 0.5 })
    const m = model(reviews, '7d')
    setup({ range: '7d', model: m })
    const live = screen.getByText(/Showing last 7 days:/)
    expect(live).toHaveAttribute('aria-live', 'polite')
    expect(live.textContent).toContain(`${m.total} review`)
  })
})

describe('health and concerns copy', () => {
  it('"all time" says there is no earlier period instead of faking a trend', () => {
    const reviews = makeReviews({ count: 80, spanDays: 50, urgentShare: 0.05, positiveShare: 0.6 })
    setup({ range: 'all', model: model(reviews, 'all') })
    expect(screen.getByText(/All time has no earlier period to compare with/)).toBeInTheDocument()
    expect(screen.getByText(/All time has no earlier period, so trends are not shown/)).toBeInTheDocument()
  })

  it('too little data in a period: honest "not enough reviews" with the counts', () => {
    const reviews = makeReviews({ count: 6, spanDays: 6, urgentShare: 0, positiveShare: 0.6 })
    setup({ range: '7d', model: model(reviews, '7d') })
    expect(screen.getByText(/Not enough reviews to show a trend/)).toBeInTheDocument()
  })

  it('a rising concern carries a text label, not colour alone', () => {
    const reviews = makeReviews({ count: 120, spanDays: 60, urgentShare: 0.05, positiveShare: 0.6, risingTopic: 'delivery_delay' })
    setup({ range: '30d', model: model(reviews, '30d') })
    const concerns = screen.getByRole('region', { name: /What customers keep raising/ })
    expect(within(concerns).getAllByText(/^(New|Rising|Easing|Steady)$/).length).toBeGreaterThan(0)
  })
})
