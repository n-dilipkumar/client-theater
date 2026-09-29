/**
 * Primitives the meeting reminders page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Modal, Notice, Toggle, Checkbox, Spinner, ErrorNote, EmptyState, inputClass,
 * useAsync and JsonView. Two things are missing for this page, and both are
 * built here rather than added to the shared file, which a feature may not edit.
 * The integrator promotes a recurring one into `ui.jsx` as platform work, once.
 *
 * **Why the status chip is not a colour.** This page's whole subject is
 * `Scheduled` / `Sent` / `Skipped` with five documented skip reasons. A rep
 * diagnosing "the customer says they never got the text" is reading exactly
 * this chip, and the two things that answer that question - sent, or skipped and
 * why - must never depend on hue. So every chip is a glyph plus the vendor's own
 * wording, and the wording is fetched from the server rather than typed here.
 */

import { Icon } from '@/components/ui'
import Glyphs from './icons'

const STATUS_GLYPHS = {
  scheduled: Glyphs.pending,
  sent: Glyphs.sent,
  skipped: Glyphs.skipped,
}

const STATUS_TONES = {
  scheduled: 'border-sky-500/30 bg-sky-500/15 text-sky-300',
  sent: 'border-accent/30 bg-accent/15 text-accent',
  skipped: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
}

/**
 * A delivery's status, with the vendor's own wording beside it.
 *
 * `reasonText` is the documented skip reason, quoted from the server - "Reminder
 * condition not satisfied" rather than a slug - because that string is what a
 * rep reads in *Meetings Activity* and what a support conversation quotes.
 */
export function StatusChip({ status, reasonText }) {
  const glyph = STATUS_GLYPHS[status] || Glyphs.pending
  const tone = STATUS_TONES[status] || STATUS_TONES.scheduled
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${tone}`}
    >
      <Icon path={glyph} size={13} />
      {status}
      {reasonText && <span className="text-foreground/80">· {reasonText}</span>}
    </span>
  )
}

/** The channel a reminder went out on, as a glyph plus its label. */
export function ChannelChip({ channel }) {
  const isSms = channel === 'sms'
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle/40
        bg-muted px-2 py-0.5 font-mono text-xs font-medium text-muted-foreground"
    >
      <Icon path={isSms ? Glyphs.phone : Glyphs.envelope} size={13} />
      {channel.toUpperCase()}
    </span>
  )
}

/**
 * A key/value pair on one line, used in the detail panels.
 *
 * Not a `<dt>`/`<dd>` pair so it can be used in a flex row without a `<dl>`
 * around it, and it keeps the label from wrapping away from its value.
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

/** An inline note for a success message, matching the shared Notice's tone. */
export function Note({ children }) {
  return (
    <p
      role="status"
      aria-live="polite"
      className="rounded-lg border border-accent/30 bg-accent/10 p-3 text-sm text-foreground"
    >
      {children}
    </p>
  )
}

/**
 * A warning, for a setting that is researched but not done.
 *
 * `autoTranslateEnabled` is one: the research names the switch and no catalogue,
 * so this build records the locale a message went out in rather than pretending
 * to have translated it. Saying so is better than a toggle that quietly does
 * nothing.
 */
export function Caveat({ children }) {
  return (
    <p className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
      <Icon path={Glyphs.gate} size={14} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  )
}

/** The five documented skip reasons, with how many landed. */
export function ReasonTally({ byReason, reasons }) {
  const entries = Object.entries(byReason || {})
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">Nothing has been skipped yet.</p>
  }
  return (
    <ul className="space-y-1.5">
      {entries
        .sort((a, b) => b[1] - a[1])
        .map(([slug, count]) => (
          <li key={slug} className="flex flex-wrap items-baseline gap-2 text-xs">
            <Icon path={Glyphs.skipped} size={13} className="shrink-0 text-amber-300" />
            <span className="font-mono text-foreground">{reasons[slug] || slug}</span>
            <span className="font-mono text-muted-foreground">{count}</span>
          </li>
        ))}
    </ul>
  )
}
