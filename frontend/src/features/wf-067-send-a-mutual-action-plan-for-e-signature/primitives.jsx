/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never
 * copied. This folder holds only what that file genuinely does not export -
 * `Notice`, `Modal`, `Toggle`, `Checkbox` and `StatRow` are named in the design
 * documentation but are not in the module, so they are built here and this file
 * says so. The integrator promotes any of them that recurs; do not edit
 * `ui.jsx` to add them.
 *
 * Three rules every component here obeys, because they are the accessibility
 * floor rather than styling:
 *
 * - **No status is conveyed by colour alone.** `MilestoneBadge` renders the
 *   milestone's own label as text in every tone. A badge whose only difference
 *   is its colour tells a screen-reader user nothing at all.
 * - **Every icon sits beside a text label** or carries an `aria-label`. An icon
 *   alone is never the only label.
 * - **Every control is at least 44px tall** (`min-h-11`), because it is a target
 *   somebody has to hit with a finger.
 */

import { Badge, Card, EmptyState, Icon, inputClass } from '@/components/ui'

/**
 * The nav glyph: a document with a pen laid across it and a check in the corner -
 * a plan that has been signed. Passed as `iconPath` rather than as an `icon`
 * name, because the shared `PATHS` map is not ours to edit and this glyph is not
 * in it.
 */
export const MAP_SIGNATURE_ICON =
  'M6 3h8l4 4v14H6V3zm8 0v4h4M9 13l2 2 4-4M9 17h6'

/**
 * A short block of text that carries a tone: the invariants, a refusal reason, a
 * quote from the research. Not an error - `ErrorNote` is the error, and it is
 * used instead of this wherever the request actually failed.
 */
export function Notice({ tone = 'neutral', title, children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-foreground',
    info: 'border-border-subtle bg-accent-soft text-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
    success: 'border-success/40 bg-success/10 text-foreground',
  }
  return (
    <div className={`rounded-sm border p-4 ${tones[tone] || tones.neutral}`}>
      {title && <p className="text-sm font-semibold">{title}</p>}
      {children && <div className="mt-1 text-sm text-muted-foreground">{children}</div>}
    </div>
  )
}

/**
 * A dialog. Built here because `ui.jsx` does not export `Modal`.
 *
 * `role="dialog"` with `aria-modal` and a label, because a modal nobody can
 * identify is a modal a keyboard user cannot get out of. The backdrop is a
 * button rather than a div, so clicking it closes the dialog without needing a
 * mouse target that is not there.
 */
export function Modal({ open, title, onClose, children }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-foreground/20"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-sm border border-border-subtle bg-surface p-5"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close this dialog"
            className="min-h-11 min-w-11 rounded-sm border border-border-subtle text-muted-foreground hover:text-foreground"
          >
            <span className="sr-only">Close</span>
            <Icon name="close" size={18} className="mx-auto" />
          </button>
        </div>
        <div className="mt-4 space-y-4">{children}</div>
      </div>
    </div>
  )
}

/**
 * A labelled checkbox with a 44px target. Built here because `ui.jsx` does not
 * export `Checkbox`.
 *
 * The whole row is the label, so the click target is the row rather than the box
 * alone, and the native input stays in the DOM for keyboard and screen readers.
 */
export function Checkbox({ id, label, checked, onChange, hint }) {
  return (
    <label
      htmlFor={id}
      className="flex min-h-11 cursor-pointer items-center gap-3 rounded-sm border border-border-subtle px-3 py-2 hover:bg-muted"
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-accent"
      />
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

/**
 * A labelled text input built on the shared `inputClass`, so a new input on this
 * page cannot drift from the other hundred in the product.
 */
export function TextInput({ id, value, onChange, placeholder, type = 'text', ...rest }) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      className={inputClass}
      {...rest}
    />
  )
}

/**
 * A labelled dropdown, also on `inputClass`. The native `select` is kept rather
 * than replaced by a custom listbox, because it is keyboard- and
 * screen-reader-correct for free.
 */
export function Select({ id, value, onChange, options }) {
  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={inputClass}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

/**
 * A milestone, as a badge that always names itself.
 *
 * `label` is passed in from the server's own vocabulary rather than mapped here,
 * so a milestone this build has never heard of renders as its own name instead of
 * an empty badge.
 */
export function MilestoneBadge({ milestone, label, tone = 'neutral' }) {
  return (
    <Badge tone={tone}>
      <span className="inline-flex items-center gap-1.5">
        <Icon name="audit" size={14} />
        {label || milestone || 'unknown'}
      </span>
    </Badge>
  )
}

/**
 * A recipient's role, as a badge that always names itself.
 *
 * The approver carries its researched rule in the `title`, because that rule -
 * "Must approve before signers can sign" - is the reason the role exists and it
 * does not fit on a badge.
 */
export function RoleBadge({ role, label }) {
  return (
    <span title={role === 'APPROVER' ? 'Must approve before signers can sign' : undefined}>
      <Badge tone={role === 'APPROVER' ? 'update' : 'neutral'}>
        <span className="inline-flex items-center gap-1.5">
          <Icon name="audit" size={14} />
          {label || role}
        </span>
      </Badge>
    </span>
  )
}

/** A recipient's signing status, as text. Status is never colour alone. */
export function RecipientStatus({ status }) {
  const tone =
    {
      SIGNED: 'insert',
      APPROVED: 'insert',
      COMPLETED: 'insert',
      OPENED: 'neutral',
      UNOPENED: 'neutral',
      REJECTED: 'delete',
      EXPIRED: 'warning',
    }[status] || 'neutral'
  return (
    <Badge tone={tone}>
      <span className="font-mono">{status || 'UNOPENED'}</span>
    </Badge>
  )
}

/**
 * One row of a key/value table. Mono for the key, because a collection name, a
 * join key or a revision count is a machine value.
 */
export function Row({ label, children, mono = false }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle py-2 last:border-0">
      <span className="text-xs uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
      <span className={`min-w-0 break-all text-sm ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}>
        {children}
      </span>
    </div>
  )
}

/** A short mono value such as an id or a join key, copyable by selection. */
export function MonoValue({ children }) {
  return <span className="font-mono text-xs text-foreground">{children}</span>
}

/**
 * The empty state for a list that has nothing in it yet.
 *
 * The shared `EmptyState` inside a `Card`, so the dashed border and the copy are
 * the same everywhere in the product.
 */
export function NothingYet({ title, description, action }) {
  return (
    <Card>
      <EmptyState title={title} description={description} action={action} />
    </Card>
  )
}