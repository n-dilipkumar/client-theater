/**
 * WF-116's local primitives.
 *
 * They are drawn from `@/components/ui` rather than re-implemented: `Badge`,
 * `Card` and `Icon` come from the shared set. Anything here only composes them,
 * so this feature cannot drift from the product's dialog or badge style.
 */

import { Badge, Card, Icon } from '@/components/ui'

import {
  formatDateTime,
  priorityLabel,
  priorityTone,
  statusLabel,
  statusTone,
  typeLabel,
} from './api'

/** A labelled fact. The label sits beside the value, never in a tooltip. */
export function Fact({ label, hint, children }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-sm text-foreground">{children}</p>
      {hint && <p className="mt-0.5 truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** The status, always as a word. The tone is a second signal, never the only one. */
export function StatusBadge({ status }) {
  return <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
}

/** The priority, always as a word. */
export function PriorityBadge({ priority }) {
  return <Badge tone={priorityTone(priority)}>{priorityLabel(priority)}</Badge>
}

/** An inline message. Tones use semantic tokens only. */
export function InlineNote({ tone = 'info', children }) {
  const tones = {
    info: 'border-info/30 bg-info/10 text-info',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    danger: 'border-destructive/30 bg-destructive/10 text-destructive',
  }
  return (
    <p className={`rounded-sm border px-3 py-2 text-sm ${tones[tone] || tones.info}`}>{children}</p>
  )
}

/** The three lifecycle triggers a task has fired, oldest first. */
export function TriggerList({ events }) {
  if (!events || events.length === 0) {
    return <p className="text-sm text-muted-foreground">No lifecycle events yet.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {events.map((event) => (
        <li key={event.id} className="flex flex-wrap items-center gap-2">
          <Icon name="refresh" />
          <span className="text-sm text-foreground">{event.trigger}</span>
          <span className="font-mono text-xs text-muted-foreground">{formatDateTime(event.at_ms)}</span>
        </li>
      ))}
    </ul>
  )
}

/** One task in a list. The whole row is a button so the target is large. */
export function TaskRow({ task, selected, onSelect }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(task.id)}
        aria-pressed={selected}
        className={`flex min-h-11 w-full flex-col gap-1 rounded-sm border px-3 py-2 text-left
          ${selected ? 'border-accent bg-accent-soft' : 'border-border-subtle bg-surface hover:border-accent'}`}
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{task.subject || task.id}</span>
          <StatusBadge status={task.status} />
          <PriorityBadge priority={task.priority} />
        </span>
        <span className="text-xs text-muted-foreground">
          {typeLabel(task.task_type)} ·{' '}
          {task.standalone ? 'Standalone' : 'Attached to a contact'} ·{' '}
          {formatDateTime(task.due_at_ms)}
        </span>
      </button>
    </li>
  )
}

/** The contacts a task may attach to, as a labelled list. */
export function ContactField({ contacts, value, onChange, id, label, hint }) {
  return (
    <label htmlFor={id} className="flex flex-col gap-1.5">
      <span className="text-[13px] font-medium text-foreground">{label}</span>
      <select id={id} className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground focus:border-accent" value={value} onChange={onChange}>
        <option value="">No contact (standalone internal task)</option>
        {(contacts || []).map((contact) => (
          <option key={contact.id} value={contact.id}>
            {contact.label || contact.id}
          </option>
        ))}
      </select>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </label>
  )
}

/** A small card shell for a titled block. */
export function Section({ title, action, children }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-base font-semibold text-foreground">{title}</h2>
        {action}
      </div>
      {children}
    </Card>
  )
}
