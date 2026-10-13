/**
 * The few shapes this workflow needs that the shared primitives do not carry.
 *
 * Everything that exists in `@/components/ui` is imported from there and never copied.
 * `Badge`, `Button`, `Card`, `EmptyState`, `ErrorNote`, `Field`, `Icon`, `Spinner`, `StatCard`,
 * `JsonView`, `inputClass` and `useAsync` are all shared. This folder holds only what that file
 * genuinely does not export, and it says so rather than quietly reimplementing a primitive
 * somebody else owns.
 *
 * Three rules every component here obeys, because they are the accessibility floor rather than
 * styling:
 *
 * - **No status is conveyed by colour alone.** Every badge renders its status as text.
 * - **Every icon sits beside a text label** or carries an `aria-label`. An icon alone is never
 *   the only label.
 * - **Every control is at least 44px tall** (`min-h-11`), because it is a target somebody has to
 *   hit with a finger.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * The nav glyph: a sequence of blocks with a branch curving off it, which is a nurture path.
 * Passed as `iconPath` rather than as an `icon` name, because the shared `PATHS` map is not ours
 * to edit and this glyph is not in it.
 */
export const NURTURE_ICON =
  'M4 6h5M4 12h5M4 18h5M14 6h6M14 18h6M11 6v12M11 9h3M11 15h3'

/** The four block kinds' own glyphs, drawn here so each block card has a distinct mark. */
export const BLOCK_ICONS = {
  rule: 'M4 6h16M4 12h10M4 18h7M18 9l3 3-3 3',
  content: 'M4 5h16v14H4zM4 9h16M8 13h8M8 16h5',
  wait: 'M12 7v5l3 2M4 12a8 8 0 1 0 8-8 8 8 0 0 0-8 8z',
  tag: 'M4 4h9l7 7-9 9-7-7zM8 8h.01',
}

/**
 * A short block of text that carries a tone: the researched invariants, a skip reason, a
 * refusal, a recorded limit. Not an error -- `ErrorNote` is the error and is used instead
 * wherever the request actually failed.
 */
export function Notice({ tone = 'neutral', title, children }) {
  const tones = {
    neutral: 'border-border-subtle bg-muted text-foreground',
    info: 'border-border-subtle bg-accent-soft text-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    success: 'border-success/40 bg-success/10 text-foreground',
  }
  return (
    <div className={`rounded-sm border p-4 ${tones[tone] || tones.neutral}`}>
      {title && <p className="text-sm font-semibold">{title}</p>}
      {children && <div className="mt-1 text-sm text-muted-foreground">{children}</div>}
    </div>
  )
}

/**
 * A series or run state as text, never colour alone. The label is the published one from the
 * vocabulary, so a new state reaches the page without a change here.
 */
export function StateBadge({ state, labels = {} }) {
  const tone =
    state === 'live' || state === 'finished'
      ? 'insert'
      : state === 'exited'
        ? 'restore'
        : state === 'waiting'
          ? 'update'
          : 'neutral'
  return <Badge tone={tone}>{labels[state] || state}</Badge>
}

/**
 * A duration rendered in the words a seller would use. The numbers are seconds, and a raw
 * `2592000` on a page tells a reader nothing; this says "30 days".
 */
export function humaniseDuration(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value)) return 'unknown'
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.round(value / 60)}m`
  if (value < 86400) return `${Math.round(value / 3600)}h`
  return `${Math.round(value / 86400)}d`
}

/** One block of a series: its kind, its mark and its configured values. */
export function BlockCard({ block, labels = {}, channelLabels = {}, contentTypeLabels = {} }) {
  return (
    <li className="rounded-sm border border-border-subtle bg-surface p-3">
      <div className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-1.5 text-accent">
          <Icon path={BLOCK_ICONS[block.kind]} size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">
            {labels[block.kind] || block.kind}
            <span className="ml-2 font-mono text-xs text-muted-foreground">{block.id}</span>
          </p>
          <BlockDetail
            block={block}
            channelLabels={channelLabels}
            contentTypeLabels={contentTypeLabels}
          />
        </div>
      </div>
    </li>
  )
}

function BlockDetail({ block, channelLabels, contentTypeLabels }) {
  if (block.kind === 'rule') {
    const filters = block.filters || []
    return (
      <div className="mt-1 text-xs text-muted-foreground">
        {filters.length === 0 && block.role === 'entry' && <p>Everyone enters.</p>}
        {filters.map((rule, index) => (
          <p key={`${rule.field}-${index}`} className="font-mono">
            {rule.field} {rule.op} {String(rule.value)}
          </p>
        ))}
        {block.condition && <p>When {block.condition}</p>}
        {block.wait_mode && (
          <p>
            try to match {block.wait_mode === 'once' ? 'once' : `for ${humaniseDuration(block.match_within_seconds)}`}
          </p>
        )}
      </div>
    )
  }
  if (block.kind === 'content') {
    return (
      <div className="mt-1 text-xs text-muted-foreground">
        <p>
          {channelLabels[block.channel] || block.channel}
          {block.content_type ? ` (${contentTypeLabels[block.content_type] || block.content_type})` : ''}
          {block.goal ? ` - goal: ${block.goal}` : ''}
        </p>
        {block.fallback_enabled && (
          <p>
            send window {humaniseDuration(block.send_window_seconds)}, fallback{' '}
            {channelLabels[block.fallback_channel] || block.fallback_channel}
          </p>
        )}
      </div>
    )
  }
  if (block.kind === 'wait') {
    return <p className="mt-1 text-xs text-muted-foreground">Wait {humaniseDuration(block.duration_seconds)}</p>
  }
  return <p className="mt-1 text-xs text-muted-foreground">Tag {block.tag}</p>
}
