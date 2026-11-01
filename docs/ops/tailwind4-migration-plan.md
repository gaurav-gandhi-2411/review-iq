# Tailwind 4 migration plan (Dependabot #106)

Deadline: the npm-audit allowlist for `braces` (GHSA-vfj7-8cjw-p6xm, dev-only via Tailwind 3) expires
2026-12-31 (`scripts/npm_audit_gate.py`, `.github/workflows/ci.yml` comment). After that date the audit
gate fails unless Tailwind 3's dependency is gone or the allowlist is renewed with a reason. Tailwind 4
removes the `braces` path; that is the reason to migrate rather than renew.

## Current surface (VERIFIED by reading `web/` on 2026-10-10)

- `tailwindcss ^3.4.19`, `postcss ^8.5.28`, `autoprefixer ^10.6.1`, `vite ^8.3.2`, React 19.
- `web/tailwind.config.js`: content globs, an extended theme (two font families, `cream`, `charcoal`,
  `charcoal-light`, `green` and `amber` colour scales, two box shadows), no plugins.
- `web/postcss.config.js`: `tailwindcss` and `autoprefixer`.
- `web/src/index.css`: 16 lines, a Google Fonts `@import url(...)`, the three `@tailwind` directives,
  a `* { box-sizing }` rule, body styles and `.font-display`.
- 12 source files use `className`; no `@apply` and no `theme()` calls found.
- Why #106 fails today: `web-build` breaks because Tailwind 4 moved the PostCSS plugin to a separate
  package (VERIFIED earlier from the CI log).

The surface is small. This is a half-day change, not a project.

## Steps (one PR, web/ only)

1. Install `tailwindcss@4` and `@tailwindcss/vite`; remove `postcss`, `autoprefixer`,
   `web/postcss.config.js`. Add the plugin to `web/vite.config.ts`. Confirm Vite 8 compatibility of the
   plugin version first (BELIEVED supported; check the peer range in `npm ls`).
2. Move the theme from `tailwind.config.js` into CSS: `@import "tailwindcss";` then an `@theme { ... }`
   block with `--font-display`, `--font-sans`, `--color-cream`, `--color-charcoal`, `--color-green`
   (and its `-light`, `-muted` variants), `--color-amber`, `--shadow-card`, `--shadow-card-hover`.
   Keep the Google Fonts `@import url(...)` first in the file (CSS requires `@import` before other rules).
   Delete `tailwind.config.js` once the build output matches.
3. Run `npx @tailwindcss/upgrade` in a throwaway branch as a cross-check of steps 1 and 2, and diff its
   output against the hand migration. Do not hand-edit generated output.
4. Audit class renames and default changes (BELIEVED from the v4 upgrade guide, confirm against it
   while doing the work): default border colour and ring width changed, some shadow/blur/rounded
   utility names were shifted down one step, `bg-opacity-*` style utilities were removed in favour of
   slash opacity. `grep` the 12 files for each.
5. Browser baseline: v4 targets modern evergreen browsers (BELIEVED Safari 16.4+, Chrome 111+,
   Firefox 128+). Check the product's audience; an Indian mobile audience can include older Android
   WebViews. If that matters, the decision is to stay on Tailwind 3 and renew the allowlist instead.

## Verification (rule 15c: this is a visible-UI change)

- Before/after screenshots of every route at 375 px and 1280 px, committed under
  `reports/screenshots/<pr>/` and embedded in the PR body. Pixel-diff the pairs; any diff over noise is
  a rename or default change from step 4.
- `npm run lint`, `npm run build`, the existing `web-build` job, and `python3 scripts/npm_audit_gate.py`
  with the `braces` entry removed from the allowlist (the proof the migration did its job).
- Bundle size before/after (`vite build` output) in the PR body.

## Gate and rollback

UI paths mean the merge gate's gate 5 applies (images or an evidenced substitute). The change is
revertable as a single commit; Vercel preview before merge. Order: do not start before #323 and the
engine PRs settle, since all three touch CI; the work is independent of Python code.

## Decision rule

Migrate by 2026-12-01 to leave a month of margin. If step 5 shows the audience needs older browsers,
record that in an ADR, renew the allowlist to a dated 2027 expiry with the reason, and close #106.
