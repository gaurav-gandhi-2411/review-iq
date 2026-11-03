import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router'
import { Loader2, AlertCircle } from 'lucide-react'
import Layout from '../components/Layout'
import { completeShopifyInstall, ShopifyApiError } from '../lib/api'
import { SHOPIFY_STATE_KEY } from '../lib/shopifyState'

// The SPA route Shopify redirects the merchant's browser to ({SHOPIFY_APP_URL}/shopify/callback).
// It forwards every query param to the API with the seller's JWT; the API verifies Shopify's
// HMAC and the user-bound state, and resolves the org from the JWT. Nothing here is trusted.
export default function ShopifyCallbackPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    // The authorization code is single-use; React StrictMode runs effects twice in dev.
    if (started.current) return
    started.current = true
    const all: Record<string, string> = {}
    params.forEach((v, k) => { all[k] = v })

    const expected = sessionStorage.getItem(SHOPIFY_STATE_KEY)
    sessionStorage.removeItem(SHOPIFY_STATE_KEY)
    if (!all.code || !all.shop || !all.state || !all.hmac) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- validation result of a one-shot effect
      setError('This page was opened without the details Shopify sends after you approve the app. Start again from Integrations.')
      return
    }
    if (expected !== all.state) {
      setError('This connection attempt did not start in this browser session, or has expired. Start again from Integrations.')
      return
    }
    completeShopifyInstall(all)
      .then(res => navigate(`/integrations/shopify?connected=${encodeURIComponent(res.shop)}`, { replace: true }))
      .catch(err => {
        setError(err instanceof ShopifyApiError ? err.message : 'Could not complete the connection. Please try again.')
      })
  }, [params, navigate])

  return (
    <Layout active="integrations">
      <div className="max-w-xl">
        {error ? (
          <div role="alert" className="bg-red-50 border border-red-100 rounded-xl p-5 flex gap-3">
            <AlertCircle size={18} className="text-red-500 shrink-0 mt-0.5" />
            <div>
              <h1 className="font-display text-lg text-charcoal mb-1">Shopify connection failed</h1>
              <p className="text-sm font-sans text-charcoal-light mb-3">{error}</p>
              <Link to="/integrations/shopify" className="text-sm font-sans text-green hover:text-green-muted font-medium underline underline-offset-2">
                Back to Integrations
              </Link>
            </div>
          </div>
        ) : (
          <div role="status" aria-live="polite" className="flex items-center gap-2 text-sm font-sans text-charcoal-light py-10">
            <Loader2 size={16} className="animate-spin" /> Finishing your Shopify connection…
          </div>
        )}
      </div>
    </Layout>
  )
}
