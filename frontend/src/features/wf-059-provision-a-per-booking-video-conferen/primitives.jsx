/**
 * Primitives the meeting-links page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Spinner, ErrorNote, EmptyState, inputClass, useAsync and JsonView. Two things
 * are missing for this page, and both are built here rather than added to the
 * shared file, which a feature may not edit. The integrator promotes a
 * recurring one into `ui.jsx` as platform work, once.
 */

import { Icon } from '@/components/ui'
import { PLUG_ICON, SWAP_ICON, VIDEO_ICON, WAIT_ICON, WARN_ICON } from './icons'

/**
 * The six researched Location states, each with a glyph and a label.
 *
 * A booking list is exactly where tone-only encoding fails: six states that
 * differ only by hue are unreadable to a colour-blind rep and in a screenshot
 * printed in black and white, and the design floor requires a text label beside
 * every icon. The label is the point; the tone is decoration.
 */
export const BOOKING_STATES = {
  provisioned: { label: 'Link ready', tone: 'insert', glyph: VIDEO_ICON },
  swapped: { label: 'Moved to a new tool', tone: 'update', glyph: SWAP_ICON },
  static: { label: 'Static link', tone: 'neutral', glyph: VIDEO_ICON },
  'in-person': { label: 'In person', tone: 'neutral', glyph: VIDEO_ICON },
  'awaiting-guest': { label: 'Waiting on the guest', tone: 'restore', glyph: WAIT_ICON },
  'provision-failed': { label: 'No link yet', tone: 'delete', glyph: WARN_ICON },
  unprovisioned: { label: 'Not provisioned', tone: 'neutral', glyph: WARN_ICON },
}

const TONE_CLASSES = {
  insert: 'border-accent/30 bg-accent/15 text-accent',
  update: 'border-sky-500/30 bg-sky-500/15 text-sky-300',
  delete: 'border-destructive/30 bg-destructive/15 text-destructive',
  restore: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  neutral: 'border-border-subtle/40 bg-muted text-muted-foreground',
}

export function StateChip({ state }) {
  const spec = BOOKING_STATES[state] || BOOKING_STATES.unprovisioned
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${TONE_CLASSES[spec.tone]}`}
    >
      <Icon path={spec.glyph} size={13} />
      {spec.label}
    </span>
  )
}

/**
 * A readiness chip for the Integrations tab.
 *
 * "Connected" and "usable" are different facts - a connection whose credential
 * was revoked still appears in the list and provisions nothing - so the two are
 * rendered with different words rather than the same chip in two colours.
 */
export function ReadinessChip({ readiness }) {
  const ready = Boolean(readiness?.ready)
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${ready ? TONE_CLASSES.insert : TONE_CLASSES.delete}`}
    >
      <Icon path={ready ? PLUG_ICON : WARN_ICON} size={13} />
      {ready ? 'Ready to provision' : 'Cannot provision'}
    </span>
  )
}

/**
 * A quoted line of the research, on the page rather than in a comment.
 *
 * Every rule this workflow implements is traceable to a sentence the research
 * quotes. Showing the sentence beside the rule it produced is what lets a
 * reviewer check the build against its sources without opening the spec.
 */
export function SourceNote({ children, cite }) {
  return (
    <blockquote className="rounded-lg border-l-2 border-accent/50 bg-muted/40 py-2 pl-3 pr-3">
      <p className="text-xs leading-relaxed text-muted-foreground italic">{children}</p>
      {cite && <p className="mt-1 text-[11px] text-muted-foreground/70">{cite}</p>}
    </blockquote>
  )
}

/**
 * A key/value pair on one line.
 *
 * Not a `<dt>`/`<dd>` pair so it can be used in a flex row without a `<dl>`
 * around it, and it keeps the label from wrapping away from its value.
 */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={`min-w-0 truncate ${mono ? 'font-mono' : ''} text-foreground`}>{children}</span>
    </div>
  )
}

/**
 * A join link, shown as a real link and not as a bare string.
 *
 * `meetingLocation` is a URL a prospect clicks, so the page's job is to make it
 * clickable rather than to render it as text someone retypes. It opens in a new
 * tab so a rep clicking through a list of five meetings keeps the list.
 */
export function JoinLink({ url, label = 'Join link' }) {
  if (!url) return <span className="text-xs text-muted-foreground/70">no link</span>
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer noopener"
      className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-xs font-mono
        text-accent underline decoration-accent/40 underline-offset-4 hover:decoration-accent
        focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Icon path={VIDEO_ICON} size={14} />
      <span className="sr-only">{label}: </span>
      {url}
    </a>
  )
}
