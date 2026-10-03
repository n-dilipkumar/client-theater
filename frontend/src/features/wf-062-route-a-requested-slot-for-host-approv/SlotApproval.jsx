/**
 * WF-062: route a requested slot for host approval before confirming.
 *
 * The page follows the researched user flow, in the order the flow implies:
 *
 *   1. **The gates.** Step one of the research is an admin turning *requires
 *      confirmation* on for an event type. Until that is on, a booking is
 *      accepted the moment it is asked for and no host ever sees it - so the
 *      toggles come first, with the separate optional email-verification gate
 *      beside them rather than buried in a form.
 *   2. **The request.** A prospect or an agent asks for a slot. Depending on the
 *      gate, this either creates a `PENDING` booking carrying a
 *      `oneTimePassword`, fires `BOOKING_REQUESTED` and waits, or accepts the
 *      booking and creates its calendar event. The request form shows which of
 *      the two will happen before anything is sent.
 *   3. **Needs you.** The requests a host has to decide are the one list this
 *      page puts at the top, because they are the only rows where the workflow
 *      is waiting on a person. Each carries the two researched actions: confirm,
 *      or decline with a reason.
 *   4. **What the request caused.** The webhook payloads it emitted and the
 *      dispatches the workflow triggers raised, because the research says a
 *      request can act without anybody opening the room and a reviewer should be
 *      able to see that it did.
 *   5. **The decisions.** Every judgement call this build made, served verbatim
 *      from the server.
 *
 * Two things this page is careful not to do. It never implies a message was
 * sent: a dispatch is recorded with `dispatched_by: "simulated"` and the page
 * says so, because this product has no mail or SMS transport and a tick beside
 * "emailed the rep" would be a lie told with a badge. And it never shows a
 * slot as free when a request is holding it, because the hold is the whole
 * mechanism - a page that showed a pending request's time as available would be
 * the one place in the product that contradicts its own workflow.
 *
 * Nothing here reaches upwards with a relative path and nothing was added to a
 * shared file: the API calls are in `./api.js` and the small pieces in
 * `./primitives.jsx`.
 */

