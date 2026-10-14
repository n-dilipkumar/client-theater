/**
 * WF-126's own primitives.
 *
 * The shared `components/ui.jsx` carries Button, Card, Badge, Field, Spinner,
 * ErrorNote, EmptyState and the rest, and this page uses them. What it adds here
 * is the shape of *this* workflow's data: a recording row, a viewer's roll-up, a
 * playback line, an alert and the hero preview. None of those exist in the shared
 * set, so they live in this feature's folder rather than being promoted into a
 * file a hundred features share. See docs/FEATURE-CONTRACT.md.
 */

import { Badge, Card, Icon } from '@/components/ui'

import { eventLabel, formatDateTime, percentText, placementLabel, stateLabel, stateTone } from './api'

/**
 * A play-in-a-frame glyph. It is not in the shared PATHS map, so it is passed as
 * a path rather than added to the map, which `components/ui.jsx` does not allow.
 */
export const VIDEO_GLYPH =
  'M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1zM10 8.5v7l6-3.5-6-3.5z'

export function Section({ title, action, children }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
        {action}
      </div>
      <div className="mt-4 flex flex-col gap-3">{children}</div>
    </Card>
  )
}

export function Fact({ label, hint, children }) {
  return (
    <div className="flex flex-col gap-1 rounded-sm border border-border-subtle bg-surface p-3">
      <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </span>
      <span className="text-sm text-foreground">{children}</span>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </div>
  )
}

/** A one-line note. The tone is always paired with the sentence, never colour alone. */
export function InlineNote({ tone = 'info', children }) {
  const tones = {
    info: 'border-info/30 bg-info/10 text-info',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    success: 'border-success/30 bg-success/10 text-success',
    error: 'border-destructive/30 bg-destructive/10 text-destructive',
  }
  return (
    <p className={`rounded-sm border px-3 py-2 text-sm ${tones[tone] || tones.info}`}>{children}</p>
  )
}

export function RecordingRow({ recording, selected, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(recording.id)}
      className={`flex min-h-11 w-full flex-wrap items-center justify-between gap-2 rounded-sm border bg-surface px-3 py-2 text-left hover:border-accent ${
        selected ? 'border-accent' : 'border-border-subtle'
      }`}
    >
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-sm text-foreground">{recording.title}</span>
        <span className="font-mono text-xs text-muted-foreground">
          {recording.kind_label} &middot; {recording.source_label} &middot; {recording.id}
        </span>
      </span>
      <span className="flex flex-wrap items-center gap-2">
        <Badge tone={recording.status === 'placed' ? 'insert' : 'neutral'}>{recording.status}</Badge>
        <span className="text-xs text-muted-foreground">
          {recording.duration_seconds ? `${recording.duration_seconds}s` : 'length unknown'}
        </span>
      </span>
    </button>
  )
}

export function EngagementRow({ row }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle bg-surface px-3 py-2">
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-mono text-xs text-foreground">{row.viewer}</span>
        <span className="text-xs text-muted-foreground">
          {row.events} event(s) &middot; {row.replays} replay(s)
        </span>
      </span>
      <span className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-foreground">{percentText(row.max_percent)}</span>
        <Badge tone={stateTone(row.state)}>{stateLabel(row.state)}</Badge>
      </span>
    </li>
  )
}

export function EventLog({ events }) {
  if (!events.length) {
    return <p className="text-sm text-muted-foreground">No playback event has been recorded yet.</p>
  }
  return (
    <ul className="flex flex-col gap-1">
      {events.map((event) => (
        <li key={event.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="flex min-w-0 flex-col">
            <span className="text-foreground">{eventLabel(event.event)}</span>
            <span className="truncate font-mono text-xs text-muted-foreground">{event.viewer}</span>
          </span>
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">{percentText(event.watched_percent)}</span>
            <span>{formatDateTime(event.at_ms)}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

export function AlertRow({ alert }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-warning/30 bg-warning/10 px-3 py-2">
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-mono text-xs text-foreground">{alert.viewer}</span>
        <span className="text-xs text-muted-foreground">{alert.message}</span>
      </span>
      <Badge tone={alert.status === 'open' ? 'restore' : 'neutral'}>{alert.status}</Badge>
    </li>
  )
}

export function NotificationRow({ note }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <span className="flex min-w-0 flex-col">
        <span className="text-foreground">{note.message}</span>
        <span className="truncate font-mono text-xs text-muted-foreground">{note.recipient}</span>
      </span>
      <span className="text-xs text-muted-foreground">{formatDateTime(note.at_ms)}</span>
    </li>
  )
}

/**
 * The hero as the buyer reads it. The player is not rendered: this build hosts
 * no media, so the card shows the reference and the message and says plainly
 * that the URL is a stand-in.
 */
export function HeroPreview({ hero }) {
  if (!hero) {
    return (
      <p className="text-sm text-muted-foreground">
        No video is on this room&apos;s hero yet. Place a recording to put one there.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Icon path={VIDEO_GLYPH} size={20} />
        </span>
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-foreground">{hero.recording.title}</h3>
          <p className="mt-1 text-sm text-muted-foreground">{hero.message}</p>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Surface">{hero.surface}</Fact>
        <Fact label="Personalised for">
          {hero.placement.personalised_for.length
            ? hero.placement.personalised_for.length + ' buyer(s)'
            : 'every buyer'}
        </Fact>
        <Fact label="Status">{placementLabel(hero.placement.status)}</Fact>
        <Fact label="Player" hint={hero.hosting_note}>
          <span className="font-mono text-xs">{hero.placement.player.url}</span>
        </Fact>
      </div>
    </div>
  )
}

export function UserFlow({ steps }) {
  if (!steps || !steps.length) return null
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((step) => (
        <li key={step.step} className="flex gap-3 text-sm">
          <span className="font-mono text-xs text-muted-foreground">{step.step}</span>
          <span className="text-foreground">{step.text}</span>
        </li>
      ))}
    </ol>
  )
}

export function WordList({ items }) {
  if (!items || !items.length) return <p className="text-sm text-muted-foreground">None.</p>
  return (
    <ul className="flex flex-col gap-1">
      {items.map((item) => (
        <li key={item} className="text-sm text-foreground">
          {item}
        </li>
      ))}
    </ul>
  )
}
