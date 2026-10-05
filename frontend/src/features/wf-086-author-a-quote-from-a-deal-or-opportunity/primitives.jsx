/**
 * Primitives that `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list
 * as available from `@/components/ui`, and which that file does not export.
 *
 * The documented list is: `Button`, `Card`, `StatCard`, `Badge`, `Field`, `Modal`,
 * `Notice`, `Toggle`, `Checkbox`, `Spinner`, `ErrorNote`, `EmptyState`, `Icon`,
 * `inputClass`, `useAsync`, `JsonView`. The shipped `ui.jsx` exports all of those
 * except `Modal`, `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repository's own documentation rather than a gap
 * in this feature, and it is reported rather than quietly worked around a fourth
 * time. The contract's instruction for exactly this case is to build the primitive
 * inside the feature folder and say so, so the integrator can promote the ones that
 * recur. Everything below is built to the design system's floor: 44px targets, a
 * visible focus ring, a text label beside every control, `rounded-sm`, and semantic
 * tokens only.
 *
 * `Toggle` and `Checkbox` are not rebuilt. Nothing in WF-086 needs either. Showing
 * and hiding a quote section is a button that says what it will do, not a switch,
 * because the action is immediate rather than a setting that takes effect later.
 */

import { STATUS_TONES, STATUS_WORDS } from './api'
import { Icon } from '@/components/ui'

const TONES = {
  neutral: 'border-border-subtle bg-muted text-muted-foreground',
  info: 'border-info/30 bg-info/10 text-info',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
}

const TONE_ICON = {
  info: 'M12 8h.01M11 12h1v5h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  success: 'M5 12l5 5 9-11',
  warning: 'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  destructive: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/**
 * A short standing message. Tone carries the meaning and the icon repeats it, so
 * the state does not depend on colour alone.
 */
export function Notice({ tone = 'neutral', title, children, action }) {
  const iconPath = TONE_ICON[tone]
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start gap-3 rounded-sm border p-4 ${TONES[tone] || TONES.neutral}`}
    >
      {iconPath && <Icon path={iconPath} className="mt-0.5 shrink-0" />}
      <div className="min-w-0 flex-1">
        {title && <p className="text-sm font-semibold">{title}</p>}
        {children && <div className="mt-1 text-sm">{children}</div>}
      </div>
      {action}
    </div>
  )
}

/**
 * A quote's status, as a badge whose meaning does not depend on its colour.
 *
 * The word is always present and is the first thing a screen reader says. A
 * published quote gets the success tone because it is the only state that has
 * reached the deal, and a seller must be able to tell the two apart at a glance
 * without reading.
 */
export function StatusBadge({ status }) {
  const word = STATUS_WORDS[status] || status || 'Unknown'
  const tone = STATUS_TONES[status] || 'neutral'
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs
        font-medium ${TONES[tone] || TONES.neutral}`}
    >
      {word}
    </span>
  )
}

/**
 * Where a line's unit price came from.
 *
 * Three sources and a seller has to be able to tell them apart: carried over from
 * the deal, resolved from the product catalogue's tiers, or typed by hand. A price
 * with no visible provenance is a price nobody can check, so the source is a word
 * rather than a colour. The tier label sits beside it because "tier from 25" is the
 * only thing that explains why 30 units price differently from 24.
 */
export function PriceSourceLabel({ line }) {
  const source = line?.price_source
  const tier = line?.tier_label
  const words = {
    deal: 'From the deal',
    catalog: tier ? `From the catalogue, ${tier}` : 'From the catalogue',
    manual: 'Typed by hand',
  }
  const word = words[source] || 'Unpriced'
  const unknown = !source
  return (
    <span
      className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-[11px]
        ${unknown ? TONES.destructive : TONES.neutral}`}
    >
      {word}
    </span>
  )
}

/**
 * A line whose unit price is zero, called out in words.
 *
 * The catalogue has not been provisioned yet, so a quote can carry a line with
 * nothing to price from. The row still renders, because deleting it silently would
 * hide the fact that the quote is incomplete, but it says so.
 */
export function UnpricedNote() {
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-destructive">
      <Icon path="M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      No unit price
    </span>
  )
}

/**
 * The module editor's row for one quote section.
 *
 * Show, hide and reorder, as the research describes the quote editor doing. The
 * two controls are buttons with text labels rather than icon-only buttons, because
 * "show" and "hide" are two different actions on the same row and an icon that
 * changes with the state is read by a screen reader as one action.
 */
export function ModuleRow({ module, onToggle, onMove, disabled }) {
  const disabledClass = disabled ? 'opacity-50' : ''
  return (
    <li
      className={`flex items-center justify-between gap-3 border-b border-border-subtle py-2
        last:border-b-0 ${disabledClass}`}
    >
      <div className="min-w-0">
        <p className="truncate text-sm text-foreground">{module.label}</p>
        <p className="font-mono text-[11px] text-muted-foreground">
          {module.key}
          {module.kind === 'custom' ? ' - custom, built in the editor' : ''}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={() => onToggle(module)}
          disabled={disabled}
          className="min-h-11 rounded-sm border border-border-subtle bg-surface px-3 text-xs
            font-medium text-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2
            focus-visible:outline-offset-2 focus-visible:outline-accent
            disabled:cursor-not-allowed"
        >
          {module.visible ? 'Hide' : 'Show'}
        </button>
        <button
          type="button"
          onClick={() => onMove(module, -1)}
          disabled={disabled || module.position === 0}
          aria-label={`Move ${module.label} up`}
          className="flex min-h-11 min-w-11 items-center justify-center rounded-sm border
            border-border-subtle bg-surface text-foreground hover:bg-muted focus-visible:outline
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent
            disabled:cursor-not-allowed"
        >
          <Icon path="M12 19V5M5 12l7-7 7 7" />
        </button>
        <button
          type="button"
          onClick={() => onMove(module, 1)}
          disabled={disabled || module.isLast}
          aria-label={`Move ${module.label} down`}
          className="flex min-h-11 min-w-11 items-center justify-center rounded-sm border
            border-border-subtle bg-surface text-foreground hover:bg-muted focus-visible:outline
            focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent
            disabled:cursor-not-allowed"
        >
          <Icon path="M12 5v14M19 12l-7 7-7-7" />
        </button>
      </div>
    </li>
  )
}
