import { useState } from 'react'

import { Button, Icon } from '@/components/ui'
import { ICONS } from './icons'

/**
 * Four UI primitives this feature needs that the shared set in
 * `components/ui.jsx` does not export: `Checkbox`, `RadioGroup`, `Notice` and
 * `CopyField`.
 *
 * The branch added all four to `components/ui.jsx`, plus eight glyphs. That file
 * is shared, so a hundred features each appending to it is the collision the plugin
 * host exists to prevent. The contract's answer is explicit: build what you need
 * inside your own feature folder and say so in the report, and the integrator
 * promotes recurring ones as platform work, once.
 *
 * The promotion set, stated unambiguously so an integrator knows what to take:
 *
 *   - `Checkbox`, `RadioGroup` and `Notice` are platform-shaped. `Notice` in
 *     particular is already listed in `docs/FEATURE-CONTRACT.md` among the
 *     primitives a feature may use, and it is not exported there, which is a
 *     documentation/implementation gap rather than something to widen. All three
 *     are the standard controls, and at least two other ports have rebuilt `Notice`
 *     independently. They belong in `ui.jsx` once.
 *   - `CopyField` is narrower: a copy button for a value the operator has to hand
 *     to somebody else. Worth promoting if a second feature ships a delivery seam,
 *     because that is the only situation in which it appears.
 *   - `PathButton` should become an `iconPath` prop on the shared `Button`. It is
 *     the same wrapper another port already needed, and a shared `Button` that
 *     cannot render a feature-supplied glyph is a small hole in an otherwise
 *     complete escape hatch.
 *   - The glyphs in `./icons.jsx` are *not* a promotion candidate. See that file.
 *
 * All of them meet the same floor as the shared primitives: a 44px minimum target,
 * a real focusable control, a visible focus ring (the global `:focus-visible` rule
 * in `index.css` does that work and is never overridden here), a visible text label
 * beside every icon, and no emoji used as an icon. Colour is never the only
 * channel: a disabled control also says why, and a `Notice` always carries a title
 * or body text.
 */

/**
 * The shared `Button` resolves `icon` as a *name* through the shared `PATHS` map,
 * so it cannot draw a glyph a feature supplies. Rather than re-implement `Button`
 * and put a second subtly different button in the product, the glyph goes in as a
 * child: every variant, the 44px target and the focus ring still come from the
 * shared primitive.
 */
export function PathButton({ glyph, children, ...props }) {
  return (
    <Button {...props}>
      {glyph && <Icon path={glyph} />}
      {children}
    </Button>
  )
}

const NOTICE_TONES = {
  info: { box: 'border-sky-500/40 bg-sky-500/10', text: 'text-sky-200' },
  warn: { box: 'border-amber-500/40 bg-amber-500/10', text: 'text-amber-200' },
  good: { box: 'border-accent/40 bg-accent/10', text: 'text-foreground' },
  bad: { box: 'border-destructive/40 bg-destructive/10', text: 'text-foreground' },
}

/**
 * Inline note, for where the interface has to justify a constraint rather than
 * merely report a failure: Domain Security requiring verification, a stored policy
 * that no longer validates, or a buyer refused at the door.
 *
 * `role` is `alert` only for `bad`, so a screen reader interrupts for a refusal and
 * not for a hint.
 */
export function Notice({ tone = 'info', title, children, action }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.info
  return (
    <div
      role={tone === 'bad' ? 'alert' : 'status'}
      className={`flex flex-wrap items-start justify-between gap-3 rounded-lg border p-4 ${palette.box}`}
    >
      <div className="min-w-0">
        {title && <p className={`text-sm font-semibold ${palette.text}`}>{title}</p>}
        <div className={`text-sm text-muted-foreground ${title ? 'mt-1' : ''}`}>{children}</div>
      </div>
      {action}
    </div>
  )
}

/**
 * Checkbox with a 44px hit area.
 *
 * The native input stays in the DOM rather than being replaced by a styled span, so
 * keyboard and screen-reader behaviour is the browser's own. The label is a
 * sibling rather than a wrapper so the whole row is the target, which is what makes
 * a settings list usable on a phone.
 */
export function Checkbox({ id, label, description, checked, onChange, disabled = false }) {
  return (
    <label
      htmlFor={id}
      className={`flex min-h-11 items-start gap-3 rounded-lg py-2 ${
        disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer hover:bg-muted/30'
      }`}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        aria-describedby={description ? `${id}-hint` : undefined}
        className="mt-1 h-5 w-5 shrink-0 cursor-pointer accent-[var(--color-accent)] disabled:cursor-not-allowed"
      />
      <span className="min-w-0">
        <span
          className={`block text-sm font-medium ${
            disabled ? 'text-muted-foreground' : 'text-foreground'
          }`}
        >
          {label}
        </span>
        {description && (
          <span id={`${id}-hint`} className="mt-0.5 block text-xs text-muted-foreground">
            {description}
          </span>
        )}
      </span>
    </label>
  )
}

/**
 * Radio group for a small set of mutually exclusive options, which all have to stay
 * visible because the point of the assurance tier is the choice between them.
 */
export function RadioGroup({ name, legend, options, value, onChange, disabled = false }) {
  return (
    <fieldset disabled={disabled} className="min-w-0">
      <legend className="text-xs font-medium text-muted-foreground">{legend}</legend>
      <div className="mt-2 grid gap-2">
        {options.map((option) => {
          const id = `${name}-${option.value}`
          const active = value === option.value
          return (
            <label
              key={option.value}
              htmlFor={id}
              className={`flex min-h-11 items-start gap-3 rounded-lg border p-3 transition-colors duration-200 ${
                disabled
                  ? 'cursor-not-allowed opacity-70'
                  : active
                    ? 'cursor-pointer border-accent/60 bg-accent/10'
                    : 'cursor-pointer border-border-subtle/40 bg-background/40 hover:bg-muted/50'
              }`}
            >
              <input
                id={id}
                type="radio"
                name={name}
                value={option.value}
                checked={active}
                onChange={() => onChange(option.value)}
                className="mt-1 h-5 w-5 shrink-0 cursor-pointer accent-[var(--color-accent)]"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">{option.label}</span>
                {option.description && (
                  <span className="mt-0.5 block text-xs text-muted-foreground">{option.description}</span>
                )}
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

/**
 * Copyable value, for the verification link on an install with no mail server.
 *
 * The value is also a real, selectable, focusable input: the clipboard API is
 * refused often enough - an insecure origin, a denied permission - that a
 * copy-only control would leave the operator with no way to hand the link over.
 */
export function CopyField({ value, label }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard access can be refused; the field stays selectable either way.
      setCopied(false)
    }
  }

  return (
    <div className="min-w-0">
      {label && <p className="mb-1 text-xs font-medium text-muted-foreground">{label}</p>}
      <div className="flex items-stretch gap-2">
        <input
          readOnly
          value={value}
          aria-label={label || 'Verification link'}
          onFocus={(event) => event.target.select()}
          className="min-h-11 min-w-0 flex-1 rounded-lg border border-border-subtle/50 bg-background/60 px-3 font-mono text-xs text-foreground"
        />
        <PathButton glyph={copied ? ICONS.check : ICONS.copy} onClick={copy} className="shrink-0">
          {copied ? 'Copied' : 'Copy'}
        </PathButton>
      </div>
    </div>
  )
}
