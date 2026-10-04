/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never
 * copied. `Badge`, `Card`, `Button`, `EmptyState`, `ErrorNote`, `Field`, `Icon`,
 * `JsonView`, `Spinner`, `StatCard`, `inputClass` and `useAsync` are all shared
 * and all imported from there. This folder holds only what that file genuinely
 * does not export, and it says so rather than quietly reimplementing a primitive
 * somebody else owns.
 *
 * Three rules every component here obeys, because they are the accessibility
 * floor rather than styling:
 *
 * - **No status is conveyed by colour alone.** Every badge renders its status as
 *   text. A badge whose only difference is its colour tells a screen-reader user
 *   nothing at all.
 * - **Every icon sits beside a text label** or carries an `aria-label`. An icon
 *   alone is never the only label.
 * - **Every control is at least 44px tall** (`min-h-11`), because it is a target
 *   somebody has to hit with a finger.
 */

import { Badge, Card, EmptyState, Icon, inputClass } from '@/components/ui'

/**
 * The nav glyph: a document with a clock across it - an agreement with a
 * deadline. Passed as `iconPath` rather than as an `icon` name, because the
 * shared `PATHS` map is not ours to edit and this glyph is not in it.
 */
export const AGREEMENT_EXPIRY_ICON =
  'M6 3h8l4 4v14H6V3zm8 0v4h4M9 13l2 2 4-4M9 17h6M14 8v3l2 1'

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
 * A labelled text input built on the shared `inputClass`, so an input on this
 * page cannot drift from the other hundred in the product.
 *
 * `min-h-11` is on every control here. It is the accessibility floor and it is
 * checked in review.
 */
export function TextInput({ id, value, onChange, type = 'text', ...rest }) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={`${inputClass} min-h-11`}
      {...rest}
    />
  )
}

/**
 * A labelled dropdown, also on `inputClass`. The native `select` is kept rather
 * than replaced by a custom listbox, because it is keyboard- and
 * screen-reader-correct for free.
 */
export function Select({ id, value, onChange, options, describedBy }) {
  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      aria-describedby={describedBy}
      className={`${inputClass} min-h-11`}
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
 * A request's status, as a badge that always names itself.
 *
 * `label` is passed in from the server's own vocabulary rather than mapped here,
 * so a status this build has never heard of renders as its own name instead of
 * an empty badge.
 */
export function StatusBadge({ status, label, tone = 'neutral' }) {
  return (
    <Badge tone={tone}>
      <span className="inline-flex items-center gap-1.5">
        <Icon name="audit" size={14} />
        <span className="font-mono">{label || status || 'unknown'}</span>
      </span>
    </Badge>
  )
}

/**
 * One signer's own status, as text. Status is never colour alone, so the
 * `status_code` itself is rendered rather than a coloured dot.
 */
export function SignerStatus({ statusCode }) {
  return (
    <Badge tone="neutral">
      <span className="font-mono">{statusCode || 'awaiting_signature'}</span>
    </Badge>
  )
}

/**
 * The banner a signer reads before signing: the deadline in their own timezone,
 * beside the count of fields still to place.
 *
 * Built here because the shape is this workflow's. The deadline is the server's
 * own reading, not a string this page formats, because the server is what knows
 * whether it could resolve the timezone. Where it could not, the note beside it
 * says so rather than showing a local time that was never computed.
 */
export function ExpiryBanner({ banner }) {
  if (!banner) {
    return (
      <p className="text-xs text-muted-foreground">
        This agreement has no expiry date, so it does not close on its own.
      </p>
    )
  }
  return (
    <div className="rounded-sm border border-border-subtle bg-muted p-3">
      <p className="text-xs uppercase tracking-[0.14em] text-muted-foreground">Expiry date</p>
      <p className="mt-1 font-mono text-sm text-foreground">{banner.local}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {banner.required_fields} required field{banner.required_fields === 1 ? '' : 's'} left to
        place
      </p>
      {banner.note && <p className="mt-1 text-xs text-muted-foreground">{banner.note}</p>}
    </div>
  )
}

/**
 * How long is left, as text rather than as a colour.
 *
 * A request with no expiry reads "no expiry", never "0 days". The research is
 * explicit that such a request does not expire, and a page showing zero days
 * next to it would be telling a seller the opposite.
 */
export function Countdown({ request }) {
  if (!request?.has_expiry) {
    return <span className="text-sm text-muted-foreground">No expiry date</span>
  }
  const days = request.days_remaining
  const late = days !== null && days < 0
  return (
    <span className={`font-mono text-sm ${late ? 'text-destructive' : 'text-foreground'}`}>
      {late ? 'Past deadline' : `${days} days left`}
    </span>
  )
}

/** One row of a key/value table. Mono for the key, because a count is a machine value. */
export function Row({ label, children, mono = false }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle py-2 last:border-0">
      <span className="text-xs uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
      <span
        className={`min-w-0 break-all text-sm ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}
      >
        {children}
      </span>
    </div>
  )
}

/** A short mono value such as an id or a join key, selectable by the reader. */
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