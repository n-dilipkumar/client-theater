import { Button, Icon } from '@/components/ui'

import { ICONS } from './icons'

/**
 * The UI primitives this feature needs that `components/ui.jsx` does not carry.
 *
 * `docs/FEATURE-CONTRACT.md` lists `Modal` and `Notice` among the primitives a
 * feature may use, and this copy of `ui.jsx` exports neither. The branch added
 * both, along with nine icon paths, by editing that file. It is shared, so a
 * hundred features each appending to it is the exact collision the plugin host
 * exists to prevent, and CI refuses a diff that touches it. So they live here and
 * are reported as promotion candidates: an integrator should move `Modal` and
 * `Notice` into `ui.jsx` once, as platform work, rather than every port keeping a
 * copy. WF-011 and WF-013 have each now built their own; that is the evidence
 * for the platform change.
 *
 * The glyphs in `./icons.js` are *not* a promotion candidate. They are this
 * feature's own visual vocabulary, and the contract already has the mechanism
 * for shared glyphs: `Icon path=` and `iconPath` in the descriptor.
 *
 * All three meet the same floor as the shared primitives: a 44px minimum target,
 * a real focusable control, a visible focus ring (the global `:focus-visible` in
 * `index.css` does that work and is never overridden here), a visible text label
 * beside every icon, and no emoji used as an icon.
 */

/**
 * An icon for this feature.
 *
 * A local name resolves against `./icons.js` first; anything else falls through
 * to the shared `PATHS` map by name. That is what lets one component serve both
 * vocabularies, and it means a name the shared map does not carry still renders
 * this feature's glyph rather than the silent `schema` fallback.
 */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * A shared `Button` carrying a feature-owned glyph.
 *
 * The shared `Button` takes `icon` as a *name* into its own `PATHS` map, and that
 * map cannot be appended to from a feature. Passing a name this feature invented
 * would not error: `Icon` falls back to the `schema` glyph, so every button on the
 * page would quietly render the same mark. So the glyph goes in as the button's
 * first child instead, where the shared button's own `gap-2` already puts the
 * right space between it and the label.
 *
 * Glyphs the shared set already carries are still passed as `icon` on the shared
 * `Button` directly; this is only for the ones it does not.
 */
export function Action({ glyph, variant = 'secondary', className = '', children, ...props }) {
  return (
    <Button variant={variant} className={className} {...props}>
      {glyph && <Glyph name={glyph} />}
      {children}
    </Button>
  )
}

/**
 * A focus-managed dialog.
 *
 * Built on the native `<dialog>` element on purpose: `showModal()` gives focus
 * trapping, Escape, inertness of the page behind, and the top layer, which is the
 * accessible behaviour a hand-rolled overlay usually gets wrong. `onCancel` is
 * routed through the caller so unmounting happens in one place and the dialog
 * never closes behind a still-rendered page.
 */
export function Modal({ title, description, onClose, children, footer, wide = false }) {
  const ref = (node) => {
    if (node && typeof node.showModal === 'function' && !node.open) node.showModal()
  }

  return (
    <dialog
      ref={ref}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        // A click that lands on the dialog element itself is a backdrop click.
        if (event.target === event.currentTarget) onClose()
      }}
      className={`glass m-auto max-h-[90vh] w-[min(720px,92vw)] overflow-y-auto rounded-2xl
        p-0 text-foreground backdrop:bg-black/60 backdrop:backdrop-blur-sm ${
          wide ? 'sm:max-w-3xl' : ''
        }`}
    >
      <div className="flex items-start justify-between gap-4 border-b border-border-subtle/25 p-5">
        <div className="min-w-0">
          <h2 className="font-mono text-base font-semibold">{title}</h2>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        <Button variant="ghost" onClick={onClose} className="-mr-2 shrink-0">
          <Glyph name="close" />
          <span className="sr-only sm:not-sr-only">Close</span>
        </Button>
      </div>

      <div className="space-y-4 p-5">{children}</div>

      {footer && (
        <div className="flex flex-wrap justify-end gap-2 border-t border-border-subtle/25 p-5">
          {footer}
        </div>
      )}
    </dialog>
  )
}

const NOTICE_TONES = {
  info: 'border-sky-500/40 bg-sky-500/10',
  warn: 'border-amber-500/40 bg-amber-500/10',
  error: 'border-destructive/40 bg-destructive/10',
  ok: 'border-accent/40 bg-accent/10',
}

const NOTICE_GLYPHS = {
  info: 'info',
  warn: 'warning',
  error: 'warning',
  ok: 'info',
}

/**
 * Inline notice inside a surface: the imminent-expiry banner, the "you cannot
 * share this room" explanation, and every refusal the server sends back.
 *
 * Tone is a second channel, never the only one: the meaning is always carried by
 * `title` or the body text, so a colour-blind reader is not left guessing and a
 * screen reader is not reading a colour.
 */
export function Notice({ tone = 'info', glyph, title, children }) {
  return (
    <div
      role="status"
      className={`rounded-lg border p-3 text-sm ${
        NOTICE_TONES[tone] || NOTICE_TONES.info
      }`}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 shrink-0 text-amber-400">
          <Glyph name={glyph || NOTICE_GLYPHS[tone] || 'info'} />
        </span>
        <div className="min-w-0 flex-1">
          {title && <p className="font-semibold">{title}</p>}
          {children && <div className="text-muted-foreground">{children}</div>}
        </div>
      </div>
    </div>
  )
}
