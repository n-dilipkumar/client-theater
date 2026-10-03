/**
 * Glyphs and the three primitives this feature needs, built here rather than
 * added to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the inline notice, the labelled table and
 * the decline form are built in this folder. All three are findings for the
 * integrator: `Notice` in particular is generic enough that the next workflow
 * will want it too, and promoting it into `ui.jsx` is a one-off platform change
 * belonging to whoever owns that file.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (the global `:focus-visible` outline in `index.css`, which
 * nothing here overrides), a text label beside every glyph so meaning survives
 * with icons off, no emoji, 4.5:1 text contrast, and no motion beyond the global
 * `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

/**
 * A clock with a hand hovering over it: a slot waiting for someone to say yes.
 *
 * The `ICON` shape of "held pending a decision" is what the nav wants, and it is
 * not in the shared `PATHS` map, which is why this folder defines it and `Icon`
 * receives it as a path. `APPROVAL_ICON` is the fallback for a consumer that
 * reads only the shared name.
 */
export const APPROVAL_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 2a7 7 0 110 14 7 7 0 010-14zm-1 2.2h2V11l3.6 2.1-1 1.7L11 12.3V7.2z'

/** A hand held out, still open: the decision nobody has made. */
export const PENDING_ICON =
  'M9 11V5.5a1.5 1.5 0 013 0V11m0-1.5a1.5 1.5 0 013 0V12m0-1a1.5 1.5 0 013 0v4.5a5.5 5.5 0 01-5.5 5.5h-.7a5 5 0 01-3.9-1.9L5 15.6a1.5 1.5 0 012.3-1.9L9 15.5V8a1.5 1.5 0 00-3 0v5'

/** A tick on a booking: a calendar event that was created. */
export const CONFIRMED_ICON = 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'

/** A slot handed back: the hold a decline released. */
export const RELEASED_ICON = 'M4 4v6h6M20 20v-6h-6M20 9a8 8 0 00-14.3-3M4 15a8 8 0 0014.3 3'

const ICONS = { approval: APPROVAL_ICON, pending: PENDING_ICON, confirmed: CONFIRMED_ICON, released: RELEASED_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

const BUTTON_VARIANTS = {
  primary: 'bg-accent font-semibold text-on-accent hover:brightness-110',
  secondary: 'bg-muted text-foreground hover:bg-border-subtle',
  approve: 'bg-accent font-semibold text-on-accent hover:brightness-110',
  decline: 'border border-destructive/40 bg-destructive/15 text-destructive hover:bg-destructive/25',
}

/**
 * A button carrying a glyph this feature owns.
 *
 * The shared `Button` resolves its `icon` only through the shared `PATHS` map,
 * which is a file this feature may not edit, so the glyph is injected as a child
 * instead. `variant` and `className` are destructured out of `props` rather than
 * read off it: spreading them onto a DOM `<button>` would put an unknown
 * attribute on the element and would overwrite the computed class with the
 * caller's, which is a button that silently lost its variant.
 */
export function PathButton({ glyph, variant = 'secondary', className = '', children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-4 text-sm
        transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50
        ${BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.secondary} ${className}`}
      {...props}
    >
      {glyph && <Glyph name={glyph} />}
      {children}
    </button>
  )
}

/**
 * Inline notice for a message the user needs to act on.
 *
 * `tone` picks the semantic colour and `icon` the glyph. The title is always
 * text, never an icon on its own. A warning or a failure is announced; a
 * confirmation is not - both would be noise if every notice interrupted whatever
 * the user was doing.
 */
export function Notice({ tone = 'info', title, children, icon, onDismiss }) {
  const tones = {
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-200',
    success: 'border-accent/40 bg-accent/10 text-accent',
    warning: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
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
            className="flex min-h-11 min-w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground"
          >
            <span className="sr-only">Dismiss</span>
            <Icon name="close" size={16} />
          </button>
        )}
      </div>
    </div>
  )
}

/** A titled block, with a hint underneath it saying why it reads the way it does. */
export function Section({ title, hint, action, children }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-sm font-semibold text-foreground">{title}</h2>
          {hint && <p className="mt-1 max-w-3xl text-xs text-muted-foreground">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

/** A definition-list row. The term is always text, never a glyph on its own. */
export function Fact({ term, children, mono = false }) {
  return (
    <div>
      <dt className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{term}</dt>
      <dd className={`mt-0.5 text-[13px] text-foreground ${mono ? 'font-mono' : ''}`}>{children}</dd>
    </div>
  )
}

/**
 * A table that says so when it is empty.
 *
 * A blank grid is the worst of both worlds: it looks broken and it says nothing.
 * Every list on this page renders through here, so an empty state is always a
 * sentence explaining what would put something in it.
 */
export function DataTable({ columns, rows, rowKey, empty }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[44rem] border-collapse text-left">
        <thead>
          <tr className="border-b border-border-subtle/40">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="py-2 pr-4 text-xs font-medium tracking-wide text-muted-foreground uppercase"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-border-subtle/20 align-top">
              {columns.map((column) => (
                <td key={column.key} className="py-2.5 pr-4">
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * The decline form: a reason, or the researched default if none is given.
 *
 * Built here rather than from a `<select>` because the reason is free text and
 * the researched default is a *sentence the attendee will read*, not an internal
 * code - so it is shown as the placeholder the field starts from, and the
 * difference between "the host typed this" and "the host accepted the default"
 * survives into the record.
 *
 * The hint is always visible because placeholder-only labelling is an
 * anti-pattern in the design system: the label is text above the field and the
 * hint says what happens if it is left alone.
 */
export function ReasonField({ id, value, onChange, defaultReason }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        Reason for declining
      </label>
      <textarea
        id={id}
        rows={2}
        value={value}
        placeholder={defaultReason}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-lg border border-border-subtle/50 bg-background/60 px-3 py-2 text-sm
          text-foreground placeholder:text-muted-foreground/60 focus:border-accent"
      />
      <p className="text-xs text-muted-foreground/80">
        Optional. Left empty, the request is declined with “{defaultReason}”, which is the reason the
        vendor's own rejection payload carries.
      </p>
    </div>
  )
}

/** A single-select picker rendered from the server's vocabulary. */
export function ChoiceField({ id, label, hint, value, onChange, options }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-lg border border-border-subtle/50 bg-background/60 px-3 text-sm text-foreground focus:border-accent"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
    </div>
  )
}