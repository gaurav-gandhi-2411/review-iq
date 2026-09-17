import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// Session 14 P1b: lib/supabase.ts throws at module load when required VITE_* vars are
// missing/empty -- desirable in principle (fail loud, not silent) but a synchronous
// top-level throw during module evaluation leaves #root permanently empty with no
// visible error, only a browser console message a customer will never open. Checking
// here, BEFORE dynamically importing App (which transitively imports supabase.ts),
// renders a real, visible error instead of a blank page when config is missing.
const REQUIRED_ENV_VARS = ['VITE_SUPABASE_URL', 'VITE_SUPABASE_ANON_KEY', 'VITE_API_URL'] as const

function missingEnvVars(): string[] {
  return REQUIRED_ENV_VARS.filter((name) => !import.meta.env[name])
}

function renderConfigError(missing: string[]) {
  const rootEl = document.getElementById('root')!
  rootEl.innerHTML = `
    <div style="max-width:32rem;margin:4rem auto;padding:0 1.5rem;font-family:system-ui,sans-serif;color:#18181B;">
      <h1 style="font-size:1.25rem;font-weight:600;margin-bottom:0.75rem;">Samidha Reviews is misconfigured</h1>
      <p style="margin-bottom:0.5rem;">This deployment is missing required configuration and cannot start.</p>
      <p style="font-family:monospace;font-size:0.875rem;color:#52525B;">Missing: ${missing.join(', ')}</p>
      <p style="margin-top:1rem;font-size:0.875rem;color:#52525B;">This is a deployment problem, not something you can fix here. Please try again later or contact support.</p>
    </div>
  `
}

const missing = missingEnvVars()

if (missing.length > 0) {
  renderConfigError(missing)
} else {
  const { default: App } = await import('./App.tsx')
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
