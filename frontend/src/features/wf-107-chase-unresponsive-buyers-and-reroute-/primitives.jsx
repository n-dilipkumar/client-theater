/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never copied.
 * `Badge`, `Button`, `Card`, `EmptyState`, `ErrorNote`, `Field`, `Icon`, `Spinner`,
 * `StatCard`, `inputClass` and `useAsync` are all shared and all imported from there. This
 * folder holds only what that file genuinely does not export, and it says so rather than
 * quietly reimplementing a primitive somebody else owns.
 *
 * Three rules every component here obeys, because they are the accessibility floor rather
 * than styling:
 *
 * - **No status is conveyed by colour alone.** Every badge renders its status as text. A
 *   badge whose only difference is its colour tells a screen-reader user nothing.
 * - **Every icon sits beside a text label** or carries an `aria-label`. An icon alone is
 *   never the only label.
 * - **Every control is at least 44px tall** (`min-h-11`), because it is a target somebody
 *   has to hit with a finger.
 */

import { Badge, Button, Icon, inputClass } from '@/components/ui'

/**
 * The nav glyph: a speech bubble with a clock inside it, which is a conversation going
 * quiet. Passed as `iconPath` rather than as an `icon` name, because the shared `PATHS` map
 * is not ours to edit and this glyph is not in it.
 */
export const CHASE_ICON = 'M21 12a8 8 0 0 1-8 8H4l2-3.5A8 8 0 1 1 21 12zM12 8v4l3 2'

/**
 * A dialog, built here because the shared module exports no `Modal` on this branch.
 *
 * `docs/FEATURE-CONTRACT.md` says to build a missing primitive inside the feature folder
 * and say so, rather than editing `components/ui.jsx`, which is shared and which a hundred
 * branches are landing at once. Built on the shared `Button` and the same hairline the rest
 * of the product uses, so it reads as part of the system rather than as an exception.
 *
 * Focus and escape are handled because a dialog that traps nothing is not a dialog: the
 * backdrop and the close button both dismiss, and `Escape` does too.
 */
export function Dialog({ title, onClose, children, footer }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-foreground/20 p-4 sm:items-center"
      role="presentation"
      onClick={onClose}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-sm border border-border-subtle bg-surface p-5"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose()
        }}
      >
        <div className="flex items-start justify-between gap-4">
          <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
          <Button onClick={onClose}>
            <span className="sr-only">Close</span>
            <Icon name="close" size={16} />
          </Button>
        </div>
        <div className="mt-4">{children}</div>
        {footer && <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  )
}

/**
 * A short block of text that carries a tone: the researched invariants, a skip reason, a
 * refusal, a recorded gap. Not an error -- `ErrorNote` is the error and is used instead
 * wherever the request actually failed.
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
 * A duration rendered in the words a seller would use.
 *
 * The bounds are exclusive and the numbers are seconds, so a raw `900` on a page tells a
 * reader nothing about whether it is legal. This says "15 minutes" and the caller decides
 * whether to flag it, which keeps the conversion in one place instead of five.
 */
