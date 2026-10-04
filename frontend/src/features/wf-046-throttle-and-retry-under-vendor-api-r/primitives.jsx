/**
 * Glyphs and the three primitives this feature needs, built here rather than
 * added to `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is
 * the collision the feature host exists to prevent, so the glyphs are passed as
 * paths through `<Icon path=...>` and the budget meter, the wait line and the
 * fact row are built in this folder. All three are findings for the integrator:
 * the budget meter in particular is generic, and promoting it into `ui.jsx` is a
 * one-off platform change belonging to whoever owns that file.
 *
 * The floor they meet, from `docs/DESIGN-SYSTEM.md`: a 44px minimum touch
 * target, a visible focus ring (inherited globally from `index.css`), a text
 * label beside every glyph so meaning survives with icons off, no emoji as an
 * icon, and no motion beyond the global `prefers-reduced-motion` rule.
 */

import { Icon } from '@/components/ui'

/**
 * A token bucket: a gauge with a notch cut out of it, which is the shape of
 * "there is a budget and something is missing from it".
 *
 * The nav glyph, passed as a path because it is not in the shared `PATHS` map and
 * that file is not ours to edit.
 */
export const THROTTLE_ICON =
  'M12 3a9 9 0 100 18 9 9 0 000-18zm0 2a7 7 0 110 14 7 7 0 010-14zm-1 3h2v3h-2V8zm0 5h2v3h-2v-3z'

/** A bucket running dry, with the drip beside it. */
export const BUCKET_ICON =
  'M4 5h16l-1.5 14a2 2 0 01-2 1.8H7.5a2 2 0 01-2-1.8L4 5zm3 2l.9 12h8.2L17 7H7zm1 3h2v3H8v-3zm0 5h2v2H8v-2z'

/** A clock with a pause bar, for a batch waiting for a person. */
export const WAIT_ICON =
  'M12 2a10 10 0 100 20 10 10 0 000-20zm0 2a8 8 0 110 16 8 8 0 010-16zm-1 3h2v5.1l3.6 2.1-1 1.7L11 13V7z'

/** A hand on a switch, for the pause control the research asks for. */
export const PAUSE_ICON =
  'M12 2a10 10 0 100 20 10 10 0 000-20zm0 2a8 8 0 110 16 8 8 0 010-16zM9 7h2v10H9V7zm4 0h2v10h-2V7z'

const ICONS = { throttle: THROTTLE_ICON, bucket: BUCKET_ICON, wait: WAIT_ICON, pause: PAUSE_ICON }

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

const BUTTON_VARIANTS = {
  primary: 'bg-accent font-semibold text-on-accent hover:brightness-110',
  secondary: 'bg-muted text-foreground hover:bg-border-subtle',
}

/**
 * A shared `Button` carrying a glyph this feature owns.
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
      className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-sm px-4 text-sm
        transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none
        disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_VARIANTS[variant] || BUTTON_VARIANTS.secondary}
        ${className}`}
      {...props}
    >
      {glyph && <Glyph name={glyph} />}
      {children}
    </button>
  )
}

/**
 * The remaining budget, as something a bar can render.
 *
 * Takes `remaining` and `total` because that is what every vendor header reports:
 * `api-usage=<used>/<total>` and `X-HubSpot-RateLimit-Remaining` are both
 * "what is left", and computing it from a used-count would mean the page
 * disagreed with the vendor's own wording about the same header.
 *
 * **A half the vendor did not send renders as "not reported", never as 0%.**
 * HubSpot's own documentation says the daily headers are absent on responses to
 * OAuth-authorised requests, so a connection that sent no daily header has an
 * unknown daily budget rather than an empty one. Drawing an empty bar there would
 * tell an operator they have spent everything when the room simply has not been
 * told.
 */
