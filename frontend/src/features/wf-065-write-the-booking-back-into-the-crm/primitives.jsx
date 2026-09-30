import { useState } from 'react'

import { Card, Icon } from '@/components/ui'
import Glyph, { NODE_GLYPH } from './icons'

/**
 * Three UI pieces this feature needs that the shared set in `components/ui.jsx`
 * does not export: `Notice`, `PathButton` and `StatTile`.
 *
 * They are in their own module, and apart from the glyphs, on purpose: the
 * contract tells a feature that needs a primitive the shared set lacks to build it
 * locally and report it, so this file is the whole of what an integrator has to
 * look at.
 *
 * The promotion set, stated unambiguously so an integrator knows what to take:
 *
 *   - `Notice` is already listed in `docs/FEATURE-CONTRACT.md` among the
 *     primitives a feature may use, and it is not exported there. That is a
 *     documentation/implementation gap rather than something to widen here, and it
 *     belongs in `ui.jsx` once, as platform work.
 *   - `PathButton` is a `path` prop on the shared `Button`, and the same wrapper
 *     another feature already needed. `StatTile` is the same argument on
 *     `StatCard`: every stat on this page is about something the shared set has no
 *     glyph for - an applied node, a skipped one, a failed one - and both only
 *     accept a name from the shared `PATHS` map.
 *
 * All three meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible focus ring (the global
 * `:focus-visible` rule in `index.css` does that work and is never overridden
 * here), a visible text label beside every icon, and no emoji used as an icon.
 */

const FOCUS =
  'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background'

const NOTICE_TONES = {
  info: { box: 'border-sky-500/40 bg-sky-500/10', text: 'text-sky-200', glyph: 'path' },
  warn: { box: 'border-amber-500/40 bg-amber-500/10', text: 'text-amber-200', glyph: 'path' },
  good: { box: 'border-accent/40 bg-accent/10', text: 'text-foreground', glyph: 'event' },
  bad: { box: 'border-destructive/40 bg-destructive/10', text: 'text-foreground', glyph: 'retry' },
}

/**
 * Inline note, for where the interface has to justify a constraint or report an
 * outcome.
 *
 * `role` is `alert` only for `bad`, so a screen reader interrupts for a refusal
 * and not for a hint. The meaning is always in the text, never in the colour
 * alone - which matters here more than usual, because the two things this page
 * most needs to say are "the create node has to come first" and "nothing was
 * written", and both read as amber or grey to somebody who only sees colour.
 */
export function Notice({ tone = 'info', title, children, action }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.info
  return (
    <div
      role={tone === 'bad' ? 'alert' : 'status'}
      className={`flex flex-wrap items-start justify-between gap-3 rounded-lg border p-4 ${palette.box}`}
    >
      <div className="flex min-w-0 gap-2">
        <span className="mt-0.5 shrink-0 text-muted-foreground">
          <Glyph name={palette.glyph} size={16} />
        </span>
        <div className="min-w-0">
          {title && <p className={`text-sm font-semibold ${palette.text}`}>{title}</p>}
          <div className={`text-sm text-muted-foreground ${title ? 'mt-1' : ''}`}>{children}</div>
        </div>
      </div>
      {action}
    </div>
  )
}

/**
 * The shared `Button` resolves `icon` as a *name* through the shared `PATHS` map,
 * so it cannot draw a glyph a feature supplies. Rather than re-implement `Button`
 * and put a second subtly different button in the product, the glyph goes in as a
 * child: every variant, the 44px target and the focus ring still come from the
 * shared primitive.
 */
