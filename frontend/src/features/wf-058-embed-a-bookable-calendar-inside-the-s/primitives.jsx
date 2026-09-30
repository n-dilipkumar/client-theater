/**
 * Primitives the bookable calendar page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Spinner, ErrorNote, EmptyState, inputClass, useAsync and JsonView. Two things are
 * missing for this page, and both are built here rather than added to the shared
 * file, which a feature may not edit. The integrator promotes a recurring one into
 * `ui.jsx` as platform work, once.
 */

import { Icon } from '@/components/ui'

/**
 * A slot chip: the label, and *why* it is not available when it is not.
 *
 * The reason is the whole point. A grid that greys out 11:00 with no explanation is
 * a grid a prospect cannot act on, and the four reasons this feature can produce -
 * somebody has it, somebody is holding it, the seats are gone, not enough of the
 * named people are free - each need a different next step. Colour alone would fail
 * a colour-blind reader and a black-and-white printout, so the reason is always in
 * text beside the label.
 */
const SLOT_REASONS = {
  hosts: 'the host is busy',
  held: 'held by someone else',
  booked: 'already booked',
  seats: 'no seats left',
  not_offered: 'not offered',
  unavailable: 'unavailable',
}

export function SlotChip({ slot, selected, onSelect }) {
  const reason = slot.reason
  const detail = reason ? SLOT_REASONS[reason] || SLOT_REASONS.unavailable : null
  return (
    <button
      type="button"
      onClick={() => onSelect && !slot.available && onSelect(slot)}
      disabled={!onSelect}
      aria-pressed={Boolean(selected)}
      title={detail ? `${slot.label} - ${detail}` : slot.label}
      className={`min-h-11 rounded-lg border px-3 text-sm transition-colors duration-200
        focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none
        ${
          selected
            ? 'border-accent bg-accent/20 font-semibold text-accent'
            : slot.available
              ? 'border-border-subtle/50 bg-muted/40 text-foreground hover:border-accent/50 hover:bg-muted'
              : 'cursor-not-allowed border-border-subtle/25 bg-background/30 text-muted-foreground/60'
        }`}
    >
      <span className="font-mono">{slot.label}</span>
      {!slot.available && reason && reason !== 'not_offered' && (
        <span className="sr-only"> - {detail}</span>
      )}
    </button>
  )
}

const STATE_TONES = {
  confirmed: { tone: 'insert', label: 'confirmed' },
  cancelled: { tone: 'delete', label: 'cancelled' },
  held: { tone: 'update', label: 'live hold' },
  consumed: { tone: 'insert', label: 'became a booking' },
  released: { tone: 'neutral', label: 'released' },
  expired: { tone: 'neutral', label: 'expired' },
}

/**
 * A state chip with its label in words.
 *
 * "expired" and "released" look identical as a chip, which is the point: both mean
 * the hold no longer protects its slot, and only the reason it stopped differs. The
 * label is spelled out because a hold log is exactly where a colour-only distinction
 * would be lost.
 */
export function StateChip({ state, glyph }) {
  const spec = STATE_TONES[state] || { tone: 'neutral', label: state }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${
          spec.tone === 'insert'
            ? 'border-accent/30 bg-accent/15 text-accent'
            : spec.tone === 'delete'
              ? 'border-destructive/30 bg-destructive/15 text-destructive'
              : spec.tone === 'update'
                ? 'border-sky-500/30 bg-sky-500/15 text-sky-300'
                : 'border-border-subtle/40 bg-muted text-muted-foreground'
        }`}
    >
      {glyph && <Icon path={glyph} size={13} />}
      {spec.label}
    </span>
  )
}

/**
 * A key/value pair on one line, for the detail panels.
 *
 * Not a `<dt>`/`<dd>` pair so it can be used in a flex row without a `<dl>` around
 * it, and it keeps the label from wrapping away from its value.
 */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={`min-w-0 truncate ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}>
        {children}
      </dd>
    </div>
  )
}

/** An inline note for a success message, matching the shared ErrorNote's shape. */
export function Note({ children, tone = 'ok' }) {
  return (
    <p
      role="status"
      aria-live="polite"
      className={`rounded-lg border p-3 text-sm text-foreground ${
        tone === 'warn'
          ? 'border-amber-500/40 bg-amber-500/10'
          : 'border-accent/30 bg-accent/10'
      }`}
    >
      {children}
    </p>
  )
}

/**
 * A refusal, rendered the way the refusals read.
 *
 * The API returns the documented limits with the message - the key that broke, the
 * value and the maximum - so this shows them rather than flattening the refusal to
 * one line. A page that says "could not book" and a page that says "metadata key
 * 'k' is 41 characters; the limit is 40" are very different pages for whoever has to
 * fix it.
 */
export function Refusal({ error, onRetry }) {
  if (!error) return null
  const body = error.body || {}
  const facts = []
  if (body.limit && body.maximum !== undefined) facts.push(`limit ${body.limit} = ${body.maximum}`)
  if (body.reason) facts.push(`reason ${body.reason}`)
  if (body.value !== undefined && body.value !== null && body.key === undefined) {
    facts.push(`sent ${body.value}`)
  }
  return (
    <div
      role="alert"
      className="space-y-2 rounded-lg border border-destructive/40 bg-destructive/10 p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-destructive">
            {body.error === 'scheduling_error' ? 'That booking cannot be made as written' : 'Could not load data'}
          </p>
          <p className="mt-0.5 text-sm text-foreground">{String(body.detail || error.message || error)}</p>
          {facts.length > 0 && (
            <p className="mt-1 font-mono text-xs text-muted-foreground">{facts.join(' · ')}</p>
          )}
          {body.alternatives?.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              The next free slots are{' '}
              <span className="font-mono text-foreground">
                {body.alternatives.map((slot) => slot.label || slot.start).join(', ')}
              </span>
              .
            </p>
          )}
        </div>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-muted px-4 text-sm text-foreground hover:bg-border-subtle"
          >
            <Icon name="refresh" />
            Try again
          </button>
        )}
      </div>
    </div>
  )
}
