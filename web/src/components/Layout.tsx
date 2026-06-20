import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, BarChart2, LogOut, MessageSquare, ShieldCheck, X, ArrowUpRight, CheckCircle2 } from 'lucide-react'
import { supabase } from '../lib/supabase'
import { getAccount, requestQuotaIncrease } from '../lib/api'
import LogoMark from './LogoMark'

const QUOTA_WARN_THRESHOLD = 0.8

interface Props { children: React.ReactNode; active?: 'upload' | 'dashboard' | 'reviews' | 'authenticity' }

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
    <div className="min-h-screen bg-cream">
      {quotaBanner && !bannerDismissed && (
        <div className="bg-amber-light border-b border-amber/20 px-6 py-2">
          <div className="max-w-5xl mx-auto flex items-center justify-between gap-4">
            <p className="text-xs font-sans text-charcoal">
              You've used <span className="font-semibold">{quotaBanner.used}</span> of{' '}
              <span className="font-semibold">{quotaBanner.total}</span> reviews this month.
              New uploads will be paused at the limit.
            </p>
            <div className="flex items-center gap-3 shrink-0">
              {requestState === 'sent' ? (
                <span className="flex items-center gap-1 text-xs font-sans text-green">
                  <CheckCircle2 size={11} /> Request sent
                </span>
              ) : (
                <button
                  onClick={handleRequestMore}
                  disabled={requestState === 'sending'}
                  className="flex items-center gap-1 text-xs font-sans text-green hover:text-green-muted font-medium transition-colors disabled:opacity-50"
                >
                  <ArrowUpRight size={11} />
                  {requestState === 'sending' ? 'Sending…' : 'Request higher limit'}
                </button>
              )}
              <button onClick={() => setBannerDismissed(true)} className="text-charcoal-light hover:text-charcoal transition-colors">
                <X size={13} />
              </button>
            </div>
          </div>
        </div>
      )}
      <header className="bg-white border-b border-gray-100 shadow-sm">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <LogoMark size={24} />
            <span className="font-display text-lg text-charcoal tracking-tight">review-iq</span>
          </div>
          <nav className="flex items-center gap-1">
            <NavLink href="/dashboard" active={active === 'dashboard'} icon={<BarChart2 size={15} />}>
              Dashboard
            </NavLink>
            <NavLink href="/reviews" active={active === 'reviews'} icon={<MessageSquare size={15} />}>
              Reviews
            </NavLink>
            <NavLink href="/authenticity" active={active === 'authenticity'} icon={<ShieldCheck size={15} />}>
              Authenticity
            </NavLink>
            <NavLink href="/upload" active={active === 'upload'} icon={<Upload size={15} />}>
              Upload
            </NavLink>
            <button
              onClick={signOut}
              className="ml-3 flex items-center gap-1.5 text-charcoal-light hover:text-charcoal text-sm font-sans px-3 py-1.5 rounded-md hover:bg-gray-50 transition-colors"
            >
              <LogOut size={14} />
              Sign out
            </button>
          </nav>
        </div>
      </header>
      <main className="max-w-5xl mx-auto px-6 py-10">{children}</main>
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
      className={`flex items-center gap-1.5 text-sm font-sans px-3 py-1.5 rounded-md transition-colors ${
        active
          ? 'bg-green-light text-green font-medium'
          : 'text-charcoal-light hover:text-charcoal hover:bg-gray-50'
      }`}
    >
      {icon}
      {children}
    </button>
  )
}
