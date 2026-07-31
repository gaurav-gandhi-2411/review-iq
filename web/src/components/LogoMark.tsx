import { useId } from 'react'

interface Props { size?: number }

export default function LogoMark({ size = 32 }: Props) {
  // Gradient ids must be unique per instance -- two LogoMarks on the same
  // page would otherwise collide on `url(#log-dark)` etc. and only the
  // first one's gradient would render.
  const uid = useId()
  const logDark = `log-dark-${uid}`
  const logLight = `log-light-${uid}`
  const flame = `flame-${uid}`

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={logDark} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#5A3420" />
          <stop offset="100%" stopColor="#8A4B26" />
        </linearGradient>
        <linearGradient id={logLight} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#6B3A1F" />
          <stop offset="100%" stopColor="#A8562A" />
        </linearGradient>
        <linearGradient id={flame} x1="0%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stopColor="#FF6B35" />
          <stop offset="55%" stopColor="#FFB627" />
          <stop offset="100%" stopColor="#FFF4D6" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7" fill="#14110F" />
      <g strokeLinecap="round" fill="none">
        <line x1="17.82" y1="22.15" x2="11.32" y2="10.89" strokeWidth="5.2" stroke={`url(#${logDark})`} />
        <line x1="16.58" y1="22.31" x2="14.50" y2="10.49" strokeWidth="4.2" stroke={`url(#${logLight})`} />
        <line x1="15.42" y1="22.31" x2="17.50" y2="10.49" strokeWidth="4.2" stroke={`url(#${logLight})`} />
        <line x1="14.18" y1="22.15" x2="20.68" y2="10.89" strokeWidth="5.2" stroke={`url(#${logDark})`} />
      </g>
      <path d="M16,7.0 C17.6,9.3 18.6,11.0 18.6,12.9 C18.6,15.3 17,16.7 16,16.7 C15,16.7 13.4,15.3 13.4,12.9 C13.4,11.8 13.9,10.8 14.6,9.9 C14.7,11.1 15.2,11.7 15.7,11.7 C16.3,11.7 16.7,11.0 16.5,10.1 C16.3,9.1 15.8,8.2 16,7.0 Z" fill={`url(#${flame})`} />
    </svg>
  )
}