export function BudgetBar({ label, remaining, total, note, indeterminate }) {
  const known = typeof total === 'number' && total > 0 && typeof remaining === 'number'
  const width = known ? Math.max(0, Math.min(100, (remaining / total) * 100)) : 0

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {label}
        </span>
        <span className="font-mono text-sm text-foreground">
          {indeterminate ? 'not reported' : known ? `${remaining} of ${total}` : 'not reported'}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={known ? Math.round(width) : undefined}
        aria-valuetext={
          known ? `${remaining} of ${total} remaining` : 'Budget not reported by the vendor'
        }
        className="mt-2 h-2 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div
          className={`h-full rounded-sm transition-[width] duration-300 motion-reduce:transition-none ${
            known ? 'bg-accent' : 'bg-border-subtle'
          }`}
          style={known ? { width: `${width}%` } : undefined}
        />
      </div>
      {note && <p className="mt-1.5 text-xs text-muted-foreground">{note}</p>}
    </div>
  )
}

/**
 * The wait, in the words the record was written in.
 *
 * `source` is published rather than inferred: `retry_after` means the vendor
 * named the wait, `lock_floor` means the vendor's documented floor beat the
 * ladder, `backoff` means the room chose it, and `beyond_cap` means nothing is
 * scheduled and a person has to decide. Four different things, so four different
 * sentences rather than one number with a tooltip.
 */
export function WaitLine({ schedule }) {
  if (!schedule) return null
  const { seconds, source, basis } = schedule

  const headline =
    source === 'beyond_cap'
      ? 'Waiting for a person: the vendor asked for a longer wait than the room takes alone'
      : source === 'retry_after'
        ? `Waiting ${formatSeconds(seconds)} because the vendor said so`
        : source === 'lock_floor'
          ? `Waiting ${formatSeconds(seconds)} on this vendor's documented floor`
          : `Waiting ${formatSeconds(seconds)} on the room's backoff ladder`

  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 shrink-0">
        <Glyph name="wait" size={16} />
      </span>
      <div className="min-w-0">
        <p className="text-[13px] text-foreground">{headline}</p>
        {basis && <p className="mt-0.5 text-xs text-muted-foreground">{basis}</p>}
      </div>
    </div>
  )
}

/** A wait in the largest unit that is still exact enough to read. */
export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined) return 'an unscheduled wait'
  const value = Number(seconds)
  if (!Number.isFinite(value)) return 'an unscheduled wait'
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.round(value / 60)}m`
  if (value < 86400) return `${Math.round(value / 3600)}h`
  return `${Math.round(value / 86400)}d`
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

/** A titled block, with a hint underneath that says why it reads the way it does. */
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

/**
 * A table that says so when it is empty.
 *
 * A blank grid is the worst of both worlds: it looks broken and it says nothing.
 */
export function DataTable({ columns, rows, rowKey, empty }) {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-foreground">{empty}</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse text-left">
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
 * The tone a batch's state should be rendered in.
 *
 * Read from the published vocabulary rather than a list compiled into the page,
 * so a state added on the server reaches every client at once. **Never the only
 * signal**: every use of this renders the state's own word beside the colour, so
 * the floor's "no status by colour alone" rule holds.
 */
export function stateTone(vocabulary, state) {
  const entry = (vocabulary?.batch_states || []).find((row) => row.value === state)
  if (!entry) return 'neutral'
  if (state === 'complete') return 'insert'
  if (state === 'needs_action') return 'delete'
  if (state === 'deferred') return 'update'
  return 'neutral'
}

/** A plain-word label for a throttle kind, for the signal column. */
export function kindLabel(kind) {
  return (
    {
      rate_limit: 'rate limit',
      lock: 'high-volume lock',
      migration: 'migration',
      transient: 'transient fault',
      vendor_fault: 'vendor fault',
    }[kind] || kind || 'not a throttle'
  )
}

/** Whether this batch still has work the queue can do to it. */
export function isLive(batch) {
  return Boolean(batch) && !batch.terminal
}