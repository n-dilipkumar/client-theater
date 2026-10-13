/**
 * Local primitives for the Slack-notification page (WF-112).
 *
 * Everything here is built in this feature folder rather than imported, for one of
 * two stated reasons. `components/ui.jsx` documents a `Notice` and a `Modal` in its
 * own docstring and exports neither, so there is nothing to import. And `PATHS`, the
 * shared icon map, is module-private by design: a hundred features each appending a
 * glyph to it is the collision the feature host exists to prevent, so the nav glyph
 * is passed as a path instead.
 *
 * The design floor this file meets, restated so a later edit does not lose it:
 * semantic tokens only, no raw hex, `rounded-sm` for anything with an edge, `min-h-11`
 * on every control, no emoji as an icon, and no status carried by colour alone. Every
 * state below pairs its colour with a word, because a seller who cannot read the
 * colour still has to know whether a notification went anywhere.
 */

import { Badge, Icon } from '@/components/ui'

import { NOTHING_SENT_NOTE, stateMeaning } from './api'

/** The nav glyph: a paper plane, the closest shared shape for a Slack message. */
export const SLACK_ICON =
  'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z'

const EXTRA_GLYPHS = {
  slack: SLACK_ICON,
  channel: 'M4 9h16M4 15h16M10 3v18M14 3v18',
  limit: 'M12 7v5l3 2M12 21a9 9 0 100-18 9 9 0 000 18z',
}

/** A glyph by name, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={EXTRA_GLYPHS[name]} size={size} className={className} />
}

/**
 * Status as a badge plus a sentence, never as a colour alone.
 *
 * The `sr-only` sentence is the part that matters. A seller who cannot distinguish
 * the two tones still reads "Refused: a rule stopped this notification" out of the
 * text, which is the difference between a page they can trust and a page they guess at.
 */
const TONES = {
  recorded: 'insert',
  refused: 'warning',
  connected: 'insert',
  revoked: 'neutral',
  live: 'insert',
  draft: 'neutral',
}

export function StatusPill({ state }) {
  const key = String(state || '')
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Badge tone={TONES[key] || 'neutral'}>{key.replace(/_/g, ' ') || 'unknown'}</Badge>
      <span className="sr-only">{stateMeaning(key)}</span>
    </span>
  )
}

const NOTICE_TONES = {
  ok: 'border-success/40 bg-success/10 text-success',
  error: 'border-destructive/30 bg-destructive/10 text-destructive',
  info: 'border-info/30 bg-info/10 text-info',
}

/** A local `Notice`. `components/ui.jsx` names one in its docstring and exports none. */
export function Notice({ tone = 'info', title, children, onDismiss }) {
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start justify-between gap-3 rounded-sm border px-3 py-2 text-sm ${
        NOTICE_TONES[tone] || NOTICE_TONES.info
      }`}
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

/** A dash for a value that is not on file, rather than a blank cell that reads as a bug. */
export function Dash() {
  return <span className="text-muted-foreground">—</span>
}

/** A labelled fact, used in the connection panel and the decision drill-down. */
export function Fact({ label, children }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium tracking-[0.14em] text-muted-foreground uppercase">
        {label}
      </p>
      <div className="mt-1 text-sm break-words text-foreground">{children}</div>
    </div>
  )
}

/**
 * The note that sits at the foot of the page.
 *
 * It is here rather than in a comment because it is the single most important thing a
 * reader of this page should not have to infer: **this product records notifications
 * and does not send them.**
 */
export function ActionabilityNote() {
  return (
    <p className="rounded-sm border border-border-subtle bg-muted px-3 py-2 text-xs text-muted-foreground">
      {NOTHING_SENT_NOTE}
    </p>
  )
}
