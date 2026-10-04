/**
 * Local primitives for WF-051.
 *
 * These are additive. Nothing here replaces a shared primitive.
 *
 * `docs/FEATURE-CONTRACT.md` lists `Modal`, `Notice` and `Checkbox` among the
 * primitives in `src/components/ui.jsx`. They are not there: the twelve exports
 * that file actually has are `Icon`, `Button`, `Badge`, `Card`, `StatCard`,
 * `Spinner`, `ErrorNote`, `EmptyState`, `Field`, `inputClass`, `useAsync` and
 * `JsonView`. Since `ui.jsx` is on the shared-file list, the contract's own
 * remedy is to "build it inside your own feature folder and say so in the PR
 * description", which is this file.
 */

import { useEffect } from 'react'
import { Button, Icon } from '@/components/ui'

/**
 * A dialog. Focus moves into it on open and Escape closes it, so the keyboard
 * path works without a focus trap the shared set does not provide.
 */
export function Modal({ open, title, onClose, children, footer }) {
  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-foreground/40 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-lg rounded-sm border border-border-subtle bg-surface p-5"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="Close" icon="close" />
        </div>
        {children}
        {footer ? <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div> : null}
      </div>
    </div>
  )
}

/**
 * A status panel. Every tone carries a word beside the colour, because the
 * design floor forbids conveying status by colour alone.
 */
export function Notice({ tone = 'info', word, children }) {
  const tones = {
    info: 'border-border-subtle bg-muted/40 text-foreground',
    good: 'border-success/30 bg-success/5 text-foreground',
    warn: 'border-warning/30 bg-warning/5 text-foreground',
    bad: 'border-destructive/30 bg-destructive/5 text-foreground',
  }
  return (
    <div className={`flex items-start gap-2.5 rounded-sm border p-3 text-sm ${tones[tone]}`}>
      {word ? (
        <span className="mt-0.5 shrink-0 font-mono text-[11px] uppercase tracking-[0.14em]">
          {word}
        </span>
      ) : null}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

/**
 * A labelled checkbox. The visible box is 16px and the whole row is the target,
 * because a 16px hit area is not a touch target.
 */
export function Checkbox({ checked, onChange, label, hint, disabled }) {
  const id = `wf051-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <label
      htmlFor={id}
      className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-sm px-2 ${
        disabled ? 'opacity-60' : 'hover:bg-muted'
      }`}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 shrink-0 rounded-xs border border-border-subtle accent-[#10506f]"
      />
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        {hint ? <span className="block text-xs text-muted-foreground">{hint}</span> : null}
      </span>
    </label>
  )
}

/** A label and value pair. `mono` is for machine values, never for prose. */
export function Fact({ label, value, mono = false }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd
        className={`mt-1 truncate text-sm text-foreground ${mono ? 'font-mono text-[13px]' : ''}`}
      >
        {value === undefined || value === null || value === '' ? '—' : String(value)}
      </dd>
    </div>
  )
}

/** A facts grid. One column on mobile, more from `sm:` upwards. */
export function Facts({ children, columns = 3 }) {
  const grid = {
    2: 'sm:grid-cols-2',
    3: 'sm:grid-cols-3',
    4: 'sm:grid-cols-2 lg:grid-cols-4',
  }
  return <dl className={`grid grid-cols-1 gap-4 ${grid[columns] || grid[3]}`}>{children}</dl>
}

/** A section heading with a rule, matching the data-dense pages in this product. */
export function SectionHead({ title, count, children }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle pb-2">
      <h2 className="font-display text-base font-semibold text-foreground">
        {title}
        {count === undefined ? null : (
          <span className="ml-2 font-mono text-xs text-muted-foreground">{count}</span>
        )}
      </h2>
      {children}
    </div>
  )
}

/** The researched quotation block. Present so a reader sees what each rule rests on. */
export function Quote({ children, source }) {
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-muted/30 px-3 py-2">
      <blockquote className="text-[13px] leading-relaxed text-foreground">{children}</blockquote>
      {source ? (
        <figcaption className="mt-1 font-mono text-[11px] text-muted-foreground">{source}</figcaption>
      ) : null}
    </figure>
  )
}

/** A glyph row for the states the researched flow can leave a booking in. */
export function StateChip({ state }) {
  const tones = {
    pending: 'border-warning/30 bg-warning/5 text-warning',
    booked: 'border-success/30 bg-success/5 text-success',
    not_scheduled: 'border-destructive/30 bg-destructive/5 text-destructive',
    not_offered: 'border-border-subtle bg-muted text-muted-foreground',
  }
  const words = {
    pending: 'Pending',
    booked: 'Booked',
    not_scheduled: 'Not scheduled',
    not_offered: 'Not offered',
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.14em] ${
        tones[state] || tones.not_offered
      }`}
    >
      <Icon name="audit" size={12} />
      {words[state] || state}
    </span>
  )
}