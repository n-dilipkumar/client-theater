/**
 * Local primitives for the page-outreach page (WF-106).
 *
 * Everything here is built in this feature folder rather than imported, for two
 * stated reasons. `components/ui.jsx` names a `Notice` in its own docstring and
 * exports none, so there is nothing to import. And `PATHS`, the shared icon map, is
 * module-private by design: a hundred features each appending a glyph to it is the
 * collision the feature host exists to prevent, so the nav glyph is passed as a path
 * instead.
 *
 * The design floor this file meets, restated so a later edit does not lose it:
 * semantic tokens only, no raw hex, `rounded-sm` for anything with an edge, `min-h-11`
 * on every control, no emoji as an icon, and no status carried by colour alone. Every
 * state below pairs its colour with a word, because a seller who cannot read the
 * colour still has to know whether the block went anywhere.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * The nav glyph: a page turning into a signal.
 *
 * A path, so the shared icon map is untouched. The left half is a document, the right
 * half is a rising bar, which is the ticket's subject in one mark: a page view that
 * turned into outreach.
 */
export const PAGE_OUTREACH_ICON =
  'M6 3h8l4 4v10a2 2 0 01-2 2H6a2 2 0 01-2-2V5a2 2 0 012-2zm8 0v5h4M7 12h5M7 15h3M16 20l3-4 3 4'

const EXTRA_GLYPHS = {
  browse: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-13a4 4 0 100 8 4 4 0 000-8zM2 12h4M18 12h4M12 2v4M12 18v4',
  repeat: 'M4 9a5 5 0 015-5h9m0 0l-3-3m3 3l-3 3M20 15a5 5 0 01-5 5H6m0 0l3 3m-3-3l3-3',
  sessions: 'M12 8v4l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  branch: 'M6 3v6a3 3 0 003 3h6M6 3v18M15 12l3-3m-3 3l3 3',
}

/** A glyph by name, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={EXTRA_GLYPHS[name]} size={size} className={className} />
}

/** The two things a page view can be, and the gate that stops each one. */
export const STOPPED_BY = {
  show: { label: 'block shown', meaning: 'The buyer qualified and the block was shown.' },
  not_live: { label: 'workflow is a draft', meaning: 'Only a live workflow shows a block.' },
  matched_below_repeat: {
    label: 'not enough visits yet',
    meaning: 'The page matched, but the buyer has not made enough visits inside the window.',
  },
  rules_not_matched: {
    label: 'page not targeted',
    meaning: "No targeting rule held for this page, so no visit of it counts as interest.",
  },
  frequency_mode: {
    label: 'stopped by the mode',
    meaning: 'The Show workflow until mode has already stopped for this buyer.',
  },
  hidden_for_session: {
    label: 'hidden for this session',
    meaning: 'The buyer dismissed the block or opened the Messenger. The next session shows it again.',
  },
  audience: {
    label: 'outside the audience',
    meaning: 'The audience pane excluded this visitor.',
  },
}

/** One gate, as a badge plus the sentence behind it. */
export function GatePill({ stoppedBy, showText = false }) {
  const key = String(stoppedBy || '')
  const entry = STOPPED_BY[key]
  const tone =
    key === 'show' ? 'update' : key === 'hidden_for_session' ? 'neutral' : key === 'not_live' ? 'neutral' : 'insert'
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Badge tone={tone}>{entry ? entry.label : key.replace(/_/g, ' ') || 'unknown'}</Badge>
      <span className="sr-only">{entry ? entry.meaning : 'No meaning published for this gate'}</span>
      {showText && entry ? <span className="text-xs text-muted-foreground">{entry.meaning}</span> : null}
    </span>
  )
}

/** The four states a delivery can be in, each paired with its meaning in words. */
export const DELIVERY_TONES = {
  shown: 'update',
  interacted: 'insert',
  engaged: 'insert',
  hidden_for_session: 'neutral',
}

export const DELIVERY_MEANING = {
  shown: 'Shown, and the buyer has done nothing with it yet',
  interacted: 'The buyer clicked something in the block',
  engaged: 'The buyer chose a branch, or the goal fired',
  hidden_for_session: 'The buyer dismissed it or opened the Messenger',
}

export function DeliveryPill({ state }) {
  const key = String(state || 'shown')
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Badge tone={DELIVERY_TONES[key] || 'neutral'}>{key.replace(/_/g, ' ')}</Badge>
      <span className="sr-only">{DELIVERY_MEANING[key] || 'No meaning published for this state'}</span>
    </span>
  )
}

/** The visible half of DeliveryPill, for places that show the sentence too. */
export function deliveryMeaning(state) {
  return DELIVERY_MEANING[String(state || '')] || 'No meaning published for this state'
}

/**
 * A local `Notice`.
 *
 * `components/ui.jsx` names a `Notice` in its docstring and exports none, so this is
 * built here rather than imported, which is what the feature contract asks for in
 * that case. Tones are the semantic tokens only.
 */
const NOTICE_TONES = {
  ok: 'border-success/40 bg-success/10 text-success',
  error: 'border-destructive/30 bg-destructive/10 text-destructive',
  info: 'border-info/30 bg-info/10 text-info',
}

export function Notice({ tone = 'info', title, children, onDismiss }) {
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start justify-between gap-3 rounded-sm border px-3 py-2 text-sm ${NOTICE_TONES[tone] || NOTICE_TONES.info}`}
    >
      <span className="min-w-0">
        {title ? <span className="font-medium">{title} </span> : null}
        {children}
      </span>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          className="min-h-11 shrink-0 rounded-sm px-2 text-xs font-medium hover:opacity-80"
        >
          Dismiss
        </button>
      ) : null}
    </div>
  )
}

/**
 * One line of the prospects table.
 *
 * A dash for a value that is not on file, rather than a blank cell. An empty cell in
 * a data-dense table reads as a bug and a dash reads as an answer.
 */
export function Dash() {
  return <span className="text-muted-foreground">&mdash;</span>
}

/** A labelled fact, used in the delivery drill-down. */
export function Fact({ label, children }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
        {label}
      </p>
      <div className="mt-1 text-sm text-foreground">{children}</div>
    </div>
  )
}

/**
 * The note that sits at the foot of the page.
 *
 * It is here rather than in a comment because it is the single most important thing a
 * reader of this page should not have to infer: **this product records what it would
 * show; it posts nothing.** A page that says it only in its docstring is a page read
 * by someone who has not read its docstring.
 */
export function DeliveryHonestyNote() {
  return (
    <p className="rounded-sm border border-border-subtle bg-muted px-3 py-2 text-xs text-muted-foreground">
      This workflow records the block it would show and the receipts for what the buyer did with
      it. It posts no message to any vendor and opens no browser messenger. The page view itself is
      collected by the seller&rsquo;s own JavaScript snippet, so this room owns the rules and the
      receipts rather than the collection.
    </p>
  )
}

/**
 * The banner that says the three thresholds are this build's derivation.
 *
 * It is on the page rather than on a tab a seller has to find, because a number that
 * decides whether a buyer sees anything is not a detail.
 */
export function DerivedThresholdNote({ thresholds = [] }) {
  if (!thresholds.length) return null
  return (
    <p className="rounded-sm border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
      <span className="font-medium">Derived, not sourced. </span>
      {thresholds.map((entry) => `${entry.label}: ${entry.threshold} ${entry.unit}`).join('. ')}.
      The research names the signal and states no number, so each of these is this build&rsquo;s
      reading and each one says what would change it.
    </p>
  )
}
