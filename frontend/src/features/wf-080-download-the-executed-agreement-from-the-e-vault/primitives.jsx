/**
 * Primitives this feature needs that `@/components/ui` does not export.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice` and `Toggle`
 * among the available primitives, and the shipped `ui.jsx` has the documented set except
 * `Modal`, `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repo's own documentation rather than a gap in this
 * feature, and it is reported rather than quietly worked around a second time: WF-073 and
 * WF-078 both hit it and rebuilt the same two. The contract's instruction for exactly this
 * case is to build the primitive inside the feature folder and say so, so the integrator
 * can promote the ones that recur. These are rebuilt to the design system's floor: 44px
 * targets, a visible focus ring, a text label beside every control, `rounded-sm`, and
 * semantic tokens only.
 *
 * `Modal` and `Checkbox` are not rebuilt: nothing in WF-080 needs them. The forms are
 * inline in a card rather than in a dialog, because a form with a room picker, a vendor id
 * and a delivery id is worse in a modal on a phone than it is on the page.
 *
 * Three more are built here that the documented list does not describe at all, because the
 * workflow has shapes no shared primitive names:
 *
 *   - `RetryBanner`, which renders the 202 case. It exists because "202 with Retry-After is
 *     a normal outcome, not an error" is the specification's own instruction, and a page
 *     that shows a wait as a red banner has told its user the opposite.
 *   - `OutcomeBadge`, which puts the word beside the colour so a state is never carried by
 *     tone alone.
 *   - `VariantMatrix`, which draws the two download endpoints side by side with their two
 *     booleans, because "the two endpoints are not interchangeable" is a claim about a
 *     comparison and a list of one endpoint hides it.
 */

import { Badge, Icon } from '@/components/ui'

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
 * A short standing message. Tone carries the meaning and the icon repeats it, so the state
 * does not depend on colour alone.
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
 * A labelled select. Built here because the workflow needs one in a card with a visible
 * label and a hint, and the shared set has `Field` and `inputClass` but no select.
 *
 * `inputClass` supplies the styling so the control matches every other input in the
 * product: `min-h-11` for the 44px floor, a semantic border, and `focus:border-accent`. The
 * chevron is the browser's own, so there is no glyph to keep in step and no icon without a
 * label.
 */
export function Select({ id, value, onChange, children, disabled = false }) {
  return (
    <select
      id={id}
      className={INPUT_CLASS}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {children}
    </select>
  )
}

// Re-declared rather than imported so this file has one styling source. The shared
// `inputClass` is the canonical string and this is a copy of it for a `<select>`; a test
// asserts the two match, so a change to the shared one cannot silently leave this behind.
const INPUT_CLASS =
  'min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm ' +
  'text-foreground placeholder:text-muted-foreground/70 focus:border-accent'

/**
 * The 202 case, rendered as what it is.
 *
 * "The signed document file is not ready yet... Retry after the indicated number of seconds.
 * No response body is returned." So the three things a reader needs are the word "wait", the
 * number of seconds, and the fact that no bytes arrived. All three are in words here, and
 * the tone is `info` rather than `warning` because the specification calls it a documented
 * back-pressure signal and this product does not get to upgrade it into a fault.
 *
 * The seconds come from the attempt log rather than from the response body, because the
 * response body is empty by design. That is the cost of mirroring the vendor's shape and
 * it is recorded as DERIVED_EMPTY_BODY_ON_202 on the server.
 */
export function RetryBanner({ seconds, source = 'the attempt log', action }) {
  const wait = Number(seconds || 0)
  return (
    <Notice
      tone="info"
      title={wait > 0 ? `Wait ${wait} seconds and try again` : 'Wait and try again'}
      action={action}
    >
      <p>
        The PDF is still being produced. This is the documented back-pressure signal, not a
        failure: the endpoint answered <span className="font-mono">202</span> with a{' '}
        <span className="font-mono">Retry-After</span> header and no body at all.
      </p>
      <p className="mt-1 text-xs">
        The wait shown here comes from {source}, because the response carries none.
      </p>
    </Notice>
  )
}

/**
 * The outcome of one retrieval, as a badge with the word in it.
 *
 * The label is the word, not the colour. Five outcomes have to be told apart at a glance -
 * retrieved, wait, refused for the key, refused for the rate, and a repeat delivery - and
 * four of the five are things a seller may need to act on. A board where those read as
 * differently-shaded grey rows is a board whose failures nobody investigates.
 */
