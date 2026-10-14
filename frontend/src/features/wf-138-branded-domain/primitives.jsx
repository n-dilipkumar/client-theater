/**
 * WF-138's local primitives.
 *
 * `components/ui.jsx` carries the shared set: Button, Card, StatCard, Badge,
 * Field, Spinner, ErrorNote, EmptyState, Icon, inputClass, useAsync, JsonView.
 * Everything below is built inside this feature folder because the shared file
 * does not have it, which is what `docs/FEATURE-CONTRACT.md` asks for rather
 * than adding to `ui.jsx`:
 *
 *   - `StatePill` puts the words beside the tone, so a host's state is never
 *     carried by colour alone;
 *   - `CheckField` is a labelled checkbox. `ui.jsx` has no checkbox, and a
 *     checkbox needs its own label wrapper to reach the 44px touch target;
 *   - `KeyValue` is the label/value row the brand panel is built from;
 *   - `VariableList` renders a token map as the custom properties it becomes;
 *   - `CheckList` renders a verification report, which is a shape both this
 *     workflow and WF-017 answer with.
 *
 * Every control carries a visible text label and reaches `min-h-11`. Nothing
 * here animates, and no colour value is written by hand: the only colours used
 * are the semantic tokens named in `docs/DESIGN-SYSTEM.md`.
 */

import { Badge, Card, Field, Icon, inputClass } from '@/components/ui'

import { STATE_TONES, STATE_WORDS } from './api'

export function StatePill({ state }) {
  const word = STATE_WORDS[state] || state || 'Unknown'
  return <Badge tone={STATE_TONES[state] || 'neutral'}>{word}</Badge>
}

export function KeyValue({ label, children }) {
  return (
    <div className="flex flex-col gap-0.5 py-1.5">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-sm text-foreground">{children}</dd>
    </div>
  )
}

export function CheckField({ id, label, hint, checked, onChange }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className="flex min-h-11 cursor-pointer items-center gap-3 text-[13px] font-medium text-foreground"
      >
        <input
          id={id}
          type="checkbox"
          checked={Boolean(checked)}
          onChange={(event) => onChange(event.target.checked)}
          className="h-5 w-5 rounded-xs border border-border-subtle accent-[var(--color-accent)]"
        />
        {label}
      </label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function TokenSelect({ id, label, value, options, onChange }) {
  return (
    <Field label={label} id={id} hint="Leave it on the product default to inherit this token.">
      <select
        id={id}
        className={inputClass}
        value={value || ''}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Product default</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </Field>
  )
}

export function VariableList({ variables }) {
  const entries = Object.entries(variables || {})
  if (!entries.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No override. Every token renders the product default.
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-1">
      {entries.map(([name, value]) => (
        <li key={name} className="font-mono text-[13px] text-foreground">
          {name}: {value}
        </li>
      ))}
    </ul>
  )
}

export function CheckList({ checks, title }) {
  return (
    <ul aria-label={title} className="flex flex-col gap-2">
      {(checks || []).map((check) => (
        <li key={check.name} className="flex flex-wrap items-baseline gap-2">
          <Badge tone={check.ok ? 'success' : 'destructive'}>{check.ok ? 'Pass' : 'Fail'}</Badge>
          <span className="text-sm text-foreground">{check.label}</span>
          <span className="text-xs text-muted-foreground">{check.detail}</span>
        </li>
      ))}
    </ul>
  )
}

export function CannotVerify({ notes }) {
  if (!notes || !notes.length) return null
  return (
    <Card>
      <h3 className="flex items-center gap-2 text-base font-semibold text-foreground">
        <Icon name="database" />
        What this deployment cannot verify
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">
        A verified CNAME shows the record exists. It does not show the account controls the domain,
        and it says nothing about the infrastructure below.
      </p>
      <ul className="mt-3 flex list-disc flex-col gap-1.5 pl-5 text-sm text-muted-foreground">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </Card>
  )
}
