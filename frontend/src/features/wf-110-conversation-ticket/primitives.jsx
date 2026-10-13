/**
 * Primitives the conversation tickets page needs and the shared set cannot express.
 *
 * The shared `components/ui.jsx` exports Button, Card, StatCard, Badge, Field,
 * Modal, Notice, Toggle, Checkbox, Spinner, ErrorNote, EmptyState, Icon,
 * inputClass, useAsync and JsonView. Three things this page needs are missing, and
 * all three are built here rather than added to the shared file, which a feature
 * may not edit. The integrator promotes a recurring one into `ui.jsx` as platform
 * work, once.
 *
 * The design floor is met in this file rather than only in the page: 44px touch
 * targets on every control, a visible text label beside every glyph, and no status
 * conveyed by colour alone.
 */

import { Badge, Icon } from '@/components/ui'

/**
 * The nav glyph: a ticket with a check on it, drawn as the owned ticket.
 *
 * Passed as `path` rather than added to the shared `PATHS` map, because that map
 * is a shared file this feature may not edit.
 */
export const CONVERSATION_TICKET_ICON =
  'M4 5h16v6a3 3 0 000 6v2H4v-2a3 3 0 000-6V5zm5 4h6M9 13h3m3 4l2 2 4-4'

const GLYPHS = {
  ticket: 'M4 7h16v10H4V7zm4 3h8M8 14h5',
  team: 'M16 19v-2a4 4 0 00-4-4H7a4 4 0 00-4 4v2m6-10a3 3 0 100-6 3 3 0 000 6zm9 10v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8',
  conversation: 'M21 15a2 2 0 01-2 2H8l-4 4V5a2 2 0 012-2h13a2 2 0 012 2v10z',
  webhook: 'M10 13a5 5 0 007 0l2-2a5 5 0 00-7-7l-1 1m-1 3a5 5 0 00-7 0l-2 2a5 5 0 007 7l1-1',
  bell: 'M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zm0-14v5l3 2',
}

/** A short heading for a group of related fields, with an optional count. */
export function Subhead({ children, count }) {
  return (
    <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
      <span>{children}</span>
      {count !== undefined && <span className="font-mono text-foreground/70">{count}</span>}
    </h3>
  )
}

/** A key/value pair on one line, used in the detail panels. */
export function Fact({ label, children, mono = true }) {
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={`min-w-0 truncate ${mono ? 'font-mono text-foreground' : 'text-foreground'}`}>
        {children}
      </span>
    </div>
  )
}

/**
 * The researched sentence a behaviour came from, quoted.
 *
 * Every published constant in this workflow carries the sentence that fixes it, so
 * the page shows the sentence rather than asserting the value.
 */
export function Quote({ children, source }) {
  return (
    <figure className="rounded-sm border-l-2 border-accent bg-background/60 px-3 py-2">
      <blockquote className="text-xs text-foreground/90 italic">{children}</blockquote>
      {source && <figcaption className="mt-1 text-xs text-muted-foreground">{source}</figcaption>}
    </figure>
  )
}

/**
 * An inline note, for a warning beside a count or an explanation under a rule.
 *
 * The shared file does not export a `Notice`, so this primitive is built here and
 * the discrepancy is named in the pull request.
 */
export function Notice({ children, tone = 'info' }) {
  const tones = {
    info: 'border-border-subtle bg-muted text-muted-foreground',
    warning: 'border-warning/40 bg-warning/10 text-foreground',
    danger: 'border-destructive/40 bg-destructive/10 text-foreground',
    good: 'border-accent/30 bg-accent-soft text-foreground',
  }
  const labels = { info: 'Note', warning: 'Warning', danger: 'Problem', good: 'OK' }
  return (
    <div
      className={`flex items-start gap-2 rounded-sm border px-3 py-2 text-sm ${tones[tone] || tones.info}`}
      role={tone === 'danger' ? 'alert' : 'status'}
    >
      <span className="shrink-0 text-[11px] font-semibold tracking-[0.14em] uppercase opacity-80">
        {labels[tone] || labels.info}
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  )
}

/** One action, showing its kind as words and its outcome as words. */
export function ActionRow({ action, actionLabel, outcomeLabel }) {
  return (
    <li className="flex min-h-11 items-center gap-3 border-b border-border-subtle py-2 last:border-b-0">
      <Icon path={GLYPHS.team} size={15} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-foreground">{actionLabel(action.kind)}</span>
        <span className="block text-xs text-muted-foreground">
          {action.outcome ? outcomeLabel(action.outcome) : 'No outcome yet'}
        </span>
      </span>
      {action.outcome && (
        <Badge tone={action.outcome === 'ran' ? 'insert' : 'restore'}>
          {action.outcome === 'ran' ? 'Ran' : 'Skipped'}
        </Badge>
      )}
      {action.team && <span className="font-mono text-xs text-muted-foreground">{action.team}</span>}
    </li>
  )
}

/** One ticket row: its subject, state, owning team and converted flag. */
export function TicketRow({ ticket, selected, onSelect }) {
  const converted = ticket.converted
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(ticket.id)}
        aria-pressed={selected}
        className={`flex min-h-11 w-full items-center gap-3 rounded-sm border px-3 py-2 text-left transition-colors ${
          selected
            ? 'border-accent bg-accent-soft'
            : 'border-border-subtle bg-surface hover:border-accent'
        }`}
      >
        <Icon path={GLYPHS.ticket} size={16} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-foreground">{ticket.subject}</span>
          <span className="block font-mono text-xs text-muted-foreground">
            {ticket.ticket_type} / {ticket.owning_team || 'no team'}
          </span>
        </span>
        <Badge tone={converted ? 'insert' : 'neutral'}>
          {converted ? 'Converted' : 'Not converted'}
        </Badge>
      </button>
    </li>
  )
}

/** One emitted webhook, showing its topic, shape and parsed ticket id. */
export function WebhookRow({ webhook }) {
  const payload = webhook.data?.payload || {}
  const parsed = webhook.parsed_ticket || {}
  return (
    <li className="flex min-h-11 items-center gap-3 border-b border-border-subtle py-2 last:border-b-0">
      <Icon path={GLYPHS.webhook} size={15} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-xs text-foreground">{payload.type}</span>
        <span className="block text-xs text-muted-foreground">
          ticket {String(parsed.id || 'unparsed')}
        </span>
      </span>
      <Badge tone="neutral">{webhook.data?.shape}</Badge>
    </li>
  )
}

export const TICKET_GLYPHS = GLYPHS