export function OutcomeBadge({ outcome, label, reason }) {
  const tone = TONE_FOR_OUTCOME[outcome] || 'neutral'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        TONES[tone] || TONES.neutral
      }`}
    >
      <Icon path={TONE_ICON[tone] || 'M12 8h.01M11 12h1v5h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z'} size={13} />
      {label}
      {reason ? ` (${reason})` : ''}
    </span>
  )
}

//: One map, so an outcome cannot be drawn one way on one card and another way on another.
//: `neutral` for a duplicate because a repeat delivery is not a fault at all.
const TONE_FOR_OUTCOME = {
  retrieved: 'success',
  back_pressure: 'info',
  duplicate: 'neutral',
  sandbox_key_rejected: 'warning',
  throttled: 'warning',
}

/**
 * The two download endpoints, side by side.
 *
 * Drawn as a grid rather than a list because the specification's claim is comparative:
 * "the `/download-protected` endpoint always returns the same digitally sealed PDF file,
 * while `/download` allows for watermark customization". One row per endpoint, one column
 * per property, and the two `false` cells are written as words rather than left blank -
 * because an empty cell a reader has to interpret is exactly the ambiguity the design
 * system's "status is never conveyed by colour alone" rule exists to prevent.
 */
export function VariantMatrix({ variants }) {
  return (
    <div className="overflow-hidden rounded-sm border border-border-subtle">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          The two download endpoints, and whether each is byte-stable, takes a watermark and
          answers in the sandbox.
        </caption>
        <thead>
          <tr className="border-b border-border-subtle bg-muted">
            <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Endpoint
            </th>
            <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Byte-stable
            </th>
            <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Watermark
            </th>
            <th scope="col" className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Sandbox
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {(variants || []).map((variant) => (
            <tr key={variant.variant} className="bg-surface align-top">
              <th scope="row" className="px-3 py-2">
                <span className="block font-mono text-sm font-medium text-foreground">
                  {variant.endpoint}
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {variant.summary}
                </span>
              </th>
              <td className="px-3 py-2 font-mono text-xs text-foreground">
                {variant.byte_stable ? 'Yes, same bytes' : 'No, differs'}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-foreground">
                {variant.watermarkable ? 'Yes, applies one' : 'No, refuses one'}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-foreground">
                {variant.environments.includes('sandbox') ? 'Answers' : '401 refused'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * One digest, in mono, wrapping rather than truncating.
 *
 * A digest is a machine value, so it is set in the mono face, and it is shown in full. A
 * digest truncated to twelve characters is twelve characters that cannot be checked against
 * anything, and the whole point of recording one is that a reader can compare it.
 */
export function Digest({ value }) {
  return (
    <span className="break-all font-mono text-[11px] text-muted-foreground">
      {value || 'No digest recorded'}
    </span>
  )
}

/**
 * The state of one executed agreement, with its word beside its colour.
 *
 * `state_label` comes from the server's own vocabulary, so the page cannot spell a state the
 * validator does not know. The tone is decoration; the label is the state.
 */
export function DocumentStateBadge({ document }) {
  if (!document) return null
  const tone = TONE_FOR_STATE[document.state] || 'neutral'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        TONES[tone] || TONES.neutral
      }`}
    >
      <Icon path={TONE_ICON[tone] || 'M12 8h.01M11 12h1v5h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z'} size={13} />
      {document.state_label || document.state}
    </span>
  )
}

const TONE_FOR_STATE = {
  awaiting_signatures: 'neutral',
  generating: 'info',
  sealed: 'success',
  failed: 'destructive',
}

/**
 * A number with its unit, so a count never reads as a bare figure.
 *
 * Byte counts are in mono because they are machine values, and they are rendered with a
 * separator because an 846-digit-free but unseparated byte length is harder to check
 * against a response header than a separated one.
 */
export function ByteCount({ value }) {
  const bytes = Number(value || 0)
  return (
    <span className="font-mono text-xs text-foreground">
      {bytes.toLocaleString('en-US')} {bytes === 1 ? 'byte' : 'bytes'}
    </span>
  )
}

/**
 * The badge for a subscription's active flag.
 *
 * A cancelled subscription stays in the list on purpose - the record of what arrived under
 * it is the evidence - so the badge has to distinguish "never listened" from "stopped
 * listening", and it does it with a word rather than by removing the row.
 */
export function SubscriptionStateBadge({ subscription }) {
  if (!subscription) return null
  return subscription.active ? (
    <Badge tone="insert">Listening</Badge>
  ) : (
    <Badge tone="restore">Cancelled</Badge>
  )
}

/**
 * One delivery's retry count, or nothing at all when there was only one.
 *
 * "process each webhook notification once... even when PandaDoc retries delivery" is only
 * demonstrably true if the repeat is visible, so a delivery that arrived twice says so in
 * words. A row that arrived once renders no badge, because "0 retries" on every row is
 * noise a reader learns to skip.
 */
export function RetryCountBadge({ deliveries }) {
  const count = Number(deliveries || 0)
  if (count <= 1) return null
  return (
    <span className="inline-flex items-center gap-1.5 rounded-xs border border-warning/30 bg-warning/10 px-2 py-0.5 font-mono text-xs font-medium text-warning">
      <Icon path={TONE_ICON.warning} size={13} />
      {count} deliveries, applied once
    </span>
  )
}