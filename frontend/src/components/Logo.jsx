import React from 'react'

/**
 * The app mark.
 *
 * A descending cost curve against a reference line: the shape of the whole product, which
 * is a tendered price being tested against a benchmark. Drawn as inline SVG rather than a
 * bitmap so it stays sharp at any size, needs no network request, and can take its colour
 * from CSS. The same geometry is in index.html as the favicon, so the tab and the header
 * are recognisably the same mark.
 */
export default function Logo(props) {
  const size = props.size || 34
  const title = props.title === undefined ? 'shouldcost' : props.title
  return (
    <svg
      className={'logo' + (props.className ? ' ' + props.className : '')}
      width={size}
      height={size}
      viewBox="0 0 40 40"
      role="img"
      aria-label={title}
    >
      {title ? <title>{title}</title> : null}
      <rect width="40" height="40" rx="9" className="logo-bg" />
      {/* The benchmark reference line. */}
      <line x1="8" y1="15.5" x2="32" y2="15.5" className="logo-baseline" />
      {/* Tendered against benchmark: the curve settles onto the line. */}
      <path
        d="M8 27.5 C 13 26.5, 15 19.5, 20 17.5 S 28.5 15.5, 32 15.5"
        className="logo-curve"
        fill="none"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <circle cx="8" cy="27.5" r="2.6" className="logo-dot-start" />
      <circle cx="32" cy="15.5" r="2.6" className="logo-dot-end" />
    </svg>
  )
}
