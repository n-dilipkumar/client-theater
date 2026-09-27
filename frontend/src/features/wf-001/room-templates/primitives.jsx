import { Icon } from '@/components/ui'

import { ICONS } from './icons'

/**
 * The UI primitives this feature needs that `components/ui.jsx` does not carry.
 *
 * The branch added `Stepper` and `ChoiceCard` to `components/ui.jsx`, along with
 * the `check` and `back` glyphs. That file is shared, so a hundred features each
 * appending to it is the exact collision the plugin host exists to prevent, and CI
 * fails a diff that touches it. So they live here and are reported as promotion
 * candidates: an integrator should move `Stepper` and `ChoiceCard` into `ui.jsx`
 * once, as platform work, rather than every port keeping a copy. Several ports
 * have now built their own, which is the evidence for that platform change.
 *
 * All of these meet the same floor as the shared primitives: a 44px minimum
 * target, a real focusable control, a visible focus ring (the global
 * `:focus-visible` in `index.css` does that work and is never overridden here), a
 * visible text label beside every icon, no emoji used as an icon, and
 * `prefers-reduced-motion` respected.
 */

/** An icon for this feature. Falls through to the shared set for names it lacks. */
export function Glyph({ name, size = 18, className = '' }) {
  return <Icon name={name} path={ICONS[name]} size={size} className={className} />
}

/**
 * A button carrying a glyph this feature owns.
 *
 * Only needed for the two glyphs the shared `PATHS` map does not carry. The
 * shared `Button` takes `icon` as a *name* into that map, and a name this feature
 * invented would not error - `Icon` falls back to the `schema` glyph - so every
 * button would quietly render the same mark. The glyph goes in as the button's
 * first child instead, where the shared button's own `gap-2` already puts the
 * right space between it and the label.
 *
 * For a glyph the shared set already has (`plus`, `close`, `chevron`, `search`)
 * use the shared `Button icon=...` instead. This component exists for the rest,
 * not as a replacement for the shared button.
 */
export function Action({ glyph, variant = 'secondary', className = '', children, ...props }) {
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
        transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50
        ${variant === 'primary'
          ? 'bg-accent text-on-accent hover:brightness-110 font-semibold'
          : variant === 'danger'
            ? 'bg-destructive/15 text-destructive hover:bg-destructive/25'
            : 'bg-muted text-foreground hover:bg-border-subtle'}
        ${className}`}
      {...props}
    >
      {glyph && <Glyph name={glyph} />}
      {children}
    </button>
  )
}

/**
 * Wizard progress as a real ordered list.
 *
 * `<ol>` rather than a row of divs because position is the information here, and
 * a list is what a screen reader announces position from. `aria-current="step"`
 * marks the one the operator is on. The visual bar between markers is decorative
 * and hidden from assistive tech; the labels carry the meaning.
 */
export function Stepper({ steps, current }) {
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-2" aria-label="Room creation steps">
      {steps.map((step, index) => {
        const state = index < current ? 'done' : index === current ? 'current' : 'todo'
        return (
          <li key={step.id} className="flex items-center gap-2">
            <span
              aria-current={state === 'current' ? 'step' : undefined}
              className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm
                ${
                  state === 'current'
                    ? 'bg-accent/15 font-semibold text-accent'
                    : state === 'done'
                      ? 'text-muted-foreground'
                      : 'text-muted-foreground/70'
                }`}
            >
              <span
                aria-hidden="true"
                className={`flex h-6 w-6 items-center justify-center rounded-full border font-mono text-xs
                  ${
                    state === 'current'
                      ? 'border-accent bg-accent/20 text-accent'
                      : state === 'done'
                        ? 'border-accent/50 text-accent'
                        : 'border-border-subtle/60'
                  }`}
              >
                {state === 'done' ? <Glyph name="check" size={14} /> : index + 1}
              </span>
              {step.label}
            </span>
            {index < steps.length - 1 && (
              <span aria-hidden="true" className="h-px w-6 bg-border-subtle/40" />
            )}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * A selectable card for a wizard step, built on a real radio input.
 *
 * The radio lives inside its own `<label>`, so clicking anywhere on the card
 * selects it, arrow keys move between the options in a group, and group semantics
 * come from the browser rather than from a pile of `div`s with click handlers.
 * Visually the input is hidden but not `display: none`, so it keeps its place in
 * the tab order; the ring is drawn on the card through `has-[:focus-visible]`, so
 * what gets outlined is the 44px target the operator actually clicked rather than
 * the 16px dot.
 */
export function ChoiceCard({ name, value, checked, onChange, title, description, meta }) {
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-xl border p-4
        transition-colors duration-200 has-[:focus-visible]:outline
        has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2
        has-[:focus-visible]:outline-accent
        ${
          checked
            ? 'border-accent bg-accent/10'
            : 'border-border-subtle/40 bg-background/30 hover:border-accent/40'
        }`}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
        className="mt-1 h-4 w-4 shrink-0 accent-accent"
      />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-foreground">{title}</span>
        {description && (
          <span className="mt-0.5 block text-sm text-muted-foreground">{description}</span>
        )}
        {meta && <span className="mt-2 flex flex-wrap items-center gap-1.5">{meta}</span>}
      </span>
    </label>
  )
}
