import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import type { Session } from '@supabase/supabase-js'
import { supabase } from './lib/supabase'
import { provision } from './lib/api'
import LoginPage from './pages/Login'
import UploadPage from './pages/Upload'
import DashboardPage from './pages/Dashboard'
import ReviewsPage from './pages/Reviews'
import ReviewDetailPage from './pages/ReviewDetail'
import AuthenticityPage from './pages/Authenticity'

function AuthRouter() {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })

    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (_event, session) => {
      setSession(session)
      if (session) {
        // Ensure org is provisioned on every sign-in (idempotent on the server)
        try { await provision() } catch { /* provision errors are non-fatal — BFF handles missing org gracefully */ }
        // New sign-in → go to upload so they can add data
        if (_event === 'SIGNED_IN') navigate('/upload')
      } else {
        navigate('/')
      }
    })
    return () => subscription.unsubscribe()
  }, [navigate])

  if (loading) {
    return (
      <div className="min-h-screen bg-cream flex items-center justify-center">
        <p className="text-charcoal-light font-sans text-sm">Loading...</p>
      </div>
    )
  }

  return (
    <Routes>
      <Route path="/" element={session ? <Navigate to="/dashboard" replace /> : <LoginPage />} />
      <Route path="/upload" element={session ? <UploadPage /> : <Navigate to="/" replace />} />
      <Route path="/dashboard" element={session ? <DashboardPage /> : <Navigate to="/" replace />} />
      <Route path="/reviews" element={session ? <ReviewsPage /> : <Navigate to="/" replace />} />
      <Route path="/reviews/:reviewHash" element={session ? <ReviewDetailPage /> : <Navigate to="/" replace />} />
      <Route path="/authenticity" element={session ? <AuthenticityPage /> : <Navigate to="/" replace />} />
      <Route path="*" element={<Navigate to={session ? '/dashboard' : '/'} replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthRouter />
    </BrowserRouter>
  )
}
