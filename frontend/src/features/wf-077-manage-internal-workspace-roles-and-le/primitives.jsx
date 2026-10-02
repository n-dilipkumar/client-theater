/**
 * Small building blocks for WF-077's page.
 *
 * `docs/FEATURE-CONTRACT.md` lists `Modal`, `Notice`, `Toggle` and `Checkbox`
 * among the primitives in `src/components/ui.jsx`. **They are not there.** The
 * twelve exports that file actually has are `Icon`, `Button`, `Badge`, `Card`,
 * `StatCard`, `Spinner`, `ErrorNote`, `EmptyState`, `Field`, `inputClass`,
 * `useAsync` and `JsonView`. So a page for this workflow cannot import the four
 * the contract names, and since `ui.jsx` is on the shared-file list the contract
 * says to "build it inside your own feature folder and say so in the PR
 * description" - which is what this file is.
 *
 * Two of the four are deliberately *not* reimplemented here:
 *
 *   - `Modal` is not used. Every decision this page supports is reversible or
 *     previewable, so a dialog that hid the member's current role behind an
 *     overlay would make the guard rails harder to see, not easier.
 *   - `Toggle` is not used. A boolean in this workflow is a seat or an `enabled`
 *     flag, and both are better as an explicit two-value control, because
 *     "which value is it now" is the question a reviewer asks.
 *
 * `Notice` and `Checkbox` are here because the page genuinely needs them, and
 * because they are the two most likely to be promoted into `ui.jsx` by an
 * integrator once a second feature asks for the same thing.
 */

import { Badge, Icon, inputClass } from '@/components/ui'

/** A block of quoted research, attributed. Sourced sentences are the page's spine. */
export function Quote({ children, source = '' }) {
  if (!children) return null
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-accent-soft/40 px-3 py-2">
      <blockquote className="text-[13px] leading-relaxed text-foreground">{children}</blockquote>
      {source && (
        <figcaption className="mt-1 text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
          {source}
        </figcaption>
      )}
    </figure>
  )
}

/**
 * An inline notice. Three tones, and the tone is always carried by a word as well
 * as by colour - a colour-only status is unreadable to somebody who cannot
 * distinguish the hues and useless in a printed transcript.
 */
export function Notice({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-info/40 bg-info/10 text-foreground',
    warn: 'border-warning/40 bg-warning/10 text-foreground',
    bad: 'border-destructive/40 bg-destructive/10 text-foreground',
  }
  const words = { info: 'Note', warn: 'Careful', bad: 'Refused' }
  return (
    <div className={`rounded-sm border p-3 ${tones[tone] || tones.info}`} role="status">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {words[tone] || words.info}
      </p>
      <div className="mt-1 text-[13px] leading-relaxed">
        <span className="font-medium">{title}</span>
        {children ? <> {children}</> : null}
      </div>
    </div>
  )
}

/**
 * A checkbox with a real 44px target.
 *
 * The visible box is 16px and the *target* is the whole row, which is what the
 * design floor asks for: a 16px hit area is not a target, and shrinking the box
 * to 44px would make a form of eight scopes look like a list of buttons.
 */
export function Checkbox({ checked, onChange, label, hint = '', disabled = false }) {
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-sm px-2 py-1.5 ${
        disabled ? 'cursor-not-allowed opacity-50' : 'hover:bg-muted/40'
      }`}
    >
      <input
        type="checkbox"
        checked={Boolean(checked)}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 rounded-xs accent-[#10506f]"
      />
      <span className="min-w-0">
        <span className="block font-mono text-[13px] text-foreground">{label}</span>
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </span>
    </label>
  )
}

/**
 * A select, built here because `ui.jsx` has no one.
 *
 * The role dropdown is the researched surface - "Workspace members panel with
 * role dropdown" - so its state has to be readable at a glance: the current role
 * is in the control, and the options that would be refused are still listed with
 * the reason beside them rather than silently removed. An admin needs to know
 * *why* the owner's dropdown will not take a new value, and an option that is
 * merely absent teaches nothing.
 */
export function RoleSelect({ value, onChange, options, disabled = false, id }) {
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      aria-label="Workspace role"
      className={`${inputClass} max-w-56`}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
          {option.blocked ? ` — ${option.blocked}` : ''}
        </option>
      ))}
    </select>
  )
}

/** A labelled fact, for the "so what" lines that are not rows of a table. */
export function Fact({ label, children }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <div className="mt-0.5 text-[13px] text-foreground">{children}</div>
    </div>
  )
}

/** A section heading with the researched rule it enforces beside it. */
export function SectionHead({ title, glyph, rule = '', id }) {
  return (
    <div className="mb-3" id={id}>
      <div className="flex items-center gap-2">
        {glyph && (
          <span className="rounded-sm bg-accent-soft p-1.5 text-accent">
            <Icon path={glyph} size={16} />
          </span>
        )}
        <h2 className="font-display text-[15px] font-semibold text-foreground">{title}</h2>
      </div>
      {rule && <p className="mt-1.5 text-xs text-muted-foreground">{rule}</p>}
    </div>
  )
}

/** A role's permissions, as monospace chips. */
export function PermissionChips({ permissions }) {
  if (!permissions || permissions.length === 0) {
    return <span className="text-xs text-muted-foreground">No permissions</span>
  }
  return (
    <div className="flex flex-wrap gap-1">
      {permissions.map((permission) => (
        <Badge key={permission} tone="neutral">
          {permission}
        </Badge>
      ))}
    </div>
  )
}