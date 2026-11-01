/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // design/tokens.json type.display / type.body.
        display: ['Fraunces', 'Georgia', 'serif'],
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
      },
      colors: {
        // Brand palette: mirrors design/tokens.json (color + derived). src/lib/tokens.test.ts
        // fails if these drift from the tokens file, so a hex here is never a free choice.
        ink: { DEFAULT: '#1C1A17', soft: '#5A5752', rule: '#403D39' },
        saffron: { DEFAULT: '#F6C042', tint: '#F9E5BB' },
        ember: { DEFAULT: '#E8823A', tint: '#F5D2B5' },
        cream: { DEFAULT: '#FAF4EA', deep: '#EDE7DD', soft: '#BCB7AF' },
        rule: '#D6D1C8',
        // Legacy palette still used by Reviews / Upload / ApiKeys / Login (not yet moved to
        // the brand tokens; the dashboard and the shared Layout do not use these).
        charcoal: '#18181B',
        'charcoal-light': '#71717A',
        green: {
          DEFAULT: '#1E6D3D',
          light: '#E8F5EE',
          muted: '#4A7C59',
        },
        amber: {
          DEFAULT: '#D4461D',
          light: '#FDF0EB',
        },
      },
      boxShadow: {
        card: '0 1px 4px rgba(0,0,0,0.08)',
        'card-hover': '0 4px 12px rgba(0,0,0,0.12)',
      },
    },
  },
  plugins: [],
}