export function PathButton({ glyph, children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg bg-muted px-4 text-sm text-foreground
        transition-colors duration-200 hover:bg-border-subtle disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS} ${props.className || ''}`}
      {...props}
    >
      {glyph && <Icon path={glyph} />}
      {children}
    </button>
  )
}

/**
 * A single headline number with its label, hint and glyph.
 *
 * Structurally the shared `StatCard`, which is the first choice. It cannot be
 * used here because every stat on this page is about something the shared set has
 * no glyph for, and `StatCard` only accepts a `name` from the shared `PATHS` map.
 * Adding a `path` prop that falls back to `name` is the platform fix, and then this
 * component is two components instead of three.
 */
export function StatTile({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {label}
          </p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        {glyph && (
          <span className="rounded-lg bg-muted p-2 text-accent">
            <Glyph name={glyph} size={20} />
          </span>
        )}
      </div>
    </Card>
  )
}

/**
 * A labelled on/off control for the Sync Meeting Type toggle.
 *
 * `role="switch"` and `aria-checked` carry the state rather than colour or the
 * thumb's position, so it reads correctly with a screen reader and in
 * forced-colours mode. `label` is required: a switch with no name says nothing
 * about which setting it belongs to, and this one decides whether *any* of the
 * flow's nodes run - and the research says the setting is org-wide, so a rep
 * cannot turn it on for their own link.
 */
export function Toggle({ checked, onChange, label, disabled = false, hint = '' }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={hint ? `${label}-hint` : undefined}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm
        transition-colors duration-200 ${FOCUS}
        disabled:cursor-not-allowed disabled:opacity-50`}
    >
      <span
        aria-hidden="true"
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border
          transition-colors duration-200 ${
            checked ? 'border-accent/40 bg-accent/25' : 'border-border-subtle/50 bg-muted'
          }`}
      >
        <span
          className={`absolute left-0.5 h-4 w-4 rounded-full transition-transform duration-200 motion-reduce:transition-none ${
            checked ? 'translate-x-5 bg-accent' : 'translate-x-0 bg-muted-foreground'
          }`}
        />
      </span>
      <span className={checked ? 'text-foreground' : 'text-muted-foreground'}>
        {checked ? 'On' : 'Off'}
      </span>
      {hint && (
        <span id={`${label}-hint`} className="sr-only">
          {hint}
        </span>
      )}
    </button>
  )
}

/**
 * One node in the declared order, with what it depends on and what became of it.
 *
 * The order is the researched feature - the create node "must precede" the rest -
 * so the number is part of the row rather than a list marker, and the node that
 * everything hangs off is marked as the anchor rather than left for the reader to
 * infer from its position.
 */
export function NodeRow({ step, index, isAnchor, onRetry, retrying }) {
  const [open, setOpen] = useState(false)
  const hasDetail = Boolean(step.message) || Object.keys(step.resolved || {}).length > 0

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        disabled={!hasDetail}
        className={`flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors
          duration-150 hover:bg-muted/40 disabled:cursor-default disabled:hover:bg-transparent ${FOCUS}`}
      >
        <span className="w-5 shrink-0 font-mono text-xs text-muted-foreground">{index + 1}</span>
        <span className="shrink-0 text-muted-foreground">
          <Glyph name={NODE_GLYPH[step.node] || 'anchor'} size={16} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[13px] text-foreground">
            {step.node}
            {isAnchor && (
              <span className="ml-2 rounded border border-accent/30 bg-accent/15 px-1.5 py-0.5 text-[10px] text-accent">
                anchor
              </span>
            )}
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {step.reason || '—'}
          </span>
        </span>
        {step.crm_id && (
          <span className="hidden shrink-0 font-mono text-[11px] text-muted-foreground sm:block">
            {step.crm_id}
          </span>
        )}
        <OutcomeTag outcome={step.outcome} />
      </button>
      {open && hasDetail && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          {step.message && <p className="text-foreground/90">{step.message}</p>}
          {step.resolved && Object.keys(step.resolved).length > 0 && (
            <div>
              <p className="font-medium tracking-wide text-muted-foreground uppercase">
                What the node resolved
              </p>
              <ul className="mt-1 space-y-0.5">
                {Object.entries(step.resolved)
                  .filter(([, value]) => value !== null && value !== '')
                  .map(([key, value]) => (
                    <li key={key} className="font-mono text-[12px]">
                      <span className="text-muted-foreground">{key}</span>
                      <span className="px-1 text-muted-foreground/60">:</span>{' '}
                      <span className="break-all text-foreground">
                        {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                      </span>
                    </li>
                  ))}
              </ul>
            </div>
          )}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              className={`inline-flex min-h-11 items-center gap-2 rounded-lg border border-border-subtle/50
                px-3 text-sm text-foreground transition-colors duration-200 hover:bg-muted
                disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS}`}
            >
              <Glyph name="retry" size={16} />
              {retrying ? 'Retrying…' : 'Retry this Event'}
            </button>
          )}
        </div>
      )}
    </li>
  )
}

function OutcomeTag({ outcome }) {
  const tones = {
    applied: 'border-accent/30 bg-accent/15 text-accent',
    skipped: 'border-border-subtle/40 bg-muted text-muted-foreground',
    failed: 'border-destructive/30 bg-destructive/15 text-destructive',
  }
  return (
    <span
      className={`shrink-0 rounded-md border px-2 py-0.5 font-mono text-xs font-medium ${tones[outcome] || tones.skipped}`}
    >
      {outcome}
    </span>
  )
}

/** One Events History row: when, which Event, and the detailed error if it failed. */
export function HistoryRow({ row, detailsAvailable, onRetry, retrying }) {
  const failed = row.status === 'failed'
  const retryable = failed && Number(row.attempt || 1) === 1
  return (
    <li className="flex flex-wrap items-start gap-3 border-b border-border-subtle/15 py-2.5 last:border-0">
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[13px] text-foreground">
          {row.guest || '—'}
          {row.is_child && (
            <span className="ml-2 rounded border border-border-subtle/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">
              child Event
            </span>
          )}
          {Number(row.attempt || 1) > 1 && (
            <span className="ml-2 rounded border border-accent/30 bg-accent/15 px-1.5 py-0.5 text-[10px] text-accent">
              attempt {row.attempt}
            </span>
          )}
        </span>
        <span className="mt-0.5 block text-xs text-muted-foreground">
          {row.meeting_type_name || '—'} · {row.node}
          {row.related_object ? ` · ${row.related_object} ${row.related_object_crm_id}` : ''}
        </span>
        {failed && row.error && (
          <span className="mt-1 block font-mono text-[12px] break-words text-destructive">
            {row.error_code ? `${row.error_code}: ` : ''}
            {row.error}
          </span>
        )}
        {!detailsAvailable && (
          <span className="mt-0.5 block text-[11px] text-muted-foreground/80">
            Event details need a global Salesforce connection; the time and the error above
            are shown either way.
          </span>
        )}
      </span>
      <span className="shrink-0 text-right">
        <span className="block font-mono text-[11px] text-muted-foreground">
          {String(row.when || '').replace('T', ' ').slice(0, 19)}
        </span>
        <span
          className={`mt-1 inline-block rounded-md border px-2 py-0.5 font-mono text-xs ${
            failed
              ? 'border-destructive/30 bg-destructive/15 text-destructive'
              : 'border-accent/30 bg-accent/15 text-accent'
          }`}
        >
          {row.status}
        </span>
      </span>
      {retryable && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className={`inline-flex min-h-11 shrink-0 items-center gap-2 rounded-lg border border-border-subtle/50
            px-3 text-sm text-foreground transition-colors duration-200 hover:bg-muted
            disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS}`}
        >
          <Glyph name="retry" size={16} />
          {retrying ? 'Retrying…' : 'Retry'}
        </button>
      )}
    </li>
  )
}

export { FOCUS }
