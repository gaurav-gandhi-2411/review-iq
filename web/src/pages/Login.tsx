import { useState } from 'react'
import { supabase } from '../lib/supabase'
import { Mail, ArrowRight, Loader2 } from 'lucide-react'

type Phase = 'idle' | 'loading' | 'sent' | 'error'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!email.trim()) return
    setPhase('loading')
    try {
      const { error } = await supabase.auth.signInWithOtp({
        email: email.trim(),
        options: { emailRedirectTo: window.location.origin },
      })
      if (error) throw error
      setPhase('sent')
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Something went wrong')
      setPhase('error')
    }
  }

  return (
    <div className="min-h-screen bg-cream flex items-center justify-center px-4">
      {/* Decorative background accent */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-32 -right-32 w-96 h-96 rounded-full bg-green-light opacity-40" />
        <div className="absolute -bottom-20 -left-20 w-64 h-64 rounded-full bg-amber-light opacity-30" />
      </div>

      <div className="relative w-full max-w-sm">
        {/* Wordmark */}
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl text-charcoal tracking-tight">review-iq</h1>
          <p className="mt-2 text-charcoal-light font-sans text-sm leading-relaxed">
            Know what your customers actually think.
          </p>
        </div>

        <div className="bg-white rounded-xl shadow-card p-8">
          {phase === 'sent' ? (
            <div className="text-center py-4">
              <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-green-light mb-4">
                <Mail size={20} className="text-green" />
              </div>
              <h2 className="font-sans font-semibold text-charcoal text-base">Check your inbox</h2>
              <p className="mt-2 text-sm text-charcoal-light font-sans">
                We sent a sign-in link to <span className="text-charcoal font-medium">{email}</span>.
                Click it to continue — no password needed.
              </p>
              <button
                onClick={() => setPhase('idle')}
                className="mt-4 text-xs text-charcoal-light hover:text-charcoal font-sans underline underline-offset-2 transition-colors"
              >
                Use a different email
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-sans font-medium text-charcoal mb-1.5">
                  Work email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@yourstore.com"
                  required
                  autoFocus
                  className="w-full px-3.5 py-2.5 text-sm font-sans border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-green/30 focus:border-green transition-shadow placeholder:text-charcoal-light/50"
                />
              </div>

              {phase === 'error' && (
                <p className="text-xs text-amber font-sans bg-amber-light px-3 py-2 rounded-md">
                  {errorMsg}
                </p>
              )}

              <button
                type="submit"
                disabled={phase === 'loading' || !email.trim()}
                className="w-full flex items-center justify-center gap-2 bg-charcoal hover:bg-charcoal/90 disabled:opacity-50 text-white text-sm font-sans font-medium py-2.5 px-4 rounded-lg transition-colors"
              >
                {phase === 'loading' ? (
                  <><Loader2 size={15} className="animate-spin" /> Sending...</>
                ) : (
                  <>Get access link <ArrowRight size={15} /></>
                )}
              </button>

              <p className="text-center text-xs text-charcoal-light font-sans">
                We'll email you a magic link — no password needed.
              </p>
            </form>
          )}
        </div>

        <p className="text-center mt-6 text-xs text-charcoal-light/60 font-sans">
          Free tier · No credit card · Your data stays yours
        </p>
      </div>
    </div>
  )
}
