import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { Store, Loader2, CheckCircle2, Info } from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import {
  beginShopifyInstall,
  getShopifyStatus,
  type ShopifyStatus,
} from '../lib/api'
import { isValidShop, normalizeShop } from '../lib/shopDomain'
import { SHOPIFY_STATE_KEY } from '../lib/shopifyState'

export default function ShopifyPage() {
  const [params] = useSearchParams()
  const [status, setStatus] = useState<ShopifyStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [shop, setShop] = useState('')
  const [touched, setTouched] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setStatus(await getShopifyStatus())
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Could not load Shopify status'))
    } finally {
      setLoading(false)
    }
  }, [])

  // eslint-disable-next-line react-hooks/set-state-in-effect -- async data-loading in effect is intentional
  useEffect(() => { load() }, [load])

  const shopInvalid = touched && !isValidShop(shop)

  async function handleConnect(e: React.FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (!isValidShop(shop)) return
    setConnecting(true)
    setConnectError(null)
    try {
      const { state, redirect_url } = await beginShopifyInstall(shop)
      sessionStorage.setItem(SHOPIFY_STATE_KEY, state)
      window.location.assign(redirect_url)
    } catch (err) {
      setConnectError(err instanceof Error ? err.message : 'Could not start the connection')
      setConnecting(false)
    }
  }

  const active = status?.installations.filter(i => !i.revoked_at) ?? []
  const connectedShop = params.get('connected')

  return (
    <Layout active="integrations">
      <div className="max-w-3xl">
        <h1 className="font-display text-2xl text-charcoal mb-1">Integrations</h1>
        <p className="text-sm text-charcoal-light font-sans mb-6">
          Connect your Shopify store to analyse the product reviews it already holds.
        </p>

        {connectedShop && (
          <div role="status" className="flex items-start gap-2 bg-green-light border border-green/20 rounded-xl p-4 mb-6">
            <CheckCircle2 size={16} className="text-green shrink-0 mt-0.5" />
            <p className="text-sm font-sans text-charcoal">
              <strong className="font-semibold">{connectedShop}</strong> is connected. Existing
              reviews are being imported in the background and will appear in your dashboard shortly.
            </p>
          </div>
        )}

        {loading && (
          <div role="status" aria-live="polite" className="flex items-center gap-2 text-sm font-sans text-charcoal-light py-10">
            <Loader2 size={16} className="animate-spin" /> Loading integration status…
          </div>
        )}

        {!loading && error && <ErrorBox error={error} onRetry={load} />}

        {!loading && !error && status && !status.enabled && (
          <div className="bg-white rounded-xl border border-gray-100 shadow-card p-6 flex gap-3">
            <Info size={18} className="text-charcoal-light shrink-0 mt-0.5" />
            <div>
              <h2 className="font-display text-lg text-charcoal mb-1">Shopify is not available yet</h2>
              <p className="text-sm text-charcoal-light font-sans">
                The Shopify connector is not switched on for this deployment. In the meantime you can
                export reviews from your store and use Upload.
              </p>
            </div>
          </div>
        )}

        {!loading && !error && status?.enabled && (
          <>
            <form onSubmit={handleConnect} noValidate className="bg-white rounded-xl border border-gray-100 shadow-card p-5 mb-6">
              <label htmlFor="shop-domain" className="block text-xs font-sans font-medium text-charcoal mb-1.5">
                Shopify store domain
              </label>
              <div className="flex gap-2">
                <input
                  id="shop-domain"
                  type="text"
                  inputMode="url"
                  autoComplete="off"
                  spellCheck={false}
                  value={shop}
                  onChange={e => setShop(e.target.value)}
                  onBlur={() => setTouched(true)}
                  placeholder="your-store.myshopify.com"
                  aria-invalid={shopInvalid}
                  aria-describedby="shop-domain-help"
                  className="flex-1 px-3 py-2 text-sm font-sans border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green/30 focus:border-green transition-shadow"
                />
                <button
                  type="submit"
                  disabled={connecting}
                  className="flex items-center gap-2 bg-green hover:bg-green-muted disabled:opacity-50 text-white text-sm font-sans font-medium py-2 px-4 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-green/40 focus:ring-offset-2"
                >
                  {connecting ? <><Loader2 size={14} className="animate-spin" /> Redirecting…</> : <><Store size={14} /> Connect Shopify</>}
                </button>
              </div>
              <p
                id="shop-domain-help"
                className={`mt-2 text-xs font-sans ${shopInvalid ? 'text-amber' : 'text-charcoal-light'}`}
                role={shopInvalid ? 'alert' : undefined}
              >
                {shopInvalid
                  ? `"${normalizeShop(shop) || shop}" is not a valid store domain. It must end in .myshopify.com.`
                  : 'Use the .myshopify.com address from your Shopify admin. We request read-only access.'}
              </p>
              {connectError && (
                <p role="alert" className="mt-3 text-xs text-amber font-sans bg-amber-light px-3 py-2 rounded-md">{connectError}</p>
              )}
            </form>

            <h2 className="font-display text-lg text-charcoal mb-3">Connected stores</h2>
            {active.length === 0 ? (
              <div className="text-center py-12 bg-white rounded-xl border border-gray-100">
                <Store size={28} className="text-charcoal-light/40 mx-auto mb-3" />
                <p className="text-sm font-sans text-charcoal-light">No store connected yet.</p>
              </div>
            ) : (
              <ul className="bg-white rounded-xl border border-gray-100 shadow-card divide-y divide-gray-100">
                {active.map(i => (
                  <li key={i.shop_domain} className="flex items-center justify-between px-5 py-3 text-sm font-sans">
                    <span className="text-charcoal font-medium">{i.shop_domain}</span>
                    <span className="flex items-center gap-1 text-xs text-green">
                      <CheckCircle2 size={12} /> Connected
                      {i.installed_at && <span className="text-charcoal-light ml-1">since {new Date(i.installed_at).toLocaleDateString()}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </Layout>
  )
}
