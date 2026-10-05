/**
 * Primitives this feature needs that `@/components/ui` does not export.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice` and `Toggle` among
 * the available primitives, and the shipped `ui.jsx` has the documented set except `Modal`,
 * `Notice`, `Toggle` and `Checkbox`. That is a contradiction in the repo's own documentation
 * rather than a gap in this feature, and it is reported rather than quietly worked around a
 * second time: WF-073, WF-078 and WF-080 all hit it and rebuilt the same one. The contract's
 * instruction for exactly this case is to build the primitive inside the feature folder and say
 * so, so the integrator can promote the recurring one once. `Notice` is rebuilt to the design
 * system's floor: 44px targets, a visible focus ring, a text label beside every control,
 * `rounded-sm`, and semantic tokens only.
 *
 * `Modal` and `Checkbox` are not rebuilt: nothing in WF-082 needs them. The forms are inline in
 * a card rather than in a dialog, because a form with a callback URL, an API key and an event id
 * is worse in a modal on a phone than it is on the page.
 *
 * Three more are built here that the documented list does not describe at all, because this
 * workflow has shapes no shared primitive names:
 *
 *   - `CheckLadder`, which draws the three checks and their order. "Verifies authenticity three
 *     ways... Only then does the handler act" is a claim about a sequence, and a list of three
 *     unrelated rows hides the sequence. The first check that failed is the one that refused, so
 *     the ladder marks it and names it.
 *   - `RetryLadderTable`, which draws the six intervals and the cumulative time. The retry
 *     contract exists so a consumer can schedule from it, and a schedule needs the numbers.
 *   - `StateBadge`, which puts the word beside the colour, so a state is never carried by tone
 *     alone.
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
 * A labelled select. Built here because the workflow needs one in a card with a visible label
 * and a hint, and the shared set has `Field` and `inputClass` but no select.
 *
 * `inputClass` supplies the styling so the control matches every other input in the product:
 * `min-h-11` for the 44px floor, a semantic border, and `focus:border-accent`. The chevron is
 * the browser's own, so there is no glyph to keep in step and no icon without a label.
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

/**
 * The shared `inputClass`, re-declared for a `<select>`.
 *
 * Exported so a test can compare it against the original. A hand-copied styling string is the
 * one thing in this folder that can silently drift from the shared set, and an exported copy is
 * what makes the comparison possible. Do not edit one without the other.
 */
export const INPUT_CLASS =
  'min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm ' +
  'text-foreground placeholder:text-muted-foreground/70 focus:border-accent'

/**
 * The three checks, in the order they run, with the one that refused marked.
 *
 * The order is the specification's, not this page's: "verifies authenticity three ways...
 * Only then does the handler act." Each row names what the check covers, because the two digests
 * cover different bytes and a reader who cannot tell them apart cannot tell which one refused.
 *
 * The row that failed is marked in words and by an icon, never by colour alone. `failedCheck`
 * is the name the server sent, so the marker cannot disagree with the refusal it is marking.
 */
