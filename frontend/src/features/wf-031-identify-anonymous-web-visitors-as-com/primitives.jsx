/**
 * Glyphs and two primitives this feature needs, built here rather than added to
 * `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyph is passed as a
 * path through `<Icon path=...>` and the two primitives are built in this folder.
 * Both are generic enough that the next workflow will want them too, and promoting
 * them into `ui.jsx` is a one-off platform change belonging to whoever owns that
 * file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

/**
 * A company resolved from a network: a building block with a ring around it, like
 * a footprint that has been attributed to an organisation rather than to a person.
 *
 * "exclusively company-level identification" is the whole meaning of the glyph, so
 * it is a building rather than a person, and it is always rendered beside the word
 * "company" so the meaning never depends on recognising a shape.
 */
export const COMPANY_ICON =
  'M4 21V6l7-3 7 3v15M4 21h16M9 21v-4h6v4M8 9h1M8 13h1M15 9h1M15 13h1M13 21v-4'

/**
 * A radar sweep over a grid: the tracking side, as against the company side.
 *
 * "We check the IP address, the country, the network" is a sweep, and this is the
 * glyph for the capture endpoint rather than for a record.
 */
export const SWEEP_ICON = 'M12 3a9 9 0 109 9M12 7a5 5 0 105 5M12 12l7-7M4 4h4M4 4v4'

/** A funnel: the Pages filter, which is the control this workflow is named for. */
export const FILTER_ICON = 'M4 5h16l-6 7v6l-4 2v-8L4 5z'

const ICONS = { company: COMPANY_ICON, sweep: SWEEP_ICON, filter: FILTER_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * Inline notice for a message the user needs to act on.
 *
 * `tone` picks the semantic colour and `icon` the glyph. The title is always
 * text, never an icon on its own, and a warning or a failure is announced while a
 * confirmation is not: both would be noise if every notice interrupted whatever
 * the user was doing.
 */
export function Notice({ tone = 'info', title, children, icon, onDismiss }) {
  const tones = {
    info: 'border-info/40 bg-info/10 text-info',
    success: 'border-success/40 bg-success/10 text-success',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-sm border p-4 ${tones[tone] || tones.info}`}>
      <div className="flex items-start gap-3">
        {icon && (
          <span className="mt-0.5 shrink-0">
            <Glyph name={icon} />
          </span>
        )}
        <div className="min-w-0 flex-1">
          {title && <p className="text-sm font-semibold">{title}</p>}
          <div className="mt-0.5 text-sm text-muted-foreground">{children}</div>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-sm text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground"
          >
            <span className="sr-only">Dismiss</span>
            <Glyph name="close" size={16} />
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * A labelled checkbox.
 *
 * `components/ui.jsx` names a `Checkbox` in its own docstring but does not export
 * one, so this is built here rather than imported. The label is the whole second
 * column, so clicking the text toggles the box, and the row is padded to the 44px
 * minimum touch target from the design system.
 */
export function Checkbox({ id, checked, onChange, label, hint, disabled = false }) {
  return (
    <label
      htmlFor={id}
      className={`flex min-h-11 items-start gap-3 py-1 ${disabled ? 'opacity-50' : 'cursor-pointer'}`}
    >
      <input
        id={id}
        type="checkbox"
        checked={Boolean(checked)}
        disabled={disabled}
        onChange={onChange}
        className="mt-0.5 h-5 w-5 shrink-0 rounded-xs border-border-subtle bg-surface accent-accent disabled:cursor-not-allowed"
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

/**
 * A small labelled list of key/value pairs, for one company's drill-down.
 *
 * Built here rather than in `ui.jsx` because it is a two-column definition list
 * with a monospace value column, which nothing in the shared set is shaped for.
 * A value that is absent is omitted rather than shown as a blank, because the
 * difference between "not known" and "not stored" is the difference between a
 * seller filling in a field and a bug.
 */
export function Facts({ rows, empty = 'Nothing recorded yet.' }) {
  const present = (rows || []).filter(
    ([, value]) => value !== undefined && value !== null && value !== ''
  )
  if (!present.length) {
    return <p className="text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      {present.map(([label, value]) => (
        <div
          key={label}
          className="flex min-w-0 items-baseline justify-between gap-3 border-b border-border-subtle/40 py-1"
        >
          <dt className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
            {label}
          </dt>
          <dd className="truncate font-mono text-[13px] text-foreground">{String(value)}</dd>
        </div>
      ))}
    </dl>
  )
}
