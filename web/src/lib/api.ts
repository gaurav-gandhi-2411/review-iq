import { supabase } from './supabase'

const API_URL = import.meta.env.VITE_API_URL as string

// ---- Error types ----
export class ServiceWarmingError extends Error {
  constructor() { super('Service is warming up. Please try again in 30 seconds.') }
}
export class QuotaError extends Error {
  constructor() { super('Monthly review limit reached. Contact support to increase.') }
}
export class BffError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// ---- Core fetch helper ----
async function getJwt(): Promise<string> {
  const { data } = await supabase.auth.getSession()
  const token = data?.session?.access_token
  if (!token) throw new BffError(401, 'Not signed in')
  return token
}

async function bff<T>(path: string, init: RequestInit = {}): Promise<T> {
  const jwt = await getJwt()
  const res = await fetch(`${API_URL}/bff${path}`, {
    ...init,
    headers: {
      'Authorization': `Bearer ${jwt}`,
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init.headers,
    },
  })
  if (res.status === 503 || res.status === 502) throw new ServiceWarmingError()
  if (res.status === 429) throw new QuotaError()
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new BffError(res.status, detail?.detail ?? `Error ${res.status}`)
  }
  return res.json()
}

// ---- Provision ----
export async function provision(): Promise<{ org_id: string; status: string }> {
  const jwt = await getJwt()
  const res = await fetch(`${API_URL}/auth/provision`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${jwt}` },
  })
  if (!res.ok) throw new BffError(res.status, 'Provision failed')
  const data = await res.json()
  // INTENTIONALLY DISCARD raw_key — it's only present on first login and is
  // never needed by the browser (all API calls go through /bff/* with the JWT).
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { raw_key: _, ...safe } = data
  return safe
}

// ---- Ingest ----
export interface IngestJob {
  job_id: string
  status: 'pending' | 'processing' | 'done' | 'failed'
  total: number
  processed: number
  failed: number
}

export async function ingestCsv(
  file: File,
  textColumn?: string,
  productColumn?: string,
): Promise<IngestJob> {
  const form = new FormData()
  form.append('file', file)
  if (textColumn) form.append('text_column', textColumn)
  if (productColumn) form.append('product_column', productColumn)
  return bff('/ingest/csv', { method: 'POST', body: form })
}

export async function pollJob(jobId: string): Promise<IngestJob> {
  return bff(`/ingest/${jobId}`)
}

// ---- Insights ----
export interface HealthScore {
  score: number
  band: 'healthy' | 'needs_attention' | 'at_risk'
  total_extractions: number
  components: {
    sentiment: { score: number; positive_count: number; total: number }
    urgency: { score: number; high_urgency_count: number; total: number }
    authenticity: { score: number; priority_review_count: number; total_audited: number }
  }
}

export interface TrendTheme {
  theme: string
  total: number
  delta_last: number
  pct_change: number | null
}

export interface TrendsData {
  themes: TrendTheme[]
}

export async function getHealthScore(): Promise<HealthScore> {
  return bff('/insights/health-score')
}

export async function getTrends(limit = 5): Promise<TrendsData> {
  return bff(`/insights/trends?limit=${limit}&bucket=month`)
}

export async function getAccount(): Promise<{ org_id: string; quota: number; usage_this_month: number }> {
  return bff('/account')
}

// ---- Types ----

export interface Review {
  id: number
  input_hash: string        // "sha256:{hex}" — strip prefix for URLs
  review_text: string       // original review text
  product: string
  stars: number | null
  stars_inferred: number | null
  sentiment: 'positive' | 'negative' | 'neutral' | 'mixed' | null
  urgency: 'low' | 'medium' | 'high'
  language: string
  pros: string[]
  cons: string[]
  topics: string[]
  competitor_mentions: string[]
  feature_requests: string[]
  buy_again: boolean | null
  review_length_chars: number | null
  confidence: number | null
  created_at: string
}

export interface ReviewsResponse {
  count: number
  offset: number
  limit: number
  results: Review[]
}

export type AuthLabel = 'genuine' | 'suspicious' | 'likely_fake'

export interface AuthenticityResult {
  score: number
  label: AuthLabel
  flags: string[]
  reasons: string
  review_hash: string
  scored_at: string
}

export type ReplyTone = 'apologetic' | 'appreciative' | 'professional' | 'warm'

export interface ReplyDraft {
  reply_text: string
  language: string
  tone: ReplyTone
  grounded_on: string[]
  caveats: string[]
  model_used: string
  drafted_at: string
}

export interface AuthInsights {
  total_audited: number
  dispositions: { clear: number; flagged_for_review: number; priority_review: number }
  disposition_rates: { clear: number; flagged_for_review: number; priority_review: number }
  review_flag_rate: number
  mean_authenticity_score: number
  signal_frequency: Array<{ signal: string; count: number }>
  flag_rate_series: Array<{ period: string; review_flag_rate: number; audited: number }>
  moderation_note: string
}

// ---- API functions ----

export async function getReviews(params: {
  limit?: number
  offset?: number
  sentiment?: string
  urgency?: string
} = {}): Promise<ReviewsResponse> {
  const q = new URLSearchParams()
  if (params.limit) q.set('limit', String(params.limit))
  if (params.offset) q.set('offset', String(params.offset))
  if (params.sentiment) q.set('sentiment', params.sentiment)
  if (params.urgency) q.set('urgency', params.urgency)
  const qs = q.toString()
  return bff(`/reviews${qs ? '?' + qs : ''}`)
}

export async function scoreAuthenticity(text: string, stars?: number | null): Promise<AuthenticityResult> {
  return bff('/authenticity', {
    method: 'POST',
    body: JSON.stringify({ text, stars: stars ?? null }),
  })
}

export async function draftReply(
  text: string,
  tone: ReplyTone,
  extraction?: object,
): Promise<ReplyDraft> {
  return bff('/reply', {
    method: 'POST',
    body: JSON.stringify({ text, tone, extraction: extraction ?? null }),
  })
}

export async function getAuthInsights(params: {
  since?: string
  until?: string
  bucket?: string
} = {}): Promise<AuthInsights> {
  const q = new URLSearchParams()
  if (params.bucket) q.set('bucket', params.bucket)
  if (params.since) q.set('since', params.since)
  if (params.until) q.set('until', params.until)
  const qs = q.toString()
  return bff(`/insights/authenticity${qs ? '?' + qs : ''}`)
}
