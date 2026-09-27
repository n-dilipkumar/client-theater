/**
 * Primitives the duplicate guard page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` carries Button, Card, StatCard, Badge, Field,
 * Modal, Notice, Toggle, Checkbox, Spinner, ErrorNote, EmptyState, inputClass,
 * useAsync and JsonView. Two things are missing for this page, and both are
 * built here rather than added to the shared file, which a feature may not edit.
 * The integrator promotes a recurring one into `ui.jsx` as platform work, once.
 */

import { Icon } from '@/components/ui'

/**
 * An outcome chip: a glyph plus its label, so the outcome never relies on colour
 * or on the glyph alone. The design floor requires a text label beside every
 * icon, and a decision log is exactly where that matters most - six outcomes
 * that differ only by hue would be unreadable to a colour-blind rep and in a
 * screenshot printed in black and white.
 */
const OUTCOME_TONES = {
  created: { tone: 'insert', glyph: 'plus' },
  updated: { tone: 'update', glyph: 'update' },
  blocked: { tone: 'neutral', glyph: 'blocked' },
  created_duplicate: { tone: 'neutral', glyph: 'allow' },
  hard_blocked: { tone: 'delete', glyph: 'duplicate' },
  escalated: { tone: 'restore', glyph: 'escalate' },
}

const OUTCOME_LABELS = {
  created: 'created',
  updated: 'updated',
  blocked: 'blocked',
  created_duplicate: 'duplicate created',
  hard_blocked: 'hard blocked',
  escalated: 'needs a human',
}

export function OutcomeChip({ outcome, glyphs }) {
  const spec = OUTCOME_TONES[outcome] || { tone: 'neutral', glyph: 'schema' }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono
        text-xs font-medium ${
          spec.tone === 'insert'
            ? 'border-accent/30 bg-accent/15 text-accent'
            : spec.tone === 'update'
              ? 'border-sky-500/30 bg-sky-500/15 text-sky-300'
              : spec.tone === 'delete'
                ? 'border-destructive/30 bg-destructive/15 text-destructive'
                : spec.tone === 'restore'
                  ? 'border-amber-500/30 bg-amber-500/15 text-amber-300'
                  : 'border-border-subtle/40 bg-muted text-muted-foreground'
        }`}
    >
      <Icon path={glyphs[spec.glyph]} size={13} />
      {OUTCOME_LABELS[outcome] || outcome}
    </span>
  )
}

/**
 * A key/value pair on one line, used in the detail panels.
 *
 * Not a `<dt>`/`<dd>` pair so it can be used in a flex row without a `<dl>`
 * around it, and it keeps the label from wrapping away from its value.
 */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className={`min-w-0 truncate ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}>
        {children}
      </dd>
    </div>
  )
}

/** An inline note for a success message, matching the shared Notice's tone. */
export function Note({ children }) {
  return (
    <p
      role="status"
      aria-live="polite"
      className="rounded-lg border border-accent/30 bg-accent/10 p-3 text-sm text-foreground"
    >
      {children}
    </p>
  )
}
