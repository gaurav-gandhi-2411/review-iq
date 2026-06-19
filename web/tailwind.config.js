/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['"DM Serif Display"', 'Georgia', 'serif'],
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
      },
      colors: {
        cream: '#F7F6F2',
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
