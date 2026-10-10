import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import DashboardView from './DashboardView'
import { buildDashboardModel } from '../../lib/dashboardModel'
import { FIXTURES, FIXTURE_NOW, FIXTURE_PREVIOUS_VISIT } from '../../dev/fixtures'

// S19: a 140-review undated CSV (analysed over 6 minutes, 3 h before FIXTURE_NOW) must show as
// ONE item in "new since last visit", and its urgent reviews must stay in the queue, labelled.
function renderBulk() {
  const model = buildDashboardModel(FIXTURES['bulk-import'](), '30d', FIXTURE_NOW, FIXTURE_PREVIOUS_VISIT)
  render(
    <MemoryRouter>
      <DashboardView
        status="ready"
        range="30d"
        onRangeChange={vi.fn()}
        model={model}
        usage={{ used: 0, quota: 50 }}
        now={FIXTURE_NOW}
        onUpload={vi.fn()}
        onTrySample={vi.fn()}
        onOpenConcern={vi.fn()}
      />
    </MemoryRouter>,
  )
  return model
}

describe('bulk import grouping in the dashboard', () => {
  it('collapses the import to one "N reviews imported <time>" item, not N rows', () => {
    const model = renderBulk()
    expect(model.fresh.reviews.length).toBeGreaterThan(140) // counts stay by review
    const fresh = screen.getByRole('region', { name: /Since you were last here/ })
    expect(within(fresh).getByText(/140 reviews imported/)).toBeInTheDocument()
    expect(within(fresh).getByText('3 h ago')).toBeInTheDocument()
    // 1 import item + the handful of loose new reviews; nowhere near 140 rows.
    expect(within(fresh).getAllByRole('listitem').length).toBeLessThanOrEqual(5)
    expect(within(fresh).getByText(/140 of them arrived in a bulk import/)).toBeInTheDocument()
  })

  it('the import item says its urgent reviews are in the queue, and links to all reviews', () => {
    renderBulk()
    const fresh = screen.getByRole('region', { name: /Since you were last here/ })
    expect(within(fresh).getByText(/Urgent reviews from an import stay in the urgent queue/)).toBeInTheDocument()
    expect(within(fresh).getByRole('link', { name: 'Browse all reviews' })).toHaveAttribute('href', '/reviews')
  })

  it('urgent reviews from the import stay in the queue, each labelled "From import"', () => {
    const model = renderBulk()
    const imported = model.urgent.filter(r => model.importIds.has(r.id))
    expect(imported).toHaveLength(4)
    const queue = screen.getByRole('region', { name: /urgent review/i })
    // The queue previews 5; expand it so every urgent review is rendered.
    const expand = within(queue).queryByRole('button', { name: /more urgent/i })
    if (expand) fireEvent.click(expand)
    expect(within(queue).getAllByText('From import')).toHaveLength(4)
    expect(within(queue).getAllByRole('listitem')).toHaveLength(model.urgent.length)
  })

  it('a dashboard without an import has no import wording', () => {
    const model = buildDashboardModel(FIXTURES.normal(), '30d', FIXTURE_NOW, FIXTURE_PREVIOUS_VISIT)
    render(
      <MemoryRouter>
        <DashboardView
          status="ready"
          range="30d"
          onRangeChange={vi.fn()}
          model={model}
          usage={null}
          now={FIXTURE_NOW}
          onUpload={vi.fn()}
          onTrySample={vi.fn()}
          onOpenConcern={vi.fn()}
        />
      </MemoryRouter>,
    )
    expect(screen.queryByText(/imported/)).not.toBeInTheDocument()
    expect(screen.queryByText('From import')).not.toBeInTheDocument()
  })
})
