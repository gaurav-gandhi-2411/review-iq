import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import SkippedReviewsView, { type SkippedReviewsViewProps } from './SkippedReviewsView'
import { BffError, QuotaError } from '../lib/api'
import { encoderChips, openErrorMessage, skippedReviewsEnabled } from '../lib/skippedReviews'
import { SKIPPED_FIXTURE_ROWS } from '../dev/fixtures'

function setup(over: Partial<SkippedReviewsViewProps> = {}) {
  const props: SkippedReviewsViewProps = {
    status: 'ready',
    rows: SKIPPED_FIXTURE_ROWS,
    rowStates: {},
    onAnalyse: vi.fn(),
    hasMore: false,
    loadingMore: false,
    onLoadMore: vi.fn(),
    ...over,
  }
  render(
    <MemoryRouter>
      <SkippedReviewsView {...props} />
    </MemoryRouter>,
  )
  return props
}

describe('SkippedReviewsView states', () => {
  it('normal: one list item per review with product, chips and preview', () => {
    setup()
    const items = screen.getAllByRole('listitem').filter((li: HTMLElement) => li.querySelector('button'))
    expect(items).toHaveLength(SKIPPED_FIXTURE_ROWS.length)
    expect(screen.getByText('Linen Kurta')).toBeInTheDocument()
    expect(screen.getByText('sentiment: positive')).toBeInTheDocument()
    expect(screen.getByText(/Paisa vasool overall/)).toBeInTheDocument()
  })

  it('loading: a busy status region and no rows', () => {
    setup({ status: 'loading', rows: [] })
    expect(screen.getByRole('status', { busy: true })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /analyse/i })).not.toBeInTheDocument()
  })

  it('empty: says nothing is waiting', () => {
    setup({ rows: [] })
    expect(screen.getByText('Nothing waiting')).toBeInTheDocument()
    expect(screen.getByText(/Every review has been analysed/)).toBeInTheDocument()
  })

  it('error: shows the message with a working retry', () => {
    const onRetry = vi.fn()
    setup({ status: 'error', rows: [], error: new Error('boom'), onRetry })
    expect(screen.getByText('boom')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('off: a calm not-enabled message, not an error', () => {
    setup({ status: 'off', rows: [] })
    expect(screen.getByText('This view is not enabled')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('SkippedReviewsView actions', () => {
  it('Analyse calls the handler with that row id', () => {
    const props = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Analyse review of Steel Water Bottle' }))
    expect(props.onAnalyse).toHaveBeenCalledWith(SKIPPED_FIXTURE_ROWS[1].review_id)
  })

  it('a pending row is disabled and labelled; other rows stay usable', () => {
    setup({ rowStates: { [SKIPPED_FIXTURE_ROWS[0].review_id]: { phase: 'pending' } } })
    expect(screen.getByRole('button', { name: 'Analyse review of Linen Kurta' })).toBeDisabled()
    expect(screen.getByText('Analysing…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analyse review of Steel Water Bottle' })).toBeEnabled()
  })

  it('a failed row shows its own message beside its button', () => {
    setup({ rowStates: { [SKIPPED_FIXTURE_ROWS[0].review_id]: { phase: 'error', message: "This review's data has been purged." } } })
    expect(screen.getByRole('alert')).toHaveTextContent('purged')
    expect(screen.getByRole('button', { name: 'Analyse review of Linen Kurta' })).toBeEnabled()
  })

  it('a done row shows the analysis inline instead of the button', () => {
    setup({
      rowStates: {
        [SKIPPED_FIXTURE_ROWS[0].review_id]: {
          phase: 'done',
          result: { product: 'Linen Kurta', sentiment: 'positive', urgency: 'low', pros: ['fit'], cons: [], topics: [] },
        },
      },
    })
    expect(screen.queryByRole('button', { name: 'Analyse review of Linen Kurta' })).not.toBeInTheDocument()
    expect(screen.getByText(/Analysed: positive, low urgency/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /go to your reviews/i })).toHaveAttribute('href', '/reviews')
  })

  it('Load more appears only when hasMore, and calls the handler', () => {
    const props = setup({ hasMore: true })
    fireEvent.click(within(document.body).getByRole('button', { name: 'Load more' }))
    expect(props.onLoadMore).toHaveBeenCalledOnce()
  })

  it('no Load more when the last page was short', () => {
    setup({ hasMore: false })
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })
})

describe('skipped-reviews helpers', () => {
  it('410 maps to the purged message', () => {
    expect(openErrorMessage(new BffError(410, 'gone'))).toBe("This review's data has been purged.")
  })

  it('429 maps to the monthly-limit wording; unknown errors get the generic one', () => {
    expect(openErrorMessage(new QuotaError())).toMatch(/Monthly review limit reached/)
    expect(openErrorMessage(new Error('x'))).toBe('Could not analyse this review. Try again.')
  })

  it('the flag is on only for the exact string "true" (default off)', () => {
    expect(skippedReviewsEnabled('true')).toBe(true)
    expect(skippedReviewsEnabled(undefined)).toBe(false)
    expect(skippedReviewsEnabled('')).toBe(false)
    expect(skippedReviewsEnabled('1')).toBe(false)
    expect(skippedReviewsEnabled('TRUE')).toBe(false)
  })

  it('chips drop null labels', () => {
    expect(encoderChips({ sentiment: 'positive', urgency: null, review_type: 'x' })).toEqual([
      'sentiment: positive',
      'review type: x',
    ])
  })
})
