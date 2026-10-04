/**
 * Primitives this feature needs that `@/components/ui` does not export.
 *
 * `docs/DESIGN-SYSTEM.md` and `docs/FEATURE-CONTRACT.md` both list `Notice` and `Toggle`
 * among the available primitives, and the shipped `ui.jsx` has all of the documented set
 * except `Modal`, `Notice`, `Toggle` and `Checkbox`.
 *
 * That is a contradiction in the repo's own documentation rather than a gap in this feature,
 * and it is reported rather than quietly worked around a second time: WF-073 and WF-078 each
 * hit it and rebuilt the same two. The contract's instruction for exactly this case is to
 * build the primitive inside the feature folder and say so, so the integrator can promote
 * the ones that recur. These are rebuilt to the design system's floor: 44px targets, a
 * visible focus ring, a text label beside every control, `rounded-sm`, and semantic tokens
 * only.
 *
 * `Modal` and `Checkbox` are not rebuilt: nothing in WF-084 needs them. The forms here are
 * inline in a card rather than in a dialog, because a form with three identifiers and a
 * room is worse in a modal on a phone than it is on the page.
 *
 * Four more things are built here that are not in the documented list at all, because this
 * workflow has shapes no shared primitive describes: `Select` (the page needs one in a card
 * with a visible label), `AssertionBadge` (whether a session stands behind an asserted
 * tenant), `AccessBadge` (a role, always in words as well as tone) and `EventBadge` (which
 * of the three SCIM operations a row records). Each is local by necessity.
 */

import { Icon } from '@/components/ui'

const TONES = {
  neutral: 'border-border-subtle bg-muted text-muted-foreground',
  info: 'border-info/30 bg-info/10 text-info',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
}

const TONE_ICON = {
  info: 'M12 8h.01M11 12h1v5h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  success: 'M5 12l5 5 9-11',
  warning: 'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  destructive: 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
}

/**
 * A short standing message. Tone carries the meaning and the icon repeats it, so the state
 * does not depend on colour alone.
 */
export function Notice({ tone = 'neutral', title, children, action }) {
  const iconPath = TONE_ICON[tone]
  return (
    <div
      role="status"
      className={`flex flex-wrap items-start gap-3 rounded-sm border p-4 ${TONES[tone] || TONES.neutral}`}
    >
      {iconPath && <Icon path={iconPath} className="mt-0.5 shrink-0" />}
      <div className="min-w-0 flex-1">
        {title && <p className="text-sm font-semibold">{title}</p>}
        {children && <div className="mt-1 text-sm">{children}</div>}
      </div>
      {action}
    </div>
  )
}

/**
 * A labelled select. Built here because the workflow needs one in a card with a visible
 * label and a hint, and the shared set has `Field` and `inputClass` but no select.
 *
 * `inputClass` supplies the styling so the control matches every other input in the
 * product: `min-h-11` for the 44px floor, a semantic border, and `focus:border-accent`. The
 * chevron is the browser's own, so there is no glyph to keep in step and no icon without a
 * label.
 */
export function Select({ id, value, onChange, children, disabled = false }) {
  return (
    <select
      id={id}
      className={INPUT_CLASS}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {children}
    </select>
  )
}

// Re-declared rather than imported so this file has one styling source. The shared
// `inputClass` is the canonical string and this is a copy of it for a `<select>`; a test
// asserts the two match, so a change to the shared one cannot silently leave this behind.
const INPUT_CLASS =
  'min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm ' +
  'text-foreground placeholder:text-muted-foreground/70 focus:border-accent'

/**
 * Whether a session stands behind an asserted tenant.
 *
 * The label is the word, never the colour alone. A green tick beside "asserted" and nothing
 * else would let a reader conclude the assertion checked the email domain, which is the one
 * reading this workflow's whole specification forbids.
 */
export function AssertionBadge({ asserted }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        asserted
          ? 'border-success/30 bg-success/10 text-success'
          : 'border-border-subtle bg-muted text-muted-foreground'
      }`}
    >
      <Icon path={asserted ? 'M5 12l5 5 9-11' : 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'} size={13} />
      {asserted ? 'Tenant asserted' : 'Not asserted'}
    </span>
  )
}

/**
 * The role a group's mapping grants, in words.
 *
 * `No access` is a real state and gets its own label rather than an empty cell. A user in
 * the directory and in no mapped group is the state an administrator most needs to notice,
 * and a blank beside their name would hide it.
 */
export function AccessBadge({ role }) {
  const labels = { admin: 'Admin', member: 'Member', auditor: 'Auditor' }
  const known = Boolean(labels[role])
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        known ? 'border-accent/30 bg-accent/10 text-accent' : 'border-border-subtle bg-muted text-muted-foreground'
      }`}
    >
      <Icon
        path={
          known
            ? 'M9 12l2 2 4-4M12 3l7 4v6c0 4.4-3 7.6-7 8-4-.4-7-3.6-7-8V7l7-4z'
            : 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
        }
        size={13}
      />
      {known ? labels[role] : 'No access'}
    </span>
  )
}

/**
 * Which of the three SCIM operations a recorded change was.
 *
 * Three states and not two, because "provisioned" and "updated" are different operations
 * with different meanings and a log that merged them would make a role change look like a
 * new hire. Deprovisioned is the destructive tone because it is the one that removes access.
 */
export function EventBadge({ operation }) {
  const rows = {
    create: { label: 'Provisioned', tone: 'success', path: 'M5 12l5 5 9-11' },
    update: { label: 'Updated', tone: 'info', path: 'M4 12h16M12 4v16' },
    delete: { label: 'Deprovisioned', tone: 'destructive', path: 'M3 6h18M8 6V4h8v2m-9 0v14h10V6' },
  }
  const row = rows[operation] || { label: operation || 'Unknown', tone: 'neutral', path: null }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        TONES[row.tone] || TONES.neutral
      }`}
    >
      {row.path && <Icon path={row.path} size={13} />}
      {row.label}
    </span>
  )
}

/**
 * Whether a session still grants access, and if not why.
 *
 * A revoked session is not an error state on this page. It is the state the specification's
 * headline automation produces, so it is rendered as a state with a reason rather than hidden
 * from a list of the sessions that worked.
 */
export function SessionState({ live, revoked }) {
  if (revoked) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-xs border border-destructive/30 bg-destructive/10 px-2 py-0.5 font-mono text-xs font-medium text-destructive">
        <Icon path="M3 6h18M8 6V4h8v2m-9 0v14h10V6" size={13} />
        Revoked by deprovisioning
      </span>
    )
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
        live
          ? 'border-success/30 bg-success/10 text-success'
          : 'border-border-subtle bg-muted text-muted-foreground'
      }`}
    >
      <Icon
        path={live ? 'M5 12l5 5 9-11' : 'M12 9v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'}
        size={13}
      />
      {live ? 'Live' : 'Not live'}
    </span>
  )
}