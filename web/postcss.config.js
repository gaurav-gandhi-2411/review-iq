// Tailwind 4 runs as its own PostCSS plugin and does vendor prefixing itself (Lightning CSS), so autoprefixer is gone.
export default {
  plugins: {
    '@tailwindcss/postcss': {},
  },
}
