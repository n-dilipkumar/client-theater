/**
 * Two UI primitives this feature needs that `@/components/ui` does not carry.
 *
 * `docs/FEATURE-CONTRACT.md` lists `Toggle` and `Notice` among the shared
 * primitives, but the file does not export them: the only exports are `Icon`,
 * `Button`, `Badge`, `Card`, `StatCard`, `Spinner`, `ErrorNote`, `EmptyState`,
 * `Field`, `inputClass`, `useAsync` and `JsonView`. The branch added them to
 * `ui.jsx`, which a feature is not allowed to edit.
 *
 * So they are built here, and this is the report line the contract asks for: a
 * `Toggle` and a non-error inline `Note` are needed by WF-007, and the
 * integrator should promote them into `ui.jsx` once as platform work rather than
 * letting a dozen features each ship a subtly different one.
 *
 * One deliberate difference from the branch's version, which is worth knowing
 * about: its `Toggle` rendered a 24px switch button beside a `<label htmlFor>`.
 * A `<label>` only forwards clicks to form controls it names, and it named a
 * `<button>`, so the label was not clickable and the only real target was 24px
 * tall -- below the 44px floor in `AGENTS.md`, despite the comment claiming the
 * row was the target. Here the whole row is one button, so the target is the
 * full height of the control and the label is inside it.
 */

import { Icon } from '@/components/ui'

import { ICONS } from './icons'

/**
 * A labelled switch.
 *
 * The entire row is the button, which is what makes the 44px target real: the
 * track is decorative, and the state is exposed to assistive tech as
 * `role="switch"` with `aria-checked` rather than conveyed by colour alone.
 */
export function Toggle({ label, hint, checked, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`flex w-full min-h-11 items-start gap-3 rounded-lg border p-3 text-left
        transition-colors duration-200
        ${checked ? 'border-accent/50 bg-accent/10' : 'border-border-subtle/50 bg-transparent'}
        hover:bg-muted`}
    >
      <span
        aria-hidden="true"
        className={`mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full border px-0.5
          ${checked ? 'border-accent/50 bg-accent/25' : 'border-border-subtle/50 bg-muted'}`}
      >
        <span
          className={`h-4 w-4 rounded-full transition-transform duration-200 motion-reduce:transition-none
            ${checked ? 'translate-x-5 bg-accent' : 'translate-x-0 bg-muted-foreground'}`}
        />
      </span>
      <span className="min-w-0">
        <span className="block text-sm text-foreground">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </button>
  )
}

/**
 * Non-error, non-success inline note.
 *
 * Used for the documented behaviours a user should know about rather than react
 * to, such as the thumbnail lag. Distinct from `ErrorNote`, which is for
 * something that went wrong and offers a retry.
 */
export function Note({ children }) {
  return (
    <p className="flex items-start gap-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs text-muted-foreground">
      <span className="mt-0.5 shrink-0 text-accent">
        <Icon path={ICONS.info} size={14} />
      </span>
      <span className="min-w-0">{children}</span>
    </p>
  )
}
