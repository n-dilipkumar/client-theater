/**
 * Glyphs and two primitives this feature needs, built here rather than added to
 * `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyph is passed as a
 * path through `<Icon path=...>` and the inline `Notice` is built in this folder.
 * `Notice` in particular is generic enough that the next workflow will want it
 * too, and promoting it into `ui.jsx` is a one-off platform change belonging to
 * whoever owns that file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

/**
 * A company found in the market: a ring around a dot, like a target that has
 * been acquired rather than reached.
 *
 * The `ICON` shape of "in-market company" is what the nav wants and it is not in
 * the shared `PATHS` map, which is why this folder defines it and `Icon`
 * receives it as a path. `COMPANY_ICON` is the fallback for a consumer that
 * reads only the shared name.
 */
export const COMPANY_ICON =
  'M12 4a8 8 0 100 16 8 8 0 000-16zm0 4.5a3.5 3.5 0 110 7 3.5 3.5 0 010-7z'

/**
 * The HubSpot-ish "in your account" mark, drawn as an outlined circle with a
 * hub and three spokes.
 *
 * "Companies currently in your account will appear with a HubSpot icon." It is
 * drawn rather than borrowed from a brand kit, and it is always rendered beside
 * the words "In CRM", so the meaning does not depend on recognising a shape.
 */
export const CRM_ICON =
  'M12 3a2 2 0 110 4 2 2 0 010-4zm0 5.5a2 2 0 110 4 2 2 0 010-4zm0 5.5a2 2 0 110 4 2 2 0 010-4zM4 9a2 2 0 110 4 2 2 0 010-4zm16 0a2 2 0 110 4 2 2 0 010-4zM6 10.5l4.5 2m3-2l4.5-2'

/** A radar sweep: the tracking side, as against the company side. */
export const TRACKING_ICON = 'M12 3a9 9 0 109 9M12 7a5 5 0 105 5M12 12l7-7'

const ICONS = { company: COMPANY_ICON, crm: CRM_ICON, tracking: TRACKING_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * Inline notice for a message the user needs to act on.
 *
 * `tone` picks the semantic colour and `icon` the glyph. The title is always
 * text, never an icon on its own.
 */
export function Notice({ tone = 'info', title, children, icon, onDismiss }) {
  const tones = {
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-200',
    success: 'border-accent/40 bg-accent/10 text-accent',
    warning: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  // A warning or a failure is announced; a confirmation is not. Both would be
  // noise if every notice interrupted whatever the user was doing.
  const role = tone === 'danger' || tone === 'warning' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-lg border p-4 ${tones[tone] || tones.info}`}>
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
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground"
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
 * one, so this is built here rather than imported. It is the single most reusable
 * primitive this feature needed - the automation toggles, the filter switches and
 * the stock categories are all boxes with a label - so promoting it into `ui.jsx`
 * is a one-off platform change belonging to whoever owns that file, and the PR
 * description should say so.
 *
 * The label is the whole second column, so clicking the text toggles the box, and
 * the row is padded to the 44px minimum touch target from the design system.
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
        className="mt-0.5 h-5 w-5 shrink-0 rounded border-border-subtle bg-background/60 accent-accent disabled:cursor-not-allowed"
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

/**
 * A small labelled list of key/value pairs, for a company's About panel.
 *
 * Built here rather than in `ui.jsx` because it is a two-column definition list
 * with a monospace value column, which nothing in the shared set is shaped for.
 */
export function Facts({ rows }) {
  const present = (rows || []).filter(([, value]) => value !== undefined && value !== null && value !== '')
  if (!present.length) {
    return <p className="text-sm text-muted-foreground">Nothing recorded for this company yet.</p>
  }
  return (
    <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      {present.map(([label, value]) => (
        <div
          key={label}
          className="flex min-w-0 items-baseline justify-between gap-3 border-b border-border-subtle/30 py-1"
        >
          <dt className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</dt>
          <dd className="truncate font-mono text-[13px] text-foreground">{String(value)}</dd>
        </div>
      ))}
    </dl>
  )
}
