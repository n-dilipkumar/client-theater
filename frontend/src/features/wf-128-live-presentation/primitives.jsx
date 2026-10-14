/**
 * WF-128's own presentational pieces.
 *
 * Every one is built from the shared primitives in `@/components/ui`. Nothing
 * here reaches for a raw colour: status always carries a word, and the only
 * accent is the product's own, via the semantic tokens.
 *
 * Three things get deliberate treatment rather than a generic list row:
 *
 *   1. **The live transport is labelled a stand-in.** No SFU and no stream exist
 *      in this build, so a panel that looked like a live "on air" indicator would
 *      be a lie about what shipped.
 *   2. **The reveal order is drawn as an order.** Each step shows its position and
 *      the current step says "Now presenting", so a reader can see why a forward
 *      jump is refused without reading the server's message.
 *   3. **The replay says it is a reference.** The URL is a stand-in and the panel
 *      says so rather than drawing a player that would never play.
 */

import { Badge, Card, EmptyState } from '@/components/ui'

import {
  attendanceLabel,
  eventLabel,
  formatDateTime,
  formatSeconds,
  participantRoleLabel,
  participantStatusLabel,
  questionLabel,
  recordingLabel,
} from './api'

export function Section({ title, description, actions, children }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions}
      </div>
      <div className="mt-4">{children}</div>
    </Card>
  )
}

