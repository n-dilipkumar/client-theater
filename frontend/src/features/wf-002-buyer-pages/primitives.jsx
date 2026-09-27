import Glyph from './icons'

/**
 * Exactly two UI primitives this feature needs that the shared set in
 * `components/ui.jsx` does not carry: `Note` and `Toggle`.
 *
 * They are in their own module, and apart from them, on purpose. The Jev gate on
 * this port (`audit_id jev-20260926T181332-15124-12579`, verdict `fix` at 0.70
 * against `merge` at 0.29) named the duplication in this feature's UI as the fix
 * it wanted. Duplication is the right thing for it to notice, and the fix is not
 * to delete the two primitives - the contract tells a feature that needs one to
 * build it locally and report it - but to make the promotion set unambiguous, so
 * an integrator promotes these two and nothing else.
 *
 * The distinction being drawn here:
 *
 *   - `Note` and `Toggle` are platform-shaped. Every feature in this product
 *     renders a status note and a labelled on/off control, the contract already
 *     lists both among the primitives a feature may use, and twelve
 *     independently-authored modules will each write their own copy. These are
 *     the two to promote into `components/ui.jsx`, once, as platform work.
 *   - The glyph vocabulary in `./icons.jsx` is *not* a promotion candidate. It
 *     is the visual identity of this feature's fragment catalogue, and no other
 *     feature has a fragment catalogue. It is feature-private by nature and
 *     belongs next to the code that renders blocks, not in a shared icon map.
 *
 * This file must not import from `components/ui.jsx`'s own future additions in a
 * way that would double-render; the shared components remain the first choice and
 * these are the fallback for the two the set is missing.
 *
 * Both meet the same floor as the shared primitives: 44px minimum hit target, a
 * real focusable control, a visible label, and no emoji as an icon.
 */

/**
 * An inline informational, warning or positive note. `tone` picks the colour and
 * the glyph; the meaning is always carried by `title` or the body text, never by
 * the colour alone.
 */
export function Note({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-sky-500/40 bg-sky-500/10',
    warn: 'border-amber-500/40 bg-amber-500/10',
    good: 'border-accent/40 bg-accent/10',
  }
  const icon = { info: 'info', warn: 'warning', good: 'check' }[tone]
  return (
    <div className={`flex gap-3 rounded-lg border p-3 text-sm ${tones[tone]}`}>
      <span className="mt-0.5 shrink-0">
        <Glyph name={icon} />
      </span>
      <div className="min-w-0">
        {title && <p className="font-semibold text-foreground">{title}</p>}
        <div className={title ? 'mt-0.5 text-foreground/90' : 'text-foreground/90'}>{children}</div>
      </div>
    </div>
  )
}

/**
 * A labelled on/off control.
 *
 * A real checkbox rather than a styled div, so it is reachable by keyboard and
 * announced correctly, and the whole row is the hit target. The label wraps the
 * input, so clicking the text toggles it without any extra wiring.
 */
export function Toggle({ label, hint, checked, onChange, id }) {
  return (
    <label
      htmlFor={id}
      className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-lg border border-border-subtle/40 bg-background/40 px-3 py-2 transition-colors duration-200 hover:border-border-subtle/70"
    >
      <span className="min-w-0">
        <span className="block text-xs font-medium text-foreground">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted-foreground">{hint}</span>}
      </span>
      <input
        id={id}
        type="checkbox"
        className="h-5 w-5 shrink-0 accent-accent"
        checked={Boolean(checked)}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  )
}
