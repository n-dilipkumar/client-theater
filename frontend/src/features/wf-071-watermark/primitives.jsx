/**
 * WF-071's own primitives.
 *
 * Everything here is built from `@/components/ui`, and every control meets the
 * accessibility floor: `min-h-11` on each button (the shared `Button` applies it),
 * a visible text label beside each icon, and no state carried by colour alone.
 *
 * These are local because `ui.jsx` has no equivalent, and building them here is
 * the contract's instruction rather than an oversight. Promotion work for the
 * integrator, not a reason to edit a shared file:
 *
 *   - `InlineNote`, because `ui.jsx` has no `Notice` and this page states what it
 *     did or could not do.
 *   - `WatermarkStateBadge`, because the state is a finding and the word beside
 *     the colour is what makes it one.
 *   - `TokenChips`, because the token picker is a row of one-tap inserts.
 *   - `OverlayPreview`, because the composited overlay is the whole point and no
 *     shared primitive draws it.
 *   - `StampRow`, because the stamp log is a list of facts.
 *
 * The overlay is drawn flat and unrotated on purpose. The research renders it at
 * the configured rotation onto the page; this panel is a data panel, and a
 * rotated preview would also be vestibular motion that the design floor refuses.
 * The configured angle is printed as a number beside it instead.
 */

import { Badge, Icon } from '@/components/ui'

import { POSITION_LABELS, STATE_LABELS, shortTimestamp, stateTone } from './api'

/** An inline note about something this page did, or could not do. */
export function InlineNote({ tone = 'neutral', children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-warning',
    info: 'border-info/40 bg-info/10 text-info',
    success: 'border-success/40 bg-success/10 text-success',
    danger: 'border-destructive/40 bg-destructive/10 text-destructive',
  }
  return (
    <p
      className={`flex items-start gap-2 rounded-sm border px-3 py-2 text-xs ${
        tones[tone] || tones.neutral
      }`}
      role="status"
      aria-live="polite"
    >
      <Icon name="schema" size={14} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  )
}

/** The watermark state as a word plus a tone. The word is always present. */
export function WatermarkStateBadge({ state }) {
  return <Badge tone={stateTone(state)}>{STATE_LABELS[state] || state || 'Unknown'}</Badge>
}

/**
 * The token picker: one button per researched token, each inserting `{{name}}`.
 *
 * The label names the token and the value it stamps, so a rep never has to
 * remember which word is legal; the server refuses an unknown token anyway.
 */
export function TokenChips({ tokens, labels, onInsert }) {
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Watermark tokens">
      {tokens.map((token) => (
        <li key={token}>
          <button
            type="button"
            className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-border-subtle bg-surface px-3 text-left text-xs text-foreground hover:border-accent hover:text-accent"
            onClick={() => onInsert(token)}
          >
            <Icon name="plus" size={14} />
            <span className="font-mono">{`{{${token}}}`}</span>
            <span className="text-muted-foreground">{labels[token] || ''}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}

/**
 * The composited overlay, rendered flat: the substituted text plus the settings
 * that travel with it. The text is the one thing a leak carries, so it is drawn
 * large and in mono rather than decorated.
 */
export function OverlayPreview({ rendered, overlay, unresolved = [] }) {
  if (!rendered) {
    return (
      <p className="rounded-sm border border-dashed border-border-subtle px-3 py-6 text-center text-sm text-muted-foreground">
        No overlay yet. Write a text and preview it, or stamp a view.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="rounded-sm border border-border-subtle bg-muted px-4 py-6 text-center font-mono text-sm text-foreground">
        {rendered}
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        <Meta label="Position" value={POSITION_LABELS[overlay?.position] || overlay?.position} />
        <Meta label="Rotation" value={`${overlay?.rotation ?? 0} degrees`} />
        <Meta label="Colour" value={overlay?.color} />
        <Meta label="Font size" value={`${overlay?.font_size ?? 0} pt`} />
        <Meta label="Opacity" value={String(overlay?.opacity ?? '')} />
        <Meta label="Tiling" value={overlay?.is_tiled ? 'Tiled across the page' : 'One anchor'} />
      </dl>
      {unresolved.length > 0 && (
        <InlineNote tone="warning">
          {`This view carried no value for: ${unresolved.join(', ')}. The token rendered as its recorded placeholder rather than being left blank, so the trace is visible.`}
        </InlineNote>
      )}
    </div>
  )
}

function Meta({ label, value }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className="font-mono text-foreground">{value ?? '--'}</dd>
    </div>
  )
}

/** One stamped view. The substituted text is the row's reason to exist. */
export function StampRow({ stamp }) {
  return (
    <li className="flex flex-col gap-1 border-b border-border-subtle py-2 last:border-b-0">
      <span className="flex flex-wrap items-center gap-2">
        <span className="truncate font-mono text-sm text-foreground">
          {stamp.rendered_text}
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          {stamp.viewer || 'anonymous'} | {stamp.ip_address || 'no address'} |{' '}
          {stamp.page_count ?? 0} page(s) | {shortTimestamp(stamp.stamped_at)}
        </span>
      </span>
      {stamp.unresolved_tokens?.length > 0 && (
        <span className="text-xs text-warning">
          Could not fill: {stamp.unresolved_tokens.join(', ')}
        </span>
      )}
    </li>
  )
}

/** The config as a compact summary, so a committed value is readable at a glance. */
export function ConfigSummary({ config }) {
  if (!config) return <p className="text-sm text-muted-foreground">No text is set.</p>
  return (
    <p className="font-mono text-xs text-muted-foreground">
      {config.text} | {POSITION_LABELS[config.position] || config.position} | {config.rotation}
      &deg; | {config.color} | {config.font_size}pt | opacity {config.opacity} |{' '}
      {config.is_tiled ? 'tiled' : 'one anchor'}
    </p>
  )
}