/** A labelled value. Machine values (ids, counts, links) are mono. */
export function Fact({ label, value, mono = false, hint }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {label}
      </p>
      <p className={`mt-1 truncate text-sm text-foreground ${mono ? 'font-mono' : ''}`}>
        {value ?? '-'}
      </p>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** An inline note. Not a status: it carries a label as well as a tone. */
export function InlineNote({ label, tone = 'info', children }) {
  const tones = {
    info: 'border-info/30 bg-info/10 text-info',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    neutral: 'border-border-subtle bg-muted text-muted-foreground',
  }
  return (
    <div className={`rounded-sm border p-3 text-sm ${tones[tone] || tones.neutral}`}>
      {label && <p className="font-medium">{label}</p>}
      <p className="mt-0.5 text-muted-foreground">{children}</p>
    </div>
  )
}

/**
 * The reveal order, drawn as an order.
 *
 * A step ahead of the cursor is "Not yet revealed", the current step says "Now
 * presenting", and an earlier step says "Already shown". The words carry the
 * state, so the tone is a second signal and never the only one.
 */
export function AgendaList({ agenda, position, revealed }) {
  if (!agenda || agenda.length === 0) {
    return <p className="text-sm text-muted-foreground">This session has no steps.</p>
  }
  return (
    <ol className="flex flex-col gap-2">
      {agenda.map((step) => {
        const seen = Number(revealed) >= Number(step.index)
        const current = Number(step.index) === Number(position)
        const state = current ? 'Now presenting' : seen ? 'Already shown' : 'Not yet revealed'
        const tone = current ? 'insert' : seen ? 'update' : 'neutral'
        return (
          <li
            key={step.index}
            className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border-subtle bg-surface px-3 py-2"
          >
            <span className="min-w-0">
              <span className="font-mono text-xs text-muted-foreground">
                {Number(step.index) + 1}/{agenda.length}
              </span>
              <span className="ml-3 text-sm text-foreground">{step.label}</span>
            </span>
            <Badge tone={tone}>{state}</Badge>
          </li>
        )
      })}
    </ol>
  )
}

export function ParticipantTable({ participants, onSync, busy }) {
  if (!participants || participants.length === 0) {
    return <EmptyState title="Nobody invited yet" description="Invite a buyer to join this session." />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border-subtle text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            <th className="py-2 pr-3 font-medium">Participant</th>
            <th className="py-2 pr-3 font-medium">Role</th>
            <th className="py-2 pr-3 font-medium">State</th>
            <th className="py-2 pr-3 font-medium">Their link</th>
            <th className="py-2 font-medium">Action</th>
          </tr>
        </thead>
        <tbody>
          {participants.map((row) => (
            <tr key={row.id} className="border-b border-border-subtle/60 align-top">
              <td className="py-2 pr-3">
                <span className="block text-foreground">{row.email}</span>
                {row.name && <span className="block text-xs text-muted-foreground">{row.name}</span>}
              </td>
              <td className="py-2 pr-3 text-muted-foreground">{participantRoleLabel(row.role)}</td>
              <td className="py-2 pr-3">
                <Badge tone={row.status === 'joined' ? 'insert' : 'neutral'}>
                  {participantStatusLabel(row.status)}
                </Badge>
              </td>
              <td className="py-2 pr-3">
                <span className="font-mono text-xs text-muted-foreground">{row.join_url || '-'}</span>
              </td>
              <td className="py-2">
                {row.role === 'follower' ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onSync(row.email)}
                    className="min-h-11 rounded-sm border border-border-subtle px-3 text-sm text-foreground hover:border-accent hover:text-accent disabled:opacity-60"
                  >
                    Simulate this follower joining
                  </button>
                ) : (
                  <span className="text-xs text-muted-foreground">Presents</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function QuestionList({ questions, onAnswer, busy }) {
  if (!questions || questions.length === 0) {
    return <EmptyState title="No questions yet" description="Questions asked live are logged against the deal." />
  }
  return (
    <ul className="flex flex-col gap-3">
      {questions.map((row) => (
        <li key={row.id} className="rounded-sm border border-border-subtle bg-surface p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs text-muted-foreground">{row.asker}</span>
            <Badge tone={row.status === 'answered' ? 'insert' : 'restore'}>
              {questionLabel(row.status)}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-foreground">{row.text}</p>
          {row.answer ? (
            <p className="mt-2 border-l border-border-subtle pl-3 text-sm text-muted-foreground">
              Answer: {row.answer}
            </p>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => onAnswer(row.id, 'Answered in the session.')}
              className="mt-2 min-h-11 rounded-sm border border-border-subtle px-3 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
            >
              Mark answered
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}

export function AttendanceTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <EmptyState title="No attendance yet" description="Attendance is written when a participant joins or the session ends." />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border-subtle text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            <th className="py-2 pr-3 font-medium">Participant</th>
            <th className="py-2 pr-3 font-medium">Attendance</th>
            <th className="py-2 pr-3 font-medium">Joined</th>
            <th className="py-2 pr-3 font-medium">Time present</th>
            <th className="py-2 font-medium">Written to</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-border-subtle/60">
              <td className="py-2 pr-3 text-foreground">{row.email}</td>
              <td className="py-2 pr-3">
                <Badge tone={row.attended ? 'insert' : 'neutral'}>{attendanceLabel(row.state)}</Badge>
              </td>
              <td className="py-2 pr-3 text-muted-foreground">{formatDateTime(row.joined_at_ms)}</td>
              <td className="py-2 pr-3 font-mono text-xs text-muted-foreground">
                {formatSeconds(row.seconds)}
              </td>
              <td className="py-2 font-mono text-xs text-muted-foreground">{row.written_to}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function EventLog({ events }) {
  if (!events || events.length === 0) {
    return <p className="text-sm text-muted-foreground">No events recorded yet.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {events.map((row) => (
        <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle/60 pb-2">
          <span className="min-w-0">
            <span className="text-sm text-foreground">{eventLabel(row.kind)}</span>
            {row.detail && <span className="ml-2 text-xs text-muted-foreground">{row.detail}</span>}
          </span>
          <span className="font-mono text-xs text-muted-foreground">{formatDateTime(row.at_ms)}</span>
        </li>
      ))}
    </ul>
  )
}

export function ReplayPanel({ recording }) {
  if (!recording) {
    return <p className="text-sm text-muted-foreground">No recording for this session yet.</p>
  }
  return (
    <div className="flex flex-col gap-3">
      <Badge tone={recording.status === 'published' ? 'insert' : 'restore'}>
        {recordingLabel(recording.status)}
      </Badge>
      <Fact label="Artefact reference" value={recording.replay_ref} mono />
      <Fact label="Playback URL (stand-in)" value={recording.playback_url} mono />
      <InlineNote label="Stand-in" tone="warning">
        {recording.hosting_note} The artefact would live in {recording.artefact_store}.
      </InlineNote>
    </div>
  )
}
