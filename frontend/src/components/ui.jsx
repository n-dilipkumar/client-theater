/**
 * Shared UI primitives.
 *
 * Every interactive element here meets the accessibility floor from the
 * design system: 44px minimum touch targets, visible focus, text labels
 * alongside icons, and no emoji used as an icon.
 */

import { useEffect, useState } from 'react'

/** Inline SVG icon set. Icons are decorative and hidden from assistive tech;
 *  the surrounding control always carries its own text label.
 *
 *  `path` lets a feature draw a glyph that is not in PATHS without editing this
 *  file. Features must not append to PATHS: it is shared, and a hundred features
 *  editing it is exactly the conflict the feature host exists to prevent. */
const PATHS = {
  dashboard: 'M3 3h7v9H3V3zm0 11h7v7H3v-7zm11 0h7v7h-7v-7zm0-11h7v9h-7V3z',
  rooms: 'M3 7l9-4 9 4-9 4-9-4zm0 5l9 4 9-4M3 17l9 4 9-4',
  audit: 'M9 12h6m-6 4h6M9 8h6M5 3h14a1 1 0 011 1v16a1 1 0 01-1 1H5a1 1 0 01-1-1V4a1 1 0 011-1z',
  schema: 'M4 6h16M4 12h16M4 18h10',
  plus: 'M12 5v14M5 12h14',
  refresh: 'M4 4v6h6M20 20v-6h-6M20 9a8 8 0 00-14.3-3M4 15a8 8 0 0014.3 3',
  trash: 'M3 6h18M8 6V4h8v2m-9 0v14h10V6',
  restore: 'M3 12a9 9 0 109-9 9 9 0 00-6.4 2.7L3 8m0-5v5h5',
  search: 'M11 19a8 8 0 100-16 8 8 0 000 16zm10 2l-4.35-4.35',
  close: 'M6 6l12 12M18 6L6 18',
  chevron: 'M9 6l6 6-6 6',
  database: 'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zm8-3v14c0 1.7-3.6 3-8 3s-8-1.3-8-3V5m16 7c0 1.7-3.6 3-8 3s-8-1.3-8-3',
}

export function Icon({ name, size = 18, className = '', path }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d={path || PATHS[name] || PATHS.schema} />
    </svg>
  )
}

export function Button({ children, icon, variant = 'secondary', className = '', ...props }) {
  const variants = {
    primary: 'bg-accent text-on-accent hover:brightness-110 font-semibold',
    secondary: 'bg-muted text-foreground hover:bg-border-subtle',
    ghost: 'text-muted-foreground hover:text-foreground hover:bg-muted',
    danger: 'bg-destructive/15 text-destructive hover:bg-destructive/25',
  }
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
        transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50
        ${variants[variant]} ${className}`}
      {...props}
    >
      {icon && <Icon name={icon} />}
      {children}
    </button>
  )
}

const ACTION_TONES = {
  insert: 'bg-accent/15 text-accent border-accent/30',
  update: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  delete: 'bg-destructive/15 text-destructive border-destructive/30',
  restore: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
}

export function Badge({ children, tone = 'neutral' }) {
  const tones = {
    neutral: 'bg-muted text-muted-foreground border-border-subtle/40',
    ...ACTION_TONES,
  }
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5
        font-mono text-xs font-medium ${tones[tone] || tones.neutral}`}
    >
      {children}
    </span>
  )
}

export function Card({ children, className = '' }) {
  return <div className={`glass rounded-xl p-5 ${className}`}>{children}</div>
}

export function StatCard({ label, value, hint, icon }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        {icon && (
          <span className="rounded-lg bg-muted p-2 text-accent">
            <Icon name={icon} size={20} />
          </span>
        )}
      </div>
    </Card>
  )
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 p-8 text-muted-foreground">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
      <span className="text-sm">{label}…</span>
    </div>
  )
}

export function ErrorNote({ error, onRetry }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4"
    >
      <div className="min-w-0">
        <p className="text-sm font-semibold text-destructive">Could not load data</p>
        <p className="mt-0.5 truncate text-sm text-muted-foreground">{String(error?.message || error)}</p>
      </div>
      {onRetry && (
        <Button icon="refresh" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}

export function EmptyState({ title, description, action }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border-subtle/50 p-10 text-center">
      <p className="font-mono text-sm font-semibold text-foreground">{title}</p>
      {description && <p className="max-w-md text-sm text-muted-foreground">{description}</p>}
      {action}
    </div>
  )
}

/** Labelled form field. The label is always visible: placeholder-only
 *  labelling is an anti-pattern in the design system. */
export function Field({ label, hint, children, id }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
    </div>
  )
}

export const inputClass =
  'min-h-11 w-full rounded-lg border border-border-subtle/50 bg-background/60 px-3 text-sm ' +
  'text-foreground placeholder:text-muted-foreground/60 focus:border-accent'

/** Async data hook with explicit loading/error state and a manual refetch. */
export function useAsync(loader, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    setState((prev) => ({ ...prev, loading: true, error: null }))
    loader()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false })
      })
      .catch((error) => {
        if (!cancelled) setState({ data: null, error, loading: false })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { ...state, refetch: () => setNonce((n) => n + 1) }
}

/** Render arbitrary JSON as a readable tree. The API is schema-flexible, so
 *  this must cope with any shape without knowing it in advance. */
export function JsonView({ value, depth = 0 }) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground/70">null</span>
  }
  if (typeof value !== 'object') {
    const tone = typeof value === 'number' ? 'text-accent' : typeof value === 'boolean' ? 'text-sky-300' : 'text-foreground'
    return <span className={`font-mono text-[13px] ${tone}`}>{String(value)}</span>
  }

  const entries = Array.isArray(value)
    ? value.map((item, index) => [index, item])
    : Object.entries(value)

  return (
    <ul className={depth > 0 ? 'ml-4 border-l border-border-subtle/30 pl-3' : ''}>
      {entries.map(([key, child]) => (
        <li key={key} className="py-0.5">
          <span className="font-mono text-[13px] text-muted-foreground">{key}</span>
          <span className="px-1 text-muted-foreground/60">:</span> <JsonView value={child} depth={depth + 1} />
        </li>
      ))}
    </ul>
  )
}