export function humaniseDuration(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value)) return 'unknown'
  if (value < 60) return `${value} second${value === 1 ? '' : 's'}`
  if (value < 3600) {
    const minutes = value / 60
    return `${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)} minute${
      minutes === 1 ? '' : 's'
    }`
  }
  if (value < 86400) {
    const hours = value / 3600
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} hour${hours === 1 ? '' : 's'}`
  }
  const days = value / 86400
  return `${Number.isInteger(days) ? days : days.toFixed(1)} day${days === 1 ? '' : 's'}`
}

/** An ISO instant as `HH:MM` or `YYYY-MM-DD HH:MM`, in the browser's own locale. */
export function humaniseInstant(value) {
  if (!value) return 'never'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'unknown'
  const date = parsed.toISOString().slice(0, 10)
  const time = parsed.toISOString().slice(11, 16)
  return `${date} ${time}`
}

/** Minutes past midnight as `HH:MM`, for the office-hours editor. */
export function humaniseMinutes(minutes) {
  if (minutes === null || minutes === undefined) return 'closed'
  const value = Number(minutes)
  if (!Number.isFinite(value)) return 'closed'
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}

/**
 * A labelled input built on the shared `inputClass`, so an input here cannot drift from
 * the other hundred in the product. `min-h-11` is the accessibility floor.
 */
export function LabeledInput({ id, label, hint, error, className = '', ...props }) {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-[13px] font-medium text-foreground">
        {label}
      </label>
      <input
        id={id}
        className={error ? `${inputClass} border-destructive` : inputClass}
        aria-invalid={error ? 'true' : undefined}
        {...props}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

/**
 * A labelled textarea, on the same footing as :func:`LabeledInput` and for the same reason.
 */
export function LabeledTextarea({ id, label, hint, error, className = '', ...props }) {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-[13px] font-medium text-foreground">
        {label}
      </label>
      <textarea
        id={id}
        rows={3}
        className={`${inputClass} py-2 ${error ? 'border-destructive' : ''}`}
        aria-invalid={error ? 'true' : undefined}
        {...props}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

/**
 * A labelled select, same footing again. `options` is a list of
 * `{ value, label }`, built from the published vocabulary rather than compiled into a page.
 */
export function LabeledSelect({ id, label, hint, error, options = [], className = '', ...props }) {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={id} className="text-[13px] font-medium text-foreground">
        {label}
      </label>
      <select
        id={id}
        className={error ? `${inputClass} border-destructive` : inputClass}
        aria-invalid={error ? 'true' : undefined}
        {...props}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

/**
 * One step in a trigger's builder list: its kind, its configured values, and a remove
 * button. Built here rather than in the page because the reroute and chase triggers render
 * the same list and two copies of it would drift.
 */
export function StepRow({ step, vocabulary, onChange, onRemove, disabled }) {
  const kindLabel = vocabulary.step_labels?.[step.kind] || step.kind
  const isHolding = (vocabulary.holding_steps || []).includes(step.kind)
  const hasDuration = (vocabulary.duration_steps || []).includes(step.kind)

  return (
    <li className="rounded-sm border border-border-subtle bg-surface p-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-[12rem] flex-1">
          <p className="text-sm font-medium text-foreground">{kindLabel}</p>
          <p className="font-mono text-xs text-muted-foreground">{step.kind}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {hasDuration && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Seconds
              <input
                type="number"
                min="31"
                className={`${inputClass} w-32`}
                value={step.duration_seconds ?? ''}
                disabled={disabled}
                onChange={(event) =>
                  onChange({
                    ...step,
                    duration_seconds:
                      event.target.value === '' ? '' : Number(event.target.value),
                  })
                }
              />
            </label>
          )}

          {step.kind === 'tag' && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Tag
              <input
                className={`${inputClass} w-44`}
                value={step.tag ?? ''}
                disabled={disabled}
                onChange={(event) => onChange({ ...step, tag: event.target.value })}
              />
            </label>
          )}

          {step.kind === 'assign' && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Inbox
              <input
                className={`${inputClass} w-44`}
                value={step.inbox ?? ''}
                disabled={disabled}
                onChange={(event) => onChange({ ...step, inbox: event.target.value })}
              />
            </label>
          )}

          <button
            type="button"
            className="min-h-11 rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
            onClick={onRemove}
            disabled={disabled}
          >
            <span className="inline-flex items-center gap-1.5">
              <Icon name="close" size={14} />
              Remove
            </span>
          </button>
        </div>
      </div>

      {(step.kind === 'message' || step.kind === 'close_message') && (
        <label className="mt-3 flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-foreground">Body</span>
          <textarea
            rows={2}
            className={`${inputClass} py-2`}
            value={step.body ?? ''}
            disabled={disabled}
            onChange={(event) => onChange({ ...step, body: event.target.value })}
          />
        </label>
      )}

      {isHolding && (
        <fieldset className="mt-3">
          <legend className="text-[13px] font-medium text-foreground">
            Cancelled by these messages
          </legend>
          <p className="mt-0.5 text-xs text-muted-foreground">
            The research names teammate and customer messages, and a wait with nothing
            selected is cancelled by nothing.
          </p>
          <div className="mt-2 flex flex-wrap gap-4">
            {(vocabulary.interruption_events || []).map((event) => (
              <label
                key={event}
                className="flex min-h-11 items-center gap-2 text-sm text-foreground"
              >
                <input
                  type="checkbox"
                  className="size-4 accent-accent"
                  disabled={disabled}
                  checked={(step.interruption_events || []).includes(event)}
                  onChange={(event) => {
                    const current = step.interruption_events || []
                    const next = event.target.checked
                      ? [...current, event]
                      : current.filter((entry) => entry !== event)
                    onChange({ ...step, interruption_events: next })
                  }}
                />
                {vocabulary.interruption_labels?.[event] || event}
              </label>
            ))}
          </div>
        </fieldset>
      )}
    </li>
  )
}

/**
 * A skip row from the sweep. The reason is rendered as its published text rather than as
 * the code alone, and the two codes that are specification rules rather than states of
 * this room are flagged, so a reader can tell "the research says this" from "this room is
 * like that".
 */
export function SkipRow({ skip, vocabulary }) {
  const isSpecRule = skip.is_specification_rule
  return (
    <li className="flex flex-wrap items-start gap-3 border-b border-border-subtle py-2 last:border-0">
      <Badge tone={isSpecRule ? 'warning' : 'neutral'}>
        {vocabulary.skip_reason_text?.[skip.reason] || skip.reason}
      </Badge>
      <span className="font-mono text-xs text-muted-foreground">{skip.reason}</span>
      {isSpecRule && <Badge tone="info">Research rule</Badge>}
      {skip.seconds_remaining !== undefined && skip.seconds_remaining !== null && (
        <span className="text-xs text-muted-foreground">
          {humaniseDuration(skip.seconds_remaining)} left
        </span>
      )}
      {skip.anchor_kind && (
        <span className="text-xs text-muted-foreground">
          measured from {vocabulary.anchor_labels?.[skip.anchor_kind] || skip.anchor_kind}
        </span>
      )}
    </li>
  )
}
