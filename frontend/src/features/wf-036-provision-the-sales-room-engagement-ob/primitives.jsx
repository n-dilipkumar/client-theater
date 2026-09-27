/**
 * The four UI pieces this feature needs that the shared set in
 * `components/ui.jsx` cannot express. They are in their own module, and apart from
 * the glyphs, on purpose: the contract tells a feature that needs a primitive the
 * shared set lacks to build it locally and report it, so this file is the whole
 * of what an integrator has to look at.
 *
 * The promotion set, unambiguously:
 *
 *   - `StatTile` and `Note` are platform-shaped. The contract already lists
 *     `Notice` and `StatCard` among the primitives a feature may use, and this is
 *     now the second feature to need a stat card with a `path` and an inline
 *     confirmation, so both belong in `components/ui.jsx` once, as platform work.
 *     `StatTile` specifically is `StatCard` with a `path` prop that falls back to
 *     `name`; `Note` is `Notice` with a glyph this feature owns.
 *   - `DiffBadge` and `FindingRow` should *not* be promoted. Both encode this
 *     workflow's vocabulary - the five property actions, the three finding
 *     severities - and promoting them would put a CRM provisioning concept into a
 *     shared module that every feature imports. They are promoted only if a
 *     second feature needs the same five actions, and until then they are the
 *     right size where they are.
 *
 * All four meet the same floor as the shared primitives: a 44px minimum hit
 * target, a real focusable control, a visible text label, and no emoji as an
 * icon.
 */
import { Card, Icon } from '@/components/ui'
import Glyph from './icons'

/**
 * A single headline number with its label, hint and glyph.
 *
 * Structurally the shared `StatCard`, which is the first choice and is used
 * wherever a shared glyph fits. It cannot be used here because it only accepts a
 * `name` from the shared `PATHS` map, and every stat on this page is about
 * something the shared set has no glyph for. This takes a `path` instead.
 */
export function StatTile({ label, value, hint, path, tone = 'default' }) {
  const valueTone = {
    default: 'text-foreground',
    good: 'text-accent',
    warn: 'text-amber-300',
    bad: 'text-destructive',
  }[tone]

  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          <p className={`mt-2 font-mono text-3xl font-semibold ${valueTone}`}>{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        {path && (
          <span className="rounded-lg bg-muted p-2 text-accent">
            <Icon path={path} size={20} />
          </span>
        )}
      </div>
    </Card>
  )
}

/**
 * An inline note reporting a completed action or a standing warning.
 *
 * `role="status"` and `aria-live="polite"`, because it appears after the request
 * that caused it has already finished: it is a confirmation, not an alert. The
 * tone picks the colour and the glyph; the meaning is always in the text, never
 * in the colour alone.
 */
export function Note({ tone = 'good', children }) {
  const tones = {
    good: 'border-accent/40 bg-accent/10 text-accent',
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  }
  const glyph = { good: 'check', warn: 'warning', info: 'diff' }[tone]

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-center gap-2 rounded-lg border p-3 text-sm ${tones[tone]}`}
    >
      <Glyph name={glyph} size={16} />
      <span className="min-w-0">{children}</span>
    </div>
  )
}

/**
 * The diff's verdict about one property, as a badge.
 *
 * The five actions are the workflow's own closed vocabulary and they mean five
 * different things to a reader, so each carries a word and not only a colour:
 * `create` is something an install would do, `unchanged` is the idempotent case
 * the research cares about, `conflict` is something a person must decide,
 * `unmappable` is a field this build will not send, and `left_in_place` is a
 * column the manifest stopped declaring that nothing dropped.
 */
const ACTION_TONES = {
  create: { tone: 'insert', glyph: 'plus' },
  unchanged: { tone: 'neutral', glyph: 'check' },
  conflict: { tone: 'update', glyph: 'warning' },
  unmappable: { tone: 'delete', glyph: 'close' },
  left_in_place: { tone: 'restore', glyph: 'link' },
}

export function DiffBadge({ action, count }) {
  const style = ACTION_TONES[action] || ACTION_TONES.unchanged
  const text = count === undefined ? action : `${action} (${count})`

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-xs font-medium ${
        {
          'bg-accent/15 text-accent border-accent/30': style.tone === 'insert',
          'bg-muted text-muted-foreground border-border-subtle/40': style.tone === 'neutral',
          'bg-sky-500/15 text-sky-300 border-sky-500/30': style.tone === 'update',
          'bg-destructive/15 text-destructive border-destructive/30': style.tone === 'delete',
          'bg-amber-500/15 text-amber-300 border-amber-500/30': style.tone === 'restore',
        }[style.tone]
      }`}
    >
      <Glyph name={style.glyph} size={12} />
      {text}
    </span>
  )
}

/**
 * One manifest finding, with its severity, its message, and the path it is about.
 *
 * A finding is only actionable if it says *where*, so the path is shown next to
 * the sentence. The severity decides the colour and the glyph, and the code is in
 * monospace so a reader can search for it in the manifest.
 */
const SEVERITY = {
  blocking: { label: 'blocking', tone: 'delete', glyph: 'warning' },
  property: { label: 'skips a field', tone: 'update', glyph: 'warning' },
  advisory: { label: 'advisory', tone: 'neutral', glyph: 'diff' },
}

export function FindingRow({ finding }) {
  const style = SEVERITY[finding.severity] || SEVERITY.advisory
  const colour = {
    delete: 'text-destructive',
    update: 'text-sky-300',
    neutral: 'text-muted-foreground',
  }[style.tone]

  return (
    <li className="flex gap-2 border-b border-border-subtle/15 py-2 text-xs last:border-0">
      <span className="mt-0.5 shrink-0">
        <Glyph name={style.glyph} size={14} className={colour} />
        <span className="sr-only">{style.label}</span>
      </span>
      <div className="min-w-0">
        <p className="text-foreground/90">{finding.message}</p>
        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {finding.severity} · {finding.code}
          {finding.path ? ` · ${finding.path}` : ''}
        </p>
      </div>
    </li>
  )
}
