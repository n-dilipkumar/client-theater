import { ICONS } from './icons'

/**
 * Exactly two UI primitives this feature needs that the shared set in
 * `components/ui.jsx` does not export: `Notice` and `Segmented`.
 *
 * The branch added both to `components/ui.jsx`. That file is shared, so a hundred
 * features each appending to it is the collision the plugin host exists to
 * prevent. The contract's answer is explicit: build what you need inside your own
 * feature folder and say so in the report, and the integrator promotes recurring
 * ones as platform work, once.
 *
 * The promotion set, stated unambiguously so an integrator knows what to take:
 *
 *   - `Notice` and `Segmented` are both platform-shaped. `docs/FEATURE-CONTRACT.md`
 *     already lists `Notice` among the primitives a feature may use, and it is not
 *     exported there, which is a documentation/implementation gap worth closing
 *     rather than widening. `Segmented` is the standard control for mutually
 *     exclusive options that should all stay visible, and three of the ports
 *     built it independently. Both belong in `ui.jsx` once.
 *   - The glyphs in `./icons.js` are *not* a promotion candidate. They are this
 *     feature's own visual vocabulary, and the contract already has the mechanism
 *     for shared glyphs: `Icon path=` and `iconPath` in the descriptor.
 *
 * Both meet the same floor as the shared primitives: a 44px minimum target, a
 * real focusable control, a visible focus ring (the global `:focus-visible` in
 * `index.css` does the work and is never removed here), a visible text label, and
 * no emoji used as an icon.
 */

const NOTICE_TONES = {
  info: {
    box: 'border-sky-500/40 bg-sky-500/10',
    text: 'text-sky-200',
    icon: ICONS.info,
  },
  warn: {
    box: 'border-amber-500/40 bg-amber-500/10',
    text: 'text-amber-200',
    icon: ICONS.warning,
  },
  danger: {
    box: 'border-destructive/40 bg-destructive/10',
    text: 'text-destructive',
    icon: ICONS.warning,
  },
}

/**
 * Inline explanatory note, used where the interface has to justify a constraint
 * rather than merely report a failure: the 10-Or limit, the Accept Block
 * exception, or a Saved Block whose rule was dropped.
 *
 * The meaning is always carried by `title` or the body text. Tone is a second
 * channel, never the only one.
 */
export function Notice({ tone = 'info', title, children }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.info
  return (
    <div
      className={`flex gap-3 rounded-lg border p-3 text-sm ${palette.box} ${palette.text}`}
    >
      <span className="mt-0.5 shrink-0" aria-hidden="true">
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d={palette.icon} />
        </svg>
      </span>
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        <div className={title ? 'mt-0.5' : ''}>{children}</div>
      </div>
    </div>
  )
}

/**
 * A mutually exclusive set of options, rendered as a real radio group.
 *
 * Radios rather than buttons: every alternative stays visible instead of hiding
 * behind a toggle, and keyboard users get arrow-key navigation for free. The
 * radio itself is visually hidden but stays in the tab order, and the label
 * carries the focus ring through `peer-focus-visible`, so keyboard focus is
 * never invisible.
 */
export function Segmented({ label, value, options, onChange, name, hint }) {
  return (
    <fieldset className="flex flex-col gap-1.5">
      {label && (
        <legend className="text-xs font-medium text-muted-foreground">{label}</legend>
      )}
      <div className="flex flex-wrap gap-1 rounded-lg border border-border-subtle/40 bg-background/40 p-1">
        {options.map((option) => {
          const active = option.value === value
          const id = `${name}-${option.value}`
          return (
            <div key={option.value} className="min-w-0 flex-1">
              <input
                id={id}
                type="radio"
                name={name}
                value={option.value}
                checked={active}
                onChange={() => onChange(option.value)}
                className="peer sr-only"
              />
              <label
                htmlFor={id}
                className={`flex min-h-11 cursor-pointer items-center justify-center rounded-md px-3
                  text-center text-sm transition-colors duration-150
                  peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2
                  peer-focus-visible:outline-accent ${
                    active
                      ? 'bg-accent/20 font-medium text-accent'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`}
              >
                {option.label}
              </label>
            </div>
          )
        })}
      </div>
      {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
    </fieldset>
  )
}
