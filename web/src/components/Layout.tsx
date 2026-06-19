import { useNavigate } from 'react-router-dom'
import { Upload, BarChart2, LogOut, MessageSquare, ShieldCheck } from 'lucide-react'
import { supabase } from '../lib/supabase'

interface Props { children: React.ReactNode; active?: 'upload' | 'dashboard' | 'reviews' | 'authenticity' }

export default function Layout({ children, active }: Props) {
  const navigate = useNavigate()

  async function signOut() {
    await supabase.auth.signOut()
    navigate('/')
  }

  return (
    <div className="min-h-screen bg-cream">
      <header className="bg-white border-b border-gray-100 shadow-sm">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <span className="font-display text-lg text-charcoal tracking-tight">review-iq</span>
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
