/**
 * Local primitives for the intent-alerting page (WF-133).
 *
 * Everything here is built in this feature folder rather than imported, for one of
 * two stated reasons. `components/ui.jsx` names a `Notice` in its own docstring and
 * exports none, so there is nothing to import. And `PATHS`, the shared icon map,
 * is module-private by design: a hundred features each appending a glyph to it is
 * the collision the feature host exists to prevent, so the nav glyph is passed as a
 * path instead.
 *
 * The design floor this file meets, restated so a later edit does not lose it:
 * semantic tokens only, no raw hex, `rounded-sm` for anything with an edge,
 * `min-h-11` on every control, no emoji as an icon, and no status carried by colour
 * alone. Every state below pairs its colour with a word, because a rep who cannot
 * read the colour still has to know whether the alert went anywhere.
 */

import { Badge, Icon } from '@/components/ui'

/** The nav glyph: a bell over a room. A path, so the shared icon map is untouched. */
export const ALERT_ICON =
  'M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M13.7 21a2 2 0 01-3.4 0'

const EXTRA_GLYPHS = {
  bell: ALERT_ICON,
  threshold: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  pages: 'M9 12h6M9 8h6M5 3h14a1 1 0 011 1v16a1 1 0 01-1 1H5a1 1 0 01-1-1V4a1 1 0 011-1z',
}

/**
 * A glyph by name, falling through to the shared set.
 *
 * This is the pattern for feature icons: the shared names still work, and the extra
 * ones stay in this folder.
 */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={EXTRA_GLYPHS[name]} size={size} className={className} />
}

/**
 * Status as a badge plus a sentence, never as a colour alone.
 *
 * The `sr-only` sentence is the part that matters. A rep who cannot distinguish the
 * four tones still reads "Suppressed for 24 hours after the last alert" out of the
 * text, which is the difference between a page they can trust and a page they have
 * to guess at.
 */
const TONES = {
  open: 'update',
  acknowledged: 'insert',
  contacted: 'insert',
  dismissed: 'neutral',
  queued: 'insert',
  held_for_integration: 'warning',
  skipped: 'warning',
  suppressed: 'neutral',
}

const MEANING = {
  open: 'Raised and not yet worked by a rep',
  acknowledged: 'A rep has seen this and is working it',
  contacted: 'A rep has reached the stakeholder',
  dismissed: 'A rep judged this not worth a call',
  queued: 'A message exists in the outbox to send',
  held_for_integration: 'Named by the research; this build has no surface for it',
  skipped: 'The accountable party has no address on file',
  suppressed: 'Told within the last 24 hours, so not sent again',
}

export function StatusPill({ state }) {
  const key = String(state || '')
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Badge tone={TONES[key] || 'neutral'}>{key.replace(/_/g, ' ') || 'unknown'}</Badge>
      <span className="sr-only">{MEANING[key] || 'No meaning published for this state'}</span>
    </span>
  )
}

/** The visible half of StatusPill, for places that show the sentence too. */
export function statusMeaning(state) {
  return MEANING[String(state || '')] || 'No meaning published for this state'
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
 * One line of the "who is engaged and who is not" table.
 *
 * A dash for a value that is not on file, rather than a blank cell. An empty cell in
 * a data-dense table reads as a bug and a dash reads as an answer.
 */
export function Dash() {
  return <span className="text-muted-foreground">—</span>
}

/** A labelled fact, used in the alert drill-down. */
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
 * It is here rather than in a comment because it is the single most important thing
 * a reader of this page should not have to infer: **this product records alerts and
 * does not send them.** A page that says it only in its docstring is a page that
 * will be read by someone who has not read its docstring.
 */
export function ActionabilityNote() {
  return (
    <p className="rounded-sm border border-border-subtle bg-muted px-3 py-2 text-xs text-muted-foreground">
      This workflow records alerts; it does not send mail or a Slack message on anyone&rsquo;s
      behalf. Email is queued as a real outbox row, Slack is held because this product has no
      Slack surface, and a rep with no address on file is named on the alert rather than
      skipped silently.
    </p>
  )
}
