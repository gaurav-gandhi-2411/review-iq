import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Upload, BarChart2, LogOut, MessageSquare, Key, X, ArrowUpRight, CheckCircle2 } from 'lucide-react'
import { supabase } from '../lib/supabase'
import { getAccount, requestQuotaIncrease } from '../lib/api'
import LogoMark from './LogoMark'
import SiteLinks from './SiteLinks'

const QUOTA_WARN_THRESHOLD = 0.8

interface Props { children: React.ReactNode; active?: 'upload' | 'dashboard' | 'reviews' | 'keys' }

export default function Layout({ children, active }: Props) {
  const navigate = useNavigate()
  const [quotaBanner, setQuotaBanner] = useState<{ used: number; total: number } | null>(null)
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const [requestState, setRequestState] = useState<'idle' | 'sending' | 'sent'>('idle')

  useEffect(() => {
    getAccount().then(acc => {
      const ratio = acc.usage_this_month / acc.quota
      if (ratio >= QUOTA_WARN_THRESHOLD) {
        setQuotaBanner({ used: acc.usage_this_month, total: acc.quota })
      }
    }).catch(() => { /* non-fatal — banner is best-effort */ })
  }, [])

  async function handleRequestMore() {
    setRequestState('sending')
    try {
      await requestQuotaIncrease('Requested from quota warning banner')
      setRequestState('sent')
    } catch {
      setRequestState('idle')
    }
  }

  async function signOut() {
    await supabase.auth.signOut()
    navigate('/')
  }

  return (
    <div className="min-h-screen bg-cream text-ink">
      {quotaBanner && !bannerDismissed && (
        <div className="bg-ember-tint border-b border-ink/20 px-4 sm:px-6 py-2">
          <div className="max-w-5xl mx-auto flex items-start sm:items-center justify-between gap-4">
            <p className="text-xs font-sans text-ink">
              You've used <span className="font-semibold">{quotaBanner.used}</span> of{' '}
              <span className="font-semibold">{quotaBanner.total}</span> reviews this month.
              New uploads will be paused at the limit.
            </p>
            <div className="flex items-center gap-3 shrink-0">
              {requestState === 'sent' ? (
                <span className="flex items-center gap-1 text-xs font-sans text-ink">
                  <CheckCircle2 size={11} aria-hidden="true" /> Request sent
                </span>
              ) : (
                <button
                  onClick={handleRequestMore}
                  disabled={requestState === 'sending'}
                  className="flex items-center gap-1 text-xs font-sans text-ink underline underline-offset-2 font-medium disabled:opacity-50"
                >
                  <ArrowUpRight size={11} aria-hidden="true" />
                  {requestState === 'sending' ? 'Sending…' : 'Request higher limit'}
                </button>
              )}
              <button onClick={() => setBannerDismissed(true)} aria-label="Dismiss usage notice" className="text-ink-soft hover:text-ink">
                <X size={13} aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      )}
      <header className="bg-cream border-b border-rule">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 min-h-14 py-2 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <LogoMark size={24} />
            <span className="font-display text-lg text-ink tracking-tight truncate sr-only sm:not-sr-only">Samidha Reviews</span>
          </div>
          <nav aria-label="Main" className="flex items-center gap-0.5 sm:gap-1">
            <NavLink href="/dashboard" active={active === 'dashboard'} icon={<BarChart2 size={16} aria-hidden="true" />}>
              Dashboard
            </NavLink>
            <NavLink href="/reviews" active={active === 'reviews'} icon={<MessageSquare size={16} aria-hidden="true" />}>
              Reviews
            </NavLink>
            <NavLink href="/upload" active={active === 'upload'} icon={<Upload size={16} aria-hidden="true" />}>
              Upload
            </NavLink>
            <NavLink href="/keys" active={active === 'keys'} icon={<Key size={16} aria-hidden="true" />}>
              API keys
            </NavLink>
            <button
              onClick={signOut}
              className="ml-1 sm:ml-3 flex items-center gap-1.5 text-ink-soft hover:text-ink text-sm font-sans min-h-[44px] min-w-[44px] justify-center px-2.5 sm:px-3 rounded-md hover:bg-cream-deep transition-colors"
            >
              <LogOut size={16} aria-hidden="true" />
              <span className="sr-only sm:not-sr-only">Sign out</span>
            </button>
          </nav>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8 sm:py-10">{children}</main>
      <footer className="max-w-5xl mx-auto px-4 sm:px-6 pb-10">
        <SiteLinks />
      </footer>
    </div>
  )
}

function NavLink({
  href,
  active,
  icon,
  children,
}: {
  href: string
  active: boolean
  icon: React.ReactNode
  children: React.ReactNode
}) {
  const navigate = useNavigate()
  return (
    <button
      onClick={() => navigate(href)}
      aria-current={active ? 'page' : undefined}
      className={`flex items-center gap-1.5 text-sm font-sans min-h-[44px] min-w-[44px] justify-center px-2.5 sm:px-3 rounded-md transition-colors ${
        active
          ? 'bg-saffron text-ink font-medium'
          : 'text-ink-soft hover:text-ink hover:bg-cream-deep'
      }`}
    >
      {icon}
      <span className="sr-only sm:not-sr-only">{children}</span>
    </button>
  )
}
