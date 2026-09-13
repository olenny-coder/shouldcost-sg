/**
 * The app's colour system: one palette, two themes, and the toggle that switches them.
 *
 * Every value here is the design table's, unchanged. The CSS custom properties in styles.css are
 * the single source of truth for anything the DOM paints; this module exists for the two things
 * CSS cannot reach:
 *
 *   1. Recharts. SVG attributes take literal colours, not custom properties, so the chart palette
 *      has to be handed to them as values - and had to be re-rendered when the theme changes,
 *      which is why the theme lives in React state rather than only on <html>.
 *   2. The very first paint. The theme is applied by an inline script in index.html before the
 *      bundle loads, so a dark-mode user never sees a white flash; React then adopts whatever
 *      that script decided.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

export const THEMES = ['light', 'dark']

/** localStorage key: the analyst's explicit choice, which outranks the OS preference. */
const STORAGE_KEY = 'shouldcost-theme'

/**
 * The chart series palette, in the design table's order (1-8).
 *
 * Recharts needs concrete values, so these are the table's hex codes rather than var() lookups.
 * They MUST stay in step with --chart-1..--chart-8 in styles.css; the unit test on the API side
 * cannot see them, so the theme check in .ui-check asserts the two agree by reading the computed
 * custom properties out of the DOM and comparing.
 */
export const CHART_PALETTE = {
  light: ['#0077B6', '#D97706', '#16A34A', '#DC2626', '#7C3AED', '#0891B2', '#CA8A04', '#64748B'],
  dark: ['#00C2FF', '#FFB020', '#22C55E', '#EF4444', '#A78BFA', '#22D3EE', '#FACC15', '#94A3B8'],
}

/** Semantic colours the charts need beyond the series palette. Same rule: keep in step with CSS. */
export const UI_COLORS = {
  light: {
    textPrimary: '#0F172A',
    textSecondary: '#475569',
    textMuted: '#64748B',
    border: '#CBD5E1',
    grid: '#E2E8F0',
    surface: '#FFFFFF',
    surfaceElevated: '#FFFFFF',
    primary: '#0077B6',
    accent: '#D97706',
    // The accent as SMALL TEXT: the table's accent-hover, because #D97706 on white is 3.2:1 - fine
    // for a 2px chart line, thin for an 11px label. Both are palette values; only the role differs.
    accentStrong: '#B45309',
    success: '#16A34A',
    danger: '#DC2626',
    warning: '#D97706',
    info: '#0284C7',
    chart5: '#7C3AED',
  },
  dark: {
    textPrimary: '#F8FAFC',
    textSecondary: '#CBD5E1',
    textMuted: '#94A3B8',
    border: '#334155',
    grid: '#334155',
    surface: '#111827',
    surfaceElevated: '#1E293B',
    primary: '#00C2FF',
    accent: '#FFB020',
    // Dark-mode accent as small text: 7.9:1 on the dark surface (accent itself is 10:1 on a line).
    accentStrong: '#F59E0B',
    success: '#22C55E',
    danger: '#EF4444',
    warning: '#F59E0B',
    info: '#38BDF8',
    chart5: '#A78BFA',
  },
}

/** What the OS asks for, unless the analyst has chosen. */
export function systemTheme() {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** The stored choice, or the OS preference when there is none (or storage is unavailable). */
export function initialTheme() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch (error) {
    // Private mode, or storage disabled: fall through to the OS preference.
  }
  return systemTheme()
}

/** Put the theme on <html>, which is what every CSS custom property hangs off. */
export function applyTheme(theme) {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-theme', theme)
  document.documentElement.style.colorScheme = theme
}

const ThemeContext = createContext(null)

export function ThemeProvider(props) {
  const [theme, setThemeState] = useState(function () { return initialTheme() })
  // True only while the analyst has not chosen, so an OS change still moves the app.
  const [followsSystem, setFollowsSystem] = useState(function () {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY)
      return stored !== 'light' && stored !== 'dark'
    } catch (error) {
      return true
    }
  })

  useEffect(function () { applyTheme(theme) }, [theme])

  useEffect(function () {
    if (!followsSystem || !window.matchMedia) return undefined
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    function onChange(event) { setThemeState(event.matches ? 'dark' : 'light') }
    if (query.addEventListener) query.addEventListener('change', onChange)
    else if (query.addListener) query.addListener(onChange)
    return function () {
      if (query.removeEventListener) query.removeEventListener('change', onChange)
      else if (query.removeListener) query.removeListener(onChange)
    }
  }, [followsSystem])

  const setTheme = useCallback(function (next) {
    if (THEMES.indexOf(next) === -1) return
    setThemeState(next)
    setFollowsSystem(false)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch (error) {
      // The theme still applies for this session; it just will not be remembered.
    }
  }, [])

  const value = useMemo(function () {
    return {
      theme,
      setTheme,
      followsSystem,
      toggle: function () { setTheme(theme === 'dark' ? 'light' : 'dark') },
      // Chart-ready values: the series palette plus the semantic colours SVG needs as literals.
      charts: {
        series: CHART_PALETTE[theme],
        ...UI_COLORS[theme],
      },
    }
  }, [theme, setTheme, followsSystem])

  return <ThemeContext.Provider value={value}>{props.children}</ThemeContext.Provider>
}

/** The theme, the setter, and the chart palette. Throws rather than silently defaulting. */
export function useTheme() {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme() was called outside <ThemeProvider>.')
  return value
}

/**
 * Light/dark switch. A button, not a checkbox: the label says which theme you would get, and
 * `aria-pressed` says which one you are in, so a screen reader reads the state and the action.
 */
export function ThemeToggle(props) {
  const { theme, toggle } = useTheme()
  const dark = theme === 'dark'
  const label = dark ? 'Switch to the light theme' : 'Switch to the dark theme'
  return (
    <button
      type="button"
      className={'theme-toggle' + (props.className ? ' ' + props.className : '')}
      onClick={toggle}
      aria-pressed={dark}
      aria-label={label}
      title={label + (props.hint ? ' (' + props.hint + ')' : '')}
    >
      <span className="theme-icon" aria-hidden="true">{dark ? '\u263D' : '\u2600'}</span>
      <span className="theme-label">{dark ? 'Dark' : 'Light'}</span>
    </button>
  )
}