export function CheckLadder({ checks = [], failedCheck = null }) {
  if (!Array.isArray(checks) || checks.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        The three checks run when a delivery arrives. None has run yet.
      </p>
    )
  }
  return (
    <ol className="space-y-2">
      {checks.map((check, index) => {
        const failed = check.passed === false
        const tone = failed ? 'destructive' : 'success'
        return (
          <li
            key={check.check || index}
            className={`flex flex-wrap items-start gap-3 rounded-sm border p-3 ${
              failed ? TONES.destructive : 'border-border-subtle bg-surface'
            }`}
          >
            <span
              className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs ${
                failed ? TONES.destructive : TONES.success
              }`}
              aria-hidden="true"
            >
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                <span className="font-mono">{check.check}</span>
                <Icon path={TONE_ICON[tone]} size={13} />
                {failed
                  ? `Refused here${failedCheck ? ` (${failedCheck})` : ''}`
                  : 'Passed'}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">{describeCheck(check)}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/**
 * What one check covers and what it found, in words.
 *
 * The two digests carry different bytes and a page that renders them as two rows of the same
 * thing teaches the reader they are interchangeable. Each row therefore says what it covers:
 * the source address, the whole JSON payload, or `event_time` joined to `event_type`.
 */
function describeCheck(check) {
  switch (check.check) {
    case 'ip_allowlist':
      return check.allowed_range
        ? `The source address is inside the published range file (${check.allowed_range}).`
        : `The source address is not in the published range file. ${check.range_count ?? 0} range(s) held.`
    case 'content_sha256':
      return check.passed
        ? `Content-Sha256 matches the payload bytes as received. The digest was ${check.expected}.`
        : 'Content-Sha256 does not match the payload bytes this handler received.'
    case 'event_hash':
      return check.passed
        ? `event_hash matches HMAC-SHA256 over event_time joined to event_type, with no separator (${check.encoding}).`
        : 'event_hash does not match HMAC-SHA256 over event_time joined to event_type.'
    default:
      return check.reason || ''
  }
}

/**
 * The retry ladder, six intervals, with the time each one lands at.
 *
 * The specification says "we will retry POSTing the event up to 6 times, with each retry
 * interval being longer than the previous one" and then gives the numbers. A consumer
 * scheduling retries needs both the interval and the cumulative time, because the last retry
 * lands more than a day after the first attempt.
 */
export function RetryLadderTable({ ladder = [] }) {
  if (!Array.isArray(ladder) || ladder.length === 0) {
    return <p className="text-sm text-muted-foreground">The retry ladder has not loaded.</p>
  }
  return (
    <div className="overflow-x-auto rounded-sm border border-border-subtle">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          The six retry intervals, and the cumulative time from the first attempt to each one.
        </caption>
        <thead>
          <tr className="border-b border-border-subtle bg-muted">
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              Attempt
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              Waits
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
            >
              Lands at
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {ladder.map((row) => (
            <tr key={row.attempt} className="bg-surface">
              <th scope="row" className="px-3 py-2 font-mono text-sm text-foreground">
                {row.attempt}
              </th>
              <td className="px-3 py-2 font-mono text-xs text-foreground">
                {formatSeconds(row.interval_seconds)}
              </td>
              <td className="px-3 py-2 text-sm text-muted-foreground">{row.cumulative_label}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Seconds as a human delay, ASCII only so it survives a Windows console. */
export function formatSeconds(seconds) {
  const total = Math.max(0, Number(seconds || 0))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  const parts = []
  if (hours) parts.push(`${hours} h`)
  if (minutes) parts.push(`${minutes} m`)
  if (rest || !parts.length) parts.push(`${rest} s`)
  return parts.join(' ')
}

/**
 * One delivery's state, as a badge with the word in it.
 *
 * The label is the state, not the colour. Three states have to be told apart at a glance, and
 * two of them are things a person may have to act on, so a board where they read as
 * differently-shaded grey rows is a board whose refusals nobody investigates.
 */
export function StateBadge({ state, label }) {
  const tone = TONE_FOR_STATE[state] || 'neutral'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        TONES[tone] || TONES.neutral
      }`}
    >
      <Icon path={TONE_ICON[tone] || TONE_ICON.neutral} size={13} />
      {label || state}
    </span>
  )
}

//: One map, so a state cannot be drawn one way on one card and another way on another.
//: `neutral` for a duplicate because a re-delivery is not a fault at all.
const TONE_FOR_STATE = {
  verified: 'success',
  duplicate: 'neutral',
  rejected: 'destructive',
}

/**
 * The seal state of a registration's API key.
 *
 * A registration is readable through the core records API, so a key stored in the clear is a
 * secret an operator can re-read, and a secret that can be re-read is not being used as one.
 * The badge says the key is sealed and names which key sealed it, and says so out loud when that
 * key is the published demo constant, because a fresh checkout is running on it.
 */
export function SealedKeyBadge({ callback }) {
  if (!callback) return null
  return (
    <span className="inline-flex items-center gap-1.5">
      <Badge tone="update">Sealed</Badge>
      {callback.key_is_published_default ? (
        <Badge tone="restore">Published demo key</Badge>
      ) : (
        <Badge tone="insert">Key from the environment</Badge>
      )}
    </span>
  )
}

/**
 * One digest, in mono, wrapping rather than truncating.
 *
 * A digest is a machine value, so it is set in the mono face, and it is shown in full. A digest
 * truncated to twelve characters is twelve characters that cannot be checked against anything,
 * and the whole point of recording one is that a reader can compare it.
 */
export function Digest({ value }) {
  return (
    <span className="break-all font-mono text-[11px] text-muted-foreground">
      {value || 'No digest recorded'}
    </span>
  )
}

/**
 * How old a range snapshot is, as words beside the word "stale".
 *
 * The evidence says to check the list "periodically" and gives no period, so an answer that
 * cannot say how old its own allowlist is cannot answer the only question an operator asks during
 * an incident: is this refusal real, or is my copy of the list stale?
 */
export function SnapshotAge({ snapshot }) {
  if (!snapshot) return null
  const stale = snapshot.stale === true
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span
        className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
          stale ? TONES.warning : TONES.success
        }`}
      >
        <Icon path={stale ? TONE_ICON.warning : TONE_ICON.success} size={13} />
        {stale ? 'Snapshot stale' : 'Snapshot fresh'}
      </span>
      <span className="text-xs text-muted-foreground">{snapshot.staleness}</span>
    </span>
  )
}