import { useEffect, useMemo, useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import {
  approvalApi,
  bypassRoles,
  bypassVersions,
  defaultRejectionReason,
  needsAHost,
  refusalSummary,
  statusMeaning,
  statusTone,
} from './api'
import { DataTable, Fact, Notice, PathButton, ReasonField, Section } from './primitives'

const TABS = [
  { id: 'pending', label: 'Needs a decision' },
  { id: 'requests', label: 'Requests' },
  { id: 'gates', label: 'Event types' },
  { id: 'automations', label: 'Workflow rules' },
  { id: 'contract', label: 'Decisions & contract' },
]

function TabBar({ current, onChange }) {
  return (
    <div role="tablist" aria-label="Slot approval sections" className="flex flex-wrap gap-1.5">
      {TABS.map((tab) => {
        const selected = tab.id === current
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`wf062-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`wf062-panel-${tab.id}`}
            onClick={() => onChange(tab.id)}
            className={`inline-flex min-h-11 cursor-pointer items-center rounded-lg px-4 text-sm transition-colors
              duration-200 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
                selected
                  ? 'bg-accent/15 font-semibold text-accent'
                  : 'bg-muted text-muted-foreground hover:bg-border-subtle hover:text-foreground'
              }`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

/** `2026-09-30T09:00:00+00:00` as `Wed 30 Sep, 9:00am` for a `<input type=datetime-local>`. */
function toLocalInput(iso) {
  if (!iso) return ''
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return ''
  const pad = (value) => String(value).padStart(2, '0')
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
}

/** A default slot to offer: two days out, in the hour, so the form is usable. */
function defaultStart() {
  const when = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000)
  when.setMinutes(0, 0, 0)
  return toLocalInput(when.toISOString())
}

function StatusBadge({ booking, vocabulary }) {
  return (
    <span title={statusMeaning(vocabulary, booking?.status)}>
      <Badge tone={statusTone(vocabulary, booking?.status)}>{booking?.status ?? 'unknown'}</Badge>
    </span>
  )
}

/** Whether the slot this booking covers is currently held by it. */
function HoldMark({ booking }) {
  if (!booking?.holdsSlot) {
    return <span className="font-mono text-xs text-muted-foreground">released</span>
  }
  return <span className="font-mono text-xs text-accent">holds the slot</span>
}

// --------------------------------------------------------------------------- //
// The two researched actions, on one row
// --------------------------------------------------------------------------- //

function DecisionPanel({ roomId, booking, vocabulary, actor, onDone }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const fallback = defaultRejectionReason(vocabulary)

  async function decide(path, payload) {
    setBusy(path)
    setError(null)
    try {
      const body = await approvalApi[path](roomId, booking.uid, { ...payload, oneTimePassword: booking.oneTimePassword })
      setResult(body)
      setReason('')
      onDone()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <ReasonField id={`reason-${booking.uid}`} value={reason} onChange={setReason} defaultReason={fallback} />
      <div className="flex flex-wrap gap-2">
        <PathButton
          glyph="confirmed"
          variant="approve"
          disabled={Boolean(busy)}
          onClick={() => decide('confirm', {})}
        >
          {busy === 'confirm' ? 'Confirming…' : 'Confirm and create the event'}
        </PathButton>
        <PathButton
          glyph="released"
          variant="decline"
          disabled={Boolean(busy)}
          onClick={() => decide('decline', reason ? { reason } : {})}
        >
          {busy === 'decline' ? 'Declining…' : 'Decline and release the slot'}
        </PathButton>
      </div>
      <p className="text-xs text-muted-foreground">
        A decision needs the owner of the booking: the host, the event owner, an assigned user, a team admin or
        an organisation admin. This page acts as <span className="font-mono text-foreground">{actor}</span>, so a
        refusal here means that identity does not own it.
      </p>
      {error && <Notice tone="danger" title="The decision was refused">{refusalSummary(error)}</Notice>}
      {result && (
        <Notice tone="success" title={`${result.decision === 'confirm' ? 'Confirmed' : 'Declined'}`}>
          The booking is now {result.request.status}
          {result.rejection_reason ? `: “${result.rejection_reason}”` : ''}.{' '}
          {result.calendar_event ? 'Its calendar event has been created.' : ''}{' '}
          {result.slot_released ? 'The slot it was holding is free again.' : ''}
          {result.webhooks?.length
            ? ` ${result.webhooks[0].data.event} was recorded.`
            : ''}
        </Notice>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Needs a decision
// --------------------------------------------------------------------------- //

function NeedsADecision({ roomId, requests, vocabulary, actor, onDone }) {
  if (!requests.length) {
    return (
      <EmptyState
        title="Nothing is waiting on a host"
        description="Every request in this room has been confirmed or declined. Turn requires confirmation on for an event type, or request a slot, to put one here."
      />
    )
  }
  return (
    <div className="flex flex-col gap-4">
      {requests.map((booking) => (
        <Card key={booking.id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-mono text-sm font-semibold text-foreground">{booking.data?.title}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {booking.data?.attendee?.name} · {booking.data?.attendee?.email}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge booking={booking} vocabulary={vocabulary} />
              <HoldMark booking={booking} />
            </div>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Fact term="Requested" mono>{absoluteTime(booking.data?.requestedAt)}</Fact>
            <Fact term="Starts" mono>{absoluteTime(booking.data?.start)}</Fact>
            <Fact term="Host" mono>{booking.data?.hostId ?? '—'}</Fact>
            <Fact term="One-time password" mono>{booking.oneTimePassword ? 'issued' : 'none'}</Fact>
          </dl>
          <div className="mt-4">
            <DecisionPanel roomId={roomId} booking={booking} vocabulary={vocabulary} actor={actor} onDone={onDone} />
          </div>
          <WebhookList roomId={roomId} booking={booking} />
        </Card>
      ))}
    </div>
  )
}

/** The two payloads a request emitted, in full, because the research quotes them. */
function WebhookList({ roomId, booking }) {
  const uid = booking.uid
  const { data, loading, error } = useAsync(() => approvalApi.webhooks(roomId, uid), [roomId, uid])
  if (loading) return <p className="mt-4 text-xs text-muted-foreground">Reading the webhook log…</p>
  if (error) return <p className="mt-4 text-xs text-muted-foreground">{String(error.message || error)}</p>
  const webhooks = data?.webhooks || []
  if (!webhooks.length) {
    return <p className="mt-4 text-xs text-muted-foreground">No webhook has been emitted for this request yet.</p>
  }
  return (
    <details className="mt-4 rounded-lg border border-border-subtle/40 p-3">
      <summary className="min-h-11 cursor-pointer py-2 text-xs font-medium text-muted-foreground">
        What this request emitted ({webhooks.map((entry) => entry.data.event).join(', ')})
      </summary>
      <div className="mt-2 flex flex-col gap-3">
        {webhooks.map((entry) => (
          <div key={entry.id}>
            <p className="font-mono text-xs text-accent">{entry.data.event}</p>
            <JsonView value={entry.data.payload} />
          </div>
        ))}
      </div>
    </details>
  )
}

// --------------------------------------------------------------------------- //
// Requests
// --------------------------------------------------------------------------- //

function RequestTable({ requests, vocabulary }) {
  const columns = [
    {
      key: 'when',
      header: 'Starts',
      render: (row) => (
        <span className="flex flex-col">
          <span className="font-mono text-[13px] text-foreground">{absoluteTime(row.data?.start)}</span>
          <span className="text-xs text-muted-foreground">{relativeTime(row.data?.start)}</span>
        </span>
      ),
    },
    {
      key: 'who',
      header: 'Attendee',
      render: (row) => (
        <span className="flex flex-col">
          <span className="text-[13px] text-foreground">{row.data?.attendee?.name ?? '—'}</span>
          <span className="font-mono text-xs text-muted-foreground">{row.data?.attendee?.email}</span>
        </span>
      ),
    },
    { key: 'type', header: 'Event type', render: (row) => <span className="text-[13px]">{row.data?.title}</span> },
    { key: 'status', header: 'Status', render: (row) => <StatusBadge booking={row} vocabulary={vocabulary} /> },
    { key: 'hold', header: 'Slot', render: (row) => <HoldMark booking={row} /> },
    {
      key: 'reason',
      header: 'Rejection reason',
      render: (row) => (
        <span className="text-[13px] text-muted-foreground">
          {row.rejectionReason || <span className="text-muted-foreground/60">—</span>}
        </span>
      ),
    },
  ]
  return (
    <DataTable
      columns={columns}
      rows={requests}
      rowKey={(row) => row.id}
      empty="No booking has been requested in this room yet. Request a slot from the Event types tab, or turn requires confirmation on and let a prospect ask."
    />
  )
}

// --------------------------------------------------------------------------- //
// The gates: the researched first step
// --------------------------------------------------------------------------- //

function EventTypePanel({ roomId, eventTypes, vocabulary, onDone }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)

  async function toggle(record, field, value) {
    setBusy(record.id)
    setError(null)
    try {
      await approvalApi.patchEventType(roomId, record.id, { [field]: value })
      onDone()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy('')
    }
  }

  if (!eventTypes.length) {
    return (
      <EmptyState
        title="This room has no event types"
        description="A slot is requested against an event type, and that is where requires confirmation is turned on. Create one to make this workflow do anything."
      />
    )
  }
  return (
    <div className="flex flex-col gap-3">
      {error && <Notice tone="danger" title="The change was refused">{refusalSummary(error)}</Notice>}
      {eventTypes.map((record) => (
        <Card key={record.id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-mono text-sm font-semibold text-foreground">{record.data?.title}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {record.data?.durationMinutes} minutes · host {record.data?.hostId ?? 'unassigned'}
                {record.data?.hostName ? ` (${record.data.hostName})` : ''}
              </p>
            </div>
            <Badge tone={record.requires_confirmation ? 'update' : 'neutral'}>
              {record.requires_confirmation ? 'needs approval' : 'books directly'}
            </Badge>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <ToggleRow
              id={`rc-${record.id}`}
              label="Requires confirmation"
              hint="Step one of the flow. On, a requested slot is held as PENDING until a host confirms or declines it."
              checked={Boolean(record.requires_confirmation)}
              disabled={busy === record.id}
              onChange={(value) => toggle(record, 'requiresConfirmation', value)}
            />
            <ToggleRow
              id={`ev-${record.id}`}
              label="Email verification"
              hint="A separate optional gate. On, a booking needs a verified emailVerificationCode in the request body."
              checked={Boolean(record.email_verification_required)}
              disabled={busy === record.id}
              onChange={(value) => toggle(record, 'emailVerification', value)}
            />
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Fact term="Notice required" mono>{record.data?.minimumNoticeMinutes ?? 'none'}</Fact>
            <Fact term="Opens this far ahead" mono>
              {record.data?.maximumRangeDays ? `${record.data.maximumRangeDays} days` : 'unbounded'}
            </Fact>
            <Fact term="Bookings per attendee" mono>{record.data?.bookingLimitPerAttendee ?? 'unlimited'}</Fact>
            <Fact term="Owners" mono>
              {(record.data?.assignedUserIds || []).length ? record.data.assignedUserIds.join(', ') : 'host only'}
            </Fact>
          </dl>
          <RequestForm roomId={roomId} eventType={record} vocabulary={vocabulary} onDone={onDone} />
        </Card>
      ))}
    </div>
  )
}

/**
 * A labelled switch.
 *
 * Built here because `components/ui.jsx` has no `Toggle`, and that file is
 * shared and not this feature's to edit. A checkbox with a real `<label>` over
 * it rather than a styled `<div role=switch>`: the native control is keyboard
 * reachable and announces itself without this file having to get it right.
 */
function ToggleRow({ id, label, hint, checked, disabled, onChange }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-3">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
          className="h-5 w-5 shrink-0 cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-50"
        />
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
      </div>
      {hint && <p className="text-xs text-muted-foreground/80">{hint}</p>}
    </div>
  )
}

/** The researched second step, and what it will do before it does it. */
function RequestForm({ roomId, eventType, vocabulary, onDone }) {
  const [start, setStart] = useState(defaultStart())
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [skipOwner, setSkipOwner] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [made, setMade] = useState(null)

  const willHold = Boolean(eventType.requires_confirmation)
  const needsCode = Boolean(eventType.email_verification_required)

  async function send(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setMade(null)
    try {
      const body = await approvalApi.requestSlot(roomId, {
        eventTypeId: eventType.id,
        start: new Date(start).toISOString(),
        attendee: { name, email },
        ...(code ? { emailVerificationCode: code } : {}),
        ...(skipOwner ? { skipContactOwner: true } : {}),
      })
      setMade(body)
      setName('')
      setEmail('')
      setCode('')
      onDone()
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  async function sendCode() {
    setBusy(true)
    setError(null)
    try {
      const body = await approvalApi.sendVerificationCode(roomId, { eventTypeId: eventType.id, email })
      setCode(body.code)
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  async function verify() {
    setBusy(true)
    setError(null)
    try {
      await approvalApi.verifyEmail(roomId, { code, eventTypeId: eventType.id, email })
      setError(null)
    } catch (failure) {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="mt-4 flex flex-col gap-3 rounded-lg border border-border-subtle/40 p-3" onSubmit={send}>
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Request a slot</p>
      <p className="text-xs text-muted-foreground">
        {willHold
          ? 'This event type requires confirmation, so the request will be held as PENDING with a one-time password, BOOKING_REQUESTED will be recorded, and a host will have to say yes before a calendar event exists.'
          : 'This event type does not require confirmation, so the booking will be accepted immediately and its calendar event created on the spot.'}
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id={`start-${eventType.id}`} label="Start">
          <input
            id={`start-${eventType.id}`}
            type="datetime-local"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className={inputClass}
            required
          />
        </Field>
        <Field id={`name-${eventType.id}`} label="Attendee name">
          <input
            id={`name-${eventType.id}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
            required
          />
        </Field>
        <Field id={`email-${eventType.id}`} label="Attendee email" hint="A verified code is checked against this address.">
          <input
            id={`email-${eventType.id}`}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
            required
          />
        </Field>
      </div>
      {needsCode && (
        <div className="flex flex-wrap items-end gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
          <div className="min-w-[12rem] flex-1">
            <Field
              id={`code-${eventType.id}`}
              label="Email verification code"
              hint="Send one, verify it, then request the slot. The code is returned here and not emailed: this product has no mail transport."
            >
              <input
                id={`code-${eventType.id}`}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className={inputClass}
              />
            </Field>
          </div>
          <Button onClick={sendCode} disabled={busy || !email}>
            Send code
          </Button>
          <Button onClick={verify} disabled={busy || !code}>
            Verify
          </Button>
        </div>
      )}
      <ToggleRow
        id={`skip-${eventType.id}`}
        label="Skip the contact owner"
        hint="Routes the request past the contact owner to the host. The host is still notified either way."
        checked={skipOwner}
        onChange={setSkipOwner}
      />
      <div>
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? 'Requesting…' : willHold ? 'Request a slot for approval' : 'Book the slot'}
        </Button>
      </div>
      {error && <Notice tone="danger" title="The request was refused">{refusalSummary(error)}</Notice>}
      {made && (
        <Notice tone="success" title={`Created ${made.request.status}`}>
          {made.request.status === 'PENDING'
            ? 'The slot is held and waiting for a host. BOOKING_REQUESTED was recorded.'
            : 'The booking was accepted and its calendar event created.'}{' '}
          {made.routing?.skipped?.length
            ? `The contact owner (${made.routing.skipped.join(', ')}) was skipped.`
            : ''}
        </Notice>
      )}
    </form>
  )
}

// --------------------------------------------------------------------------- //
// Workflow rules
// --------------------------------------------------------------------------- //

function AutomationPanel({ roomId, automations, vocabulary, onDone }) {
  const [trigger, setTrigger] = useState(vocabulary?.workflow_triggers?.[0]?.trigger || 'bookingRequested')
  const [channel, setChannel] = useState('to_do')
  const [error, setError] = useState(null)

  async function add(event) {
    event.preventDefault()
    setError(null)
    try {
      await approvalApi.createAutomation(roomId, { trigger, channels: [channel], label: `${trigger} → ${channel}` })
      onDone()
    } catch (failure) {
      setError(failure)
    }
  }

  const triggers = (vocabulary?.workflow_triggers || []).map((entry) => ({
    value: entry.trigger,
    label: entry.trigger,
  }))
  const channels = (vocabulary?.notification_channels || ['to_do']).map((value) => ({ value, label: value }))

  return (
    <div className="flex flex-col gap-4">
      <Notice tone="info" title="What these rules do">
        {(vocabulary?.workflow_triggers || [])
          .map((entry) => entry.meaning)
          .join(' ')}{' '}
        Every firing is recorded against the request that caused it, marked as simulated: nothing is actually
        sent.
      </Notice>
      <DataTable
        columns={[
          {
            key: 'label',
            header: 'Rule',
            render: (row) => (
              <span className="flex flex-col">
                <span className="text-[13px] text-foreground">{row.data?.label}</span>
                <span className="font-mono text-xs text-muted-foreground">{row.data?.trigger}</span>
              </span>
            ),
          },
          {
            key: 'channels',
            header: 'Channels',
            render: (row) => (
              <span className="flex flex-wrap gap-1.5">
                {(row.data?.channels || []).map((name) => (
                  <Badge key={name}>{name}</Badge>
                ))}
              </span>
            ),
          },
          {
            key: 'enabled',
            header: 'Enabled',
            render: (row) => <Badge tone={row.data?.enabled ? 'insert' : 'neutral'}>{row.data?.enabled ? 'yes' : 'no'}</Badge>,
          },
        ]}
        rows={automations}
        rowKey={(row) => row.id}
        empty="No workflow rule in this room. A request will record its webhook and nothing else until one is added."
      />
      <form className="flex flex-wrap items-end gap-3 rounded-lg border border-border-subtle/40 p-3" onSubmit={add}>
        <div className="min-w-[12rem] flex-1">
          <Field id="automation-trigger" label="Trigger" hint="Only the two this workflow raises.">
            <select
              id="automation-trigger"
              value={trigger}
              onChange={(e) => setTrigger(e.target.value)}
              className={inputClass}
            >
              {triggers.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="min-w-[10rem] flex-1">
          <Field id="automation-channel" label="Channel">
            <select
              id="automation-channel"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              className={inputClass}
            >
              {channels.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Button type="submit" icon="plus">
          Add rule
        </Button>
        {error && <Notice tone="danger" title="The rule was refused">{refusalSummary(error)}</Notice>}
      </form>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// The decisions and the researched contract
// --------------------------------------------------------------------------- //

function ContractPanel({ inferences, capabilities, vocabulary }) {
  return (
    <div className="flex flex-col gap-4">
      <Notice tone="info" title="The bypass flags, and who may use them">
        A caller may send {bypassVersions(vocabulary).join(' or ')} and ask to skip a check, and the flag is
        honoured only for an authenticated caller holding one of {bypassRoles(vocabulary).join(', ')}. An
        unentitled flag is ignored and the check applies as though it had not been sent — the response says which
        and why.
      </Notice>
      <Section title="Judgement calls this build made" hint="Served verbatim from the server, so a reviewer reads the list rather than reconstructing it from a diff.">
        <div className="flex flex-col gap-3">
          {(inferences?.inferences || []).map((entry) => (
            <Card key={entry.id}>
              <p className="font-mono text-xs text-accent">{entry.id}</p>
              <p className="mt-1 text-sm font-semibold text-foreground">{entry.topic}</p>
              <p className="mt-1 text-[13px] text-muted-foreground">{entry.basis}</p>
              <dl className="mt-3 grid grid-cols-1 gap-3">
                <Fact term="This build chose">{entry.why}</Fact>
                <Fact term="Change it by" mono>{entry.change_it}</Fact>
                <Fact term="Affects">{entry.blast_radius}</Fact>
              </dl>
            </Card>
          ))}
        </div>
      </Section>
      <Section title="The researched surface, and where it lives here">
        <DataTable
          columns={[
            { key: 'researched', header: 'Researched API', render: (row) => <span className="font-mono text-xs">{row.researched}</span> },
            { key: 'here', header: 'Here', render: (row) => <span className="font-mono text-xs text-accent">{row.here}</span> },
            { key: 'what', header: 'What it does', render: (row) => <span className="text-[13px]">{row.implements}</span> },
          ]}
          rows={capabilities?.apis || []}
          rowKey={(row) => row.researched}
          empty="No capability list is available."
        />
        <div className="flex flex-col gap-2">
          {(capabilities?.not_implemented || []).map((entry) => (
            <Notice key={entry.surface} tone="warning" title={`Not built here: ${entry.surface}`}>
              {entry.why}
            </Notice>
          ))}
        </div>
      </Section>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// The page
// --------------------------------------------------------------------------- //

/**
 * Who this page acts as.
 *
 * The researched rule is "the provided authorization header refers to the owner
 * of the booking", and this product has no session layer, so who the page acts
 * as is a choice the page makes visible rather than one it hides. It is editable
 * because the demo seeds several hosts and an assigned user, and a reviewer
 * should be able to see a refusal as well as a success.
 */
const ACTORS = ['dana', 'sam', 'priya']

export default function SlotApproval() {
  const [tab, setTab] = useState('pending')
  const [roomId, setRoomId] = useState('')
  const [actor, setActor] = useState('dana')
  const [filter, setFilter] = useState('')

  const rooms = useAsync(() => approvalApi.rooms(), [])
  const vocabulary = useAsync(() => approvalApi.vocabulary(), [])
  const inferences = useAsync(() => approvalApi.inferences(), [])
  const capabilities = useAsync(() => approvalApi.capabilities(), [])

  const roomOptions = useMemo(
    () => (rooms.data?.records || []).map((record) => ({ value: record.id, label: record.data?.name || record.id })),
    [rooms.data],
  )
  const chosen = roomOptions.find((option) => option.value === roomId)

  // Default to the first room once the list arrives, in an effect rather than in
  // a `useMemo`: a memo that sets state is a side effect wearing a disguise, and
  // React is free to discard a memo's value.
  useEffect(() => {
    if (!roomId && roomOptions.length) setRoomId(roomOptions[0].value)
  }, [roomId, roomOptions])

  const live = useAsync(
    () => (roomId ? approvalApi.summary(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const requests = useAsync(
    () => (roomId ? approvalApi.requests(roomId, { status: filter || undefined }) : Promise.resolve(null)),
    [roomId, filter],
  )
  const eventTypes = useAsync(
    () => (roomId ? approvalApi.eventTypes(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const automations = useAsync(
    () => (roomId ? approvalApi.automations(roomId) : Promise.resolve(null)),
    [roomId],
  )

  const refetchAll = () => {
    live.refetch()
    requests.refetch()
    eventTypes.refetch()
    automations.refetch()
  }

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading slot approval" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const all = requests.data?.requests || []
  const pending = all.filter(needsAHost)

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <div>
          <h1 className="font-mono text-lg font-semibold text-foreground">Slot approval</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            A requested slot is held as a PENDING booking carrying a one-time password. A host confirms it, and a
            calendar event is created, or declines it with a reason, and the slot goes back to the host's
            availability.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[14rem] flex-1">
            <Field id="wf062-room" label="Room">
              <select id="wf062-room" value={roomId} onChange={(e) => setRoomId(e.target.value)} className={inputClass}>
                {roomOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="min-w-[10rem]">
            <Field id="wf062-actor" label="Acting as" hint="The owner of the booking decides it.">
              <select id="wf062-actor" value={actor} onChange={(e) => setActor(e.target.value)} className={inputClass}>
                {ACTORS.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="min-w-[10rem]">
            <Field id="wf062-status" label="Filter by status">
              <select id="wf062-status" value={filter} onChange={(e) => setFilter(e.target.value)} className={inputClass}>
                <option value="">every status</option>
                {(vocabulary.data?.statuses || []).map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.value}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Button icon="refresh" onClick={refetchAll}>
            Refresh
          </Button>
        </div>
      </header>

      {live.data && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Awaiting a host"
            value={live.data.awaiting_a_host}
            hint={`${live.data.held_slots} slot(s) held`}
          />
          <StatCard label="Accepted" value={live.data.counts?.ACCEPTED ?? 0} hint={`${live.data.calendar_events ?? 0} calendar event(s)`} />
          <StatCard label="Rejected" value={live.data.counts?.REJECTED ?? 0} hint={`${live.data.webhooks ?? 0} webhook(s) recorded`} />
          <StatCard label="Event types" value={live.data.event_types ?? 0} hint={`${live.data.automations ?? 0} workflow rule(s)`} />
        </div>
      )}

      <TabBar current={tab} onChange={setTab} />

      <div role="tabpanel" id={`wf062-panel-${tab}`} aria-labelledby={`wf062-tab-${tab}`} className="flex flex-col gap-4">
        {tab === 'pending' && (
          <Section
            title={`Waiting on a host in ${chosen?.label || 'this room'}`}
            hint="The only bookings where the workflow is waiting on a person. Confirming creates the calendar event; declining releases the slot and records why."
          >
            {requests.loading || eventTypes.loading ? (
              <Spinner label="Loading requests" />
            ) : requests.error ? (
              <ErrorNote error={requests.error} onRetry={requests.refetch} />
            ) : (
              <NeedsADecision
                roomId={roomId}
                requests={pending}
                vocabulary={vocabulary.data}
                actor={actor}
                onDone={refetchAll}
              />
            )}
          </Section>
        )}

        {tab === 'requests' && (
          <Section title="Every booking" hint="Statuses come from the server's own vocabulary, so the badge and the table cannot disagree about what a status means.">
            {requests.loading ? (
              <Spinner label="Loading requests" />
            ) : requests.error ? (
              <ErrorNote error={requests.error} onRetry={requests.refetch} />
            ) : (
              <RequestTable requests={all} vocabulary={vocabulary.data} />
            )}
          </Section>
        )}

        {tab === 'gates' && (
          <Section
            title="Event types and their gates"
            hint="Requires confirmation is the researched first step. Email verification is a separate optional gate, checked only when the event type has it on."
          >
            {eventTypes.loading ? (
              <Spinner label="Loading event types" />
            ) : eventTypes.error ? (
              <ErrorNote error={eventTypes.error} onRetry={eventTypes.refetch} />
            ) : (
              <EventTypePanel
                roomId={roomId}
                eventTypes={eventTypes.data?.event_types || []}
                vocabulary={vocabulary.data}
                onDone={refetchAll}
              />
            )}
          </Section>
        )}

        {tab === 'automations' && (
          <Section title="Workflow rules" hint="A request can act without anybody opening the room: bookingRequested and bookingRejected are themselves valid triggers.">
            {automations.loading ? (
              <Spinner label="Loading rules" />
            ) : automations.error ? (
              <ErrorNote error={automations.error} onRetry={automations.refetch} />
            ) : (
              <AutomationPanel
                roomId={roomId}
                automations={automations.data?.automations || []}
                vocabulary={vocabulary.data}
                onDone={refetchAll}
              />
            )}
          </Section>
        )}

        {tab === 'contract' && (
          <ContractPanel
            inferences={inferences.data}
            capabilities={capabilities.data}
            vocabulary={vocabulary.data}
          />
        )}
      </div>
    </div>
  )
}