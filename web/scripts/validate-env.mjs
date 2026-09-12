#!/usr/bin/env node
// Session 14 P1a: web/ had no build-time check that its required VITE_* vars were
// actually set. A build with empty config values succeeds silently (Vite bakes in
// empty strings), ships green, and produces a customer-facing blank page at runtime
// (see web/src/lib/supabase.ts's module-load throw, and the Cloudflare deploy this
// was found on). This runs before `tsc -b && vite build` and fails the build loudly
// instead.
//
// Uses Vite's own loadEnv() (not a bare `process.env` read) so this sees exactly what
// `vite build` will bake in -- .env/.env.local/.env.production merged with real process
// env, same precedence Vite itself uses. A bare `process.env` check would give a false
// FAIL in local dev (values live in .env.local, not the shell) and a false pass for
// anything CI sets only via a .env file it writes rather than a real env var.
//
// Keep this list in sync with web/src/lib/supabase.ts and web/src/lib/api.ts's
// `import.meta.env.VITE_*` reads (grep for `import.meta.env.VITE_` to verify).
import { loadEnv } from 'vite'

const REQUIRED_VARS = ['VITE_SUPABASE_URL', 'VITE_SUPABASE_ANON_KEY', 'VITE_API_URL']

// `vite build` defaults to mode "production" regardless of NODE_ENV -- match that
// exactly so this checks the same .env precedence the real build will use.
const env = loadEnv('production', process.cwd(), '')

const missing = REQUIRED_VARS.filter((name) => !env[name] || String(env[name]).trim() === '')

if (missing.length > 0) {
  console.error(`FAIL: missing required env var(s) for web/ build: ${missing.join(', ')}`)
  console.error(
    'A build with these empty produces a customer-facing blank page at runtime, not a build error. Set them before building.',
  )
  process.exit(1)
}

console.log(`OK: all ${REQUIRED_VARS.length} required env var(s) present.`)
