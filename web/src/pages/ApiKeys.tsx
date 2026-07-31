import { useEffect, useState } from 'react'
import { Key, Plus, Trash2, Copy, Check, AlertTriangle, Loader2 } from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import { getApiKeys, createApiKey, revokeApiKey, type ApiKey, type CreatedApiKey } from '../lib/api'

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const [showCreateForm, setShowCreateForm] = useState(false)
  const [name, setName] = useState('')
  const [quota, setQuota] = useState(1000)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [justCreated, setJustCreated] = useState<CreatedApiKey | null>(null)
  const [rawCopied, setRawCopied] = useState(false)

  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [confirmingId, setConfirmingId] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const data = await getApiKeys()
      setKeys(data.keys)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Could not load API keys'))
    } finally {
      setLoading(false)
    }
  }

  // eslint-disable-next-line react-hooks/set-state-in-effect -- async data-loading in effect is intentional
  useEffect(() => { load() }, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    setCreateError(null)
    try {
      const created = await createApiKey(name.trim(), quota)
      setJustCreated(created)
      setName('')
      setQuota(1000)
      setShowCreateForm(false)
      await load()
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create key')
    } finally {
      setCreating(false)
    }
  }

  async function copyRawKey() {
    if (!justCreated) return
    await navigator.clipboard.writeText(justCreated.raw_key)
    setRawCopied(true)
    setTimeout(() => setRawCopied(false), 2000)
  }

  async function handleRevoke(id: string) {
    setRevokingId(id)
    try {
      await revokeApiKey(id)
      setConfirmingId(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to revoke key'))
    } finally {
      setRevokingId(null)
    }
  }

  return (
    <Layout active="keys">
      <div className="max-w-3xl">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="font-display text-2xl text-charcoal mb-1">API keys</h1>
            <p className="text-sm text-charcoal-light font-sans">
              Manage keys used to authenticate requests to your account.
            </p>
          </div>
          <button
            onClick={() => setShowCreateForm(v => !v)}
            className="flex items-center gap-2 bg-green hover:bg-green-muted text-white text-sm font-sans font-medium py-2.5 px-4 rounded-lg transition-colors"
          >
            <Plus size={14} /> Create key
          </button>
        </div>

        {/* One-time raw key reveal */}
        {justCreated && (
          <div className="bg-amber-light border border-amber/20 rounded-xl p-5 mb-6">
            <div className="flex items-start gap-2 mb-3">
              <AlertTriangle size={16} className="text-amber shrink-0 mt-0.5" />
              <p className="text-sm font-sans text-charcoal">
                <strong className="font-semibold">Copy this key now</strong> — for your security, we won't show it again.
              </p>
            </div>
            <div className="flex items-center gap-2 bg-white rounded-lg border border-gray-200 px-3 py-2">
              <code className="flex-1 text-xs font-mono text-charcoal truncate">{justCreated.raw_key}</code>
              <button
                onClick={copyRawKey}
                className={`flex items-center gap-1.5 text-xs font-sans px-3 py-1.5 rounded-lg border transition-all shrink-0 ${
                  rawCopied
                    ? 'bg-green text-white border-green'
                    : 'bg-charcoal text-white border-charcoal hover:bg-charcoal/90'
                }`}
              >
                {rawCopied ? <><Check size={12} /> Copied!</> : <><Copy size={12} /> Copy</>}
              </button>
            </div>
            <button
              onClick={() => setJustCreated(null)}
              className="mt-3 text-xs font-sans text-charcoal-light hover:text-charcoal underline underline-offset-2 transition-colors"
            >
              I've saved it, dismiss this
            </button>
          </div>
        )}

        {/* Create form */}
        {showCreateForm && (
          <form onSubmit={handleCreate} className="bg-white rounded-xl border border-gray-100 shadow-card p-5 mb-6 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="key-name" className="block text-xs font-sans font-medium text-charcoal mb-1.5">
                  Key name
                </label>
                <input
                  id="key-name"
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="e.g. production"
                  required
                  autoFocus
                  className="w-full px-3 py-2 text-sm font-sans border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green/30 focus:border-green transition-shadow"
                />
              </div>
              <div>
                <label htmlFor="key-quota" className="block text-xs font-sans font-medium text-charcoal mb-1.5">
                  Monthly quota
                </label>
                <input
                  id="key-quota"
                  type="number"
                  min={1}
                  value={quota}
                  onChange={e => setQuota(Number(e.target.value))}
                  className="w-full px-3 py-2 text-sm font-sans border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green/30 focus:border-green transition-shadow"
                />
              </div>
            </div>
            {createError && (
              <p className="text-xs text-amber font-sans bg-amber-light px-3 py-2 rounded-md">{createError}</p>
            )}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={creating || !name.trim()}
                className="flex items-center gap-2 bg-green hover:bg-green-muted disabled:opacity-50 text-white text-sm font-sans font-medium py-2 px-4 rounded-lg transition-colors"
              >
                {creating ? <><Loader2 size={14} className="animate-spin" /> Creating…</> : 'Create key'}
              </button>
              <button
                type="button"
                onClick={() => setShowCreateForm(false)}
                className="text-sm font-sans text-charcoal-light hover:text-charcoal px-4 py-2 rounded-lg border border-gray-200 hover:border-gray-300 transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {loading && <SkeletonList />}
        {!loading && error && <ErrorBox error={error} onRetry={load} />}

        {!loading && !error && keys && keys.length === 0 && (
          <div className="text-center py-16">
            <Key size={32} className="text-charcoal-light/40 mx-auto mb-4" />
            <h2 className="font-display text-lg text-charcoal mb-2">No API keys yet</h2>
            <p className="text-sm text-charcoal-light font-sans">
              Create a key to authenticate requests to your account.
            </p>
          </div>
        )}

        {!loading && !error && keys && keys.length > 0 && (
          <div className="bg-white rounded-xl border border-gray-100 shadow-card overflow-hidden">
            <table className="w-full text-sm font-sans">
              <thead>
                <tr className="border-b border-gray-100 text-left">
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Name</th>
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Prefix</th>
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Quota</th>
                  <th className="px-5 py-3 text-xs font-medium text-charcoal-light uppercase tracking-wide">Created</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {keys.map(k => (
                  <tr key={k.id} className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors">
                    <td className="px-5 py-3 text-charcoal font-medium">{k.name}</td>
                    <td className="px-5 py-3 text-charcoal-light font-mono text-xs">{k.key_prefix}…</td>
                    <td className="px-5 py-3 text-charcoal-light">{k.quota.toLocaleString()}</td>
                    <td className="px-5 py-3 text-charcoal-light text-xs whitespace-nowrap">
                      {new Date(k.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-5 py-3 text-right">
                      {confirmingId === k.id ? (
                        <span className="flex items-center justify-end gap-2">
                          <span className="text-xs text-charcoal-light">Revoke this key?</span>
                          <button
                            onClick={() => handleRevoke(k.id)}
                            disabled={revokingId === k.id}
                            className="text-xs font-sans text-white bg-amber hover:bg-amber/90 disabled:opacity-50 px-2.5 py-1 rounded-md transition-colors"
                          >
                            {revokingId === k.id ? 'Revoking…' : 'Confirm'}
                          </button>
                          <button
                            onClick={() => setConfirmingId(null)}
                            className="text-xs font-sans text-charcoal-light hover:text-charcoal transition-colors"
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          onClick={() => setConfirmingId(k.id)}
                          className="flex items-center gap-1 text-xs font-sans text-charcoal-light hover:text-amber transition-colors ml-auto"
                        >
                          <Trash2 size={12} /> Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Layout>
  )
}

function SkeletonList() {
  return (
    <div className="space-y-2 animate-pulse">
      {[1, 2, 3].map(i => (
        <div key={i} className="h-12 bg-white rounded-lg border border-gray-100 shadow-card" />
      ))}
    </div>
  )
}
