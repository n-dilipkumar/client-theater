/**
 * Glyphs and two primitives this feature needs, built here rather than added to
 * `components/ui.jsx`.
 *
 * `components/ui.jsx` is shared and a hundred features each appending to it is the
 * collision the feature host exists to prevent, so the field-mapping glyph is passed
 * as a path through `<Icon path=...>` and the inline `Notice` is built in this
 * folder. Both are findings for the integrator: `Notice` in particular is generic
 * enough that the next workflow will want it too, and promoting it into `ui.jsx` is
 * a one-off platform change belonging to whoever owns that file. It is listed in
 * `docs/FEATURE-CONTRACT.md` among the primitives a feature may use and is not
 * exported, which is a documentation/implementation gap worth closing rather than
 * widening.
 *
 * `BadgeRow` is **not** a promotion candidate. The server already returns a
 * severity per finding, and a client that rendered its own tone for the same
 * finding would be a second source of truth for what the validator said.
 *
 * The floor they meet, from the design system: a 44px minimum touch target, a
 * visible focus ring (inherited globally from `index.css`), a text label beside
 * every glyph so meaning survives with icons off, no emoji, and no motion beyond
 * the global `prefers-reduced-motion` rule.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * Two columns joined by a link: the sales-room field on the left, the CRM property
 * on the right. The `MAPPING_ICON` shape of "these are the same thing on two sides",
 * and it is not in the shared `PATHS` map, which is why this folder defines it and
 * `Icon` receives it as a path. The fallback for a consumer that reads only the
 * shared name is the shared `schema` mark.
 */
export const MAPPING_ICON =
  'M4 6h5v5H4V6zm11 7h5v5h-5v-5zM9 8.5h6M9 8.5v5M4 16.5h16'

/** A key: the one thing that makes a second write a collision. */
export const KEY_ICON = 'M14 7a4 4 0 11-4 4l-1 1v2H7v2H5v2H3v-4l6.5-6.5A4 4 0 0114 7z'

/** A tick or a cross, for a row's verdict. */
export const OK_ICON = 'M5 13l4 4L19 7'
export const BAD_ICON = 'M6 6l12 12M18 6L6 18'
export const WARN_ICON = 'M12 9v4m0 4h.01M10.3 3.9L2 18a2 2 0 001.7 3h16.6A2 2 0 0022 18L13.7 3.9a2 2 0 00-3.4 0z'

const ICONS = {
  mapping: MAPPING_ICON,
  key: KEY_ICON,
  ok: OK_ICON,
  bad: BAD_ICON,
  warn: WARN_ICON,
}

/** Draw one of this feature's glyphs, falling through to the shared set. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * Inline notice for something the interface has to justify rather than merely report:
 * a missing property read, a refused create request, a gap in the sourcing.
 *
 * The meaning is always carried by the body text. Tone is a second channel, never
 * the only one - which is why every caller passes a sentence rather than relying on
 * a colour to say what happened.
 */
const NOTICE_TONES = {
  info: { box: 'border-sky-500/40 bg-sky-500/10', text: 'text-sky-200', icon: 'warn' },
  good: { box: 'border-accent/40 bg-accent/10', text: 'text-accent', icon: 'ok' },
  warn: { box: 'border-amber-500/40 bg-amber-500/10', text: 'text-amber-200', icon: 'warn' },
  bad: { box: 'border-destructive/40 bg-destructive/10', text: 'text-destructive', icon: 'bad' },
}

export function Notice({ tone = 'info', title, icon, children, action }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.info
  const glyph = icon || palette.icon
  // A warning or a failure is announced; a confirmation is not. Both would be
  // noise if every notice interrupted whatever the user was doing.
  const role = tone === 'bad' || tone === 'warn' ? 'alert' : 'status'
  return (
    <div role={role} className={`rounded-lg border p-3 text-sm ${palette.box} ${palette.text}`}>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 shrink-0">
          <Glyph name={glyph} />
        </span>
        <div className="min-w-0 flex-1">
          {title && <p className="font-semibold">{title}</p>}
          <div className={title ? 'mt-0.5 text-muted-foreground' : 'text-muted-foreground'}>{children}</div>
          {action && <div className="mt-2">{action}</div>}
        </div>
      </div>
    </div>
  )
}

const BADGE_TONES = { good: 'insert', warn: 'restore', bad: 'delete', info: 'neutral' }

/**
 * One finding as a badge, with the server's message in a `title` so the full
 * sentence is available on hover without the grid growing a column of prose.
 *
 * The tone comes from the severity the server assigned. The client does not decide
 * what is an error: that judgement belongs to the validator, and a second opinion
 * here would be a second source of truth for the same finding.
 */
export function BadgeRow({ finding }) {
  return (
    <span className="inline-flex items-center gap-1.5" title={finding.message}>
      <Badge tone={BADGE_TONES[finding.tone] || 'neutral'}>{finding.flag}</Badge>
      <span className="truncate text-xs text-muted-foreground">{finding.message}</span>
    </span>
  )
}
