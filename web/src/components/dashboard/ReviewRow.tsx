import { Link } from 'react-router'
import type { Review } from '../../lib/api'
import { excerpt, formatArrival, reviewTime } from '../../lib/dashboardModel'

const SENTIMENT_LABEL: Record<string, string> = {
  positive: 'Positive',
  negative: 'Negative',
  neutral: 'Neutral',
  mixed: 'Mixed',
}
const URGENCY_LABEL: Record<string, string> = { high: 'Urgent', medium: 'Medium urgency', low: 'Low urgency' }

interface Props {
  review: Review
  now: number
  /** 'dark' rows sit on the ink panel, 'light' rows on cream. */
  tone: 'dark' | 'light'
  /** The review belongs to a detected bulk import (see dashboardModel.ts): label it. */
  fromImport?: boolean
}

export default function ReviewRow({ review, now, tone, fromImport = false }: Props) {
  const dark = tone === 'dark'
  const ts = reviewTime(review)
  const hash = review.input_hash.replace('sha256:', '')
  const product = review.product?.trim() || 'Unnamed product'
  const sentiment = review.sentiment ? SENTIMENT_LABEL[review.sentiment] : 'Sentiment unknown'
  const urgent = review.urgency === 'high'

  return (
    <li className={`py-4 first:pt-0 last:pb-0 ${dark ? 'border-ink-rule' : 'border-rule'} border-b last:border-b-0`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className={`font-medium ${dark ? 'text-cream' : 'text-ink'}`}>{product}</span>
        <span className={dark ? 'text-cream-soft' : 'text-ink-soft'}>{sentiment}</span>
        <span
          className={`rounded px-1.5 py-0.5 text-xs font-medium text-ink ${
            urgent ? (dark ? 'bg-saffron' : 'bg-ember-tint') : dark ? 'bg-cream-soft' : 'bg-cream-deep'
          }`}
        >
          {URGENCY_LABEL[review.urgency] ?? review.urgency}
        </span>
        {fromImport && (
          <span
            className={`rounded border px-1.5 py-0.5 text-xs ${
              dark ? 'border-cream-soft text-cream-soft' : 'border-ink-soft text-ink-soft'
            }`}
          >
            From import
          </span>
        )}
        <time
          className={`${dark ? 'text-cream-soft' : 'text-ink-soft'} sm:ml-auto`}
          dateTime={ts === null ? undefined : new Date(ts).toISOString()}
          title={ts === null ? undefined : new Date(ts).toLocaleString('en-GB')}
        >
          {formatArrival(ts, now)}
        </time>
      </div>
      <p className={`mt-2 max-w-prose text-[0.9375rem] leading-relaxed ${dark ? 'text-cream' : 'text-ink'}`}>
        {excerpt(review.review_text)}
      </p>
      <Link
        to={`/reviews/${hash}`}
        state={{ review }}
        aria-label={`Read and draft a reply, ${product} review`}
        className={`mt-2 inline-flex min-h-[44px] items-center text-sm font-medium underline underline-offset-4 decoration-1 ${
          dark ? 'text-saffron hover:text-cream' : 'text-ink hover:text-ink-soft'
        }`}
      >
        Read and draft a reply
      </Link>
    </li>
  )
}
