const LINKS = [
  { label: 'Marketing site', href: 'https://samidhareviews.xyz' },
  { label: 'Pricing', href: 'https://samidhareviews.xyz/#pricing' },
  { label: 'API docs', href: 'https://samidhareviews.xyz/docs' },
  {
    label: 'Privacy',
    href: 'https://github.com/gaurav-gandhi-2411/review-iq/blob/main/legal/privacy-policy.md',
  },
] as const

export default function SiteLinks() {
  return (
    <nav
      aria-label="Site links"
      className="flex flex-wrap justify-center gap-x-5 gap-y-1 border-t border-gray-100 pt-5 text-xs font-sans text-charcoal-light"
    >
      {LINKS.map(l => (
        <a key={l.href} href={l.href} className="hover:text-charcoal underline-offset-2 hover:underline">
          {l.label}
        </a>
      ))}
    </nav>
  )
}
