/**
 * Agreement expiry and pre-expiry reminders (WF-081).
 *
 * The page is organised the way the research describes the workflow: an agreement
 * is sent with a deadline, every unsigned signer is reminded 7 and then 3 days
 * out, and at the deadline the incomplete signers flip to `expired` while a signer
 * who signed in time stays `signed`. What expiry does not do is delete anything,
 * so the page says that in its own words rather than implying it.
 *
 * Four things this page deliberately does not do.
 *
 * - It does not re-derive the cadence. The server sends the open reminder windows
 *   on every projection, so the "remind now" button cannot disagree with the rule
 *   that runs when it is pressed.
 * - It does not format a deadline the server formatted. The banner shows the
 *   server's own reading, including the note saying the timezone could not be
 *   resolved, rather than showing a local time nobody computed.
 * - It does not render a status as a colour. Every badge carries its status as
 *   text.
 * - It does not hide the invariants. The rule that expiry keeps the document is
 *   on the page, not in a comment.
 */

import { useState } from 'react'
import {
  Button,
  Card,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  useAsync,
} from '@/components/ui'
import { expiryApi, needsAttention, refusalSummary, skipReasonText, statusTone } from './api'
import {
  Countdown,
  ExpiryBanner,
  MonoValue,
  Notice,
  Row,
  SignerStatus,
  StatusBadge,
  TextInput,
} from './primitives'

/** The timezone a reader picks. A fixed offset is always honoured by the server. */
const ZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC' },
  { value: '+05:30', label: 'UTC+05:30 India' },
  { value: '+01:00', label: 'UTC+01:00 Central Europe' },
  { value: '-04:00', label: 'UTC-04:00 US Eastern' },
  { value: '-07:00', label: 'UTC-07:00 US Pacific' },
]

export function AgreementExpiry() {
  const [roomId, setRoomId] = useState('')
  const [zone, setZone] = useState('UTC')
  const [busy, setBusy] = useState(false)
  const [refused, setRefused] = useState('')
  const [report, setReport] = useState(null)

  const rooms = useAsync(() => expiryApi.rooms(), [])
  const vocabulary = useAsync(() => expiryApi.vocabulary(), [])
  const inferences = useAsync(() => expiryApi.inferences(), [])
  const room = useAsync(
    () => (roomId ? Promise.all([expiryApi.requests(roomId), expiryApi.summary(roomId)]) : null),
    [roomId]
  )

  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading agreement expiry" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const roomOptions = (rooms.data?.records || []).map((row) => ({
    value: row.id,
    label: row.data?.name || row.id,
  }))

  async function act(work) {
    setBusy(true)
    setRefused('')
    try {
      setReport(await work())
      room.refetch()
    } catch (error) {
      setRefused(refusalSummary(error))
      setReport(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Agreement expiry and reminders
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Send an agreement with a deadline, remind every unsigned signer 7 and 3 days before it,
          and close it at the deadline without deleting it.
        </p>
      </header>

      <Notice tone="info" title="What expiry does and does not do">
        {room.data?.[1]?.invariants?.document_survives ||
          vocabulary.data?.invariants?.document_survives ||
          ''}
      </Notice>

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Room" id="wf081-room" hint="Every agreement below belongs to this room.">
            <select
              id="wf081-room"
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
              className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
            >
              <option value="">Choose a room</option>
              {roomOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Read deadlines in"
            id="wf081-zone"
            hint="Changes how a deadline reads. It never changes when it fires."
          >
            <select
              id="wf081-zone"
              value={zone}
              onChange={(event) => setZone(event.target.value)}
              className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
            >
              {ZONE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Card>

      {!roomId && (
        <Card>
          <p className="text-sm text-muted-foreground">Choose a room to see its agreements.</p>
        </Card>
      )}

      {roomId && room.loading && <Spinner label="Loading agreements" />}
      {roomId && room.error && <ErrorNote error={room.error} onRetry={room.refetch} />}

      {roomId && room.data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Agreements"
              value={room.data[1].requests}
              hint={`${room.data[1].with_expiry} carry a deadline`}
              icon="audit"
            />
            <StatCard
              label="Expired"
              value={room.data[1].expired}
              hint="Closed at the deadline, document kept"
              icon="restore"
            />
            <StatCard
              label="Still open"
              value={room.data[1].pending}
              hint={`${room.data[1].completed} completed instead`}
              icon="rooms"
            />
            <StatCard
              label="Never expires"
              value={room.data[1].never_expires}
              hint="No expires_at was set, so these never close"
              icon="database"
            />
          </div>

          {refused && (
            <Notice tone="danger" title="The request was refused">
              {refused}
            </Notice>
          )}

          {report && <SweepReport report={report} />}

          <ReminderControls
            busy={busy}
            onSweep={() =>
              act(async () => {
                const swept = await expiryApi.sweep(roomId)
                const ledger = await expiryApi.reminders(roomId)
                return { swept, reminders: ledger }
              })
            }
          />

          {room.data[0].requests.length === 0 ? (
            <Card>
              <p className="text-sm text-muted-foreground">
                This room has no agreements yet. Send one from the API, or seed the demo data.
              </p>
            </Card>
          ) : (
            <ul className="space-y-4">
              {room.data[0].requests.map((request) => (
                <AgreementRow
                  key={request.id}
                  request={request}
                  roomId={roomId}
                  zone={zone}
                  vocabulary={vocabulary.data}
                  busy={busy}
                  onRemind={() =>
                    act(() => expiryApi.runReminders(roomId, request.id))
                  }
                  onSign={(email) => act(() => expiryApi.sign(roomId, request.id, email))}
                />
              ))}
            </ul>
          )}

          <Ledger roomId={roomId} />
          <Inferences inferences={inferences} />
        </>
      )}
    </div>
  )
}

/** The sweep and reminder controls, and what the last run reported. */
function ReminderControls({ busy, onSweep }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold text-foreground">
            Reminder scheduler and expiry sweep
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Reminders go out 7 and 3 days before a deadline, and never twice inside 24 hours. The
            sweep closes every agreement whose deadline has passed.
          </p>
        </div>
        <Button variant="primary" icon="refresh" disabled={busy} onClick={onSweep}>
          Run reminders and sweep
        </Button>
      </div>
    </Card>
  )
}

/** What the last sweep did, in words rather than as a count a reader has to decode. */
function SweepReport({ report }) {
  const swept = report.swept?.swept || []
  const reminders = report.reminders || {}
  return (
    <Notice tone={swept.length ? 'warning' : 'info'} title="The last run">
      <p>
        {swept.length} agreement{swept.length === 1 ? '' : 's'} closed at the deadline. No document
        was deleted.
      </p>
      <p className="mt-1">
        {reminders.sent || 0} reminder{reminders.sent === 1 ? '' : 's'} sent and{' '}
        {reminders.skipped || 0} skipped.
      </p>
      {swept.map((row) => (
        <p key={row.id} className="mt-1 font-mono text-xs">
          {row.audit_note}
        </p>
      ))}
    </Notice>
  )
}

/** One agreement, with its signers and the actions that apply to it. */
function AgreementRow({ request, roomId, zone, vocabulary, busy, onRemind, onSign }) {
  const [showDeadline, setShowDeadline] = useState(false)
  const [deadline, setDeadline] = useState('')

  const due = (request.reminders_due || []).length > 0

  // The banner is the research's own reading of the deadline in one signer's
  // timezone, and the server is what knows whether it could resolve that zone.
  // The picker re-reads the agreement with the chosen zone rather than the page
  // formatting the instant itself, so a zone the server cannot resolve shows its
  // note instead of a local time nobody computed.
  const viewed = useAsync(
    () => (request.has_expiry ? expiryApi.request(roomId, request.id, { tz: zone }) : null),
    [roomId, request.id, request.has_expiry, zone]
  )
  const shown = viewed.data || request
  const signer = shown.signatures?.[0]

  async function saveDeadline(clear) {
    const seconds = clear
      ? null
      : Math.floor(new Date(deadline).getTime() / 1000)
    await expiryApi.setExpiry(roomId, request.id, seconds)
    setShowDeadline(false)
  }

  return (
    <li>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-display text-base font-semibold text-foreground">
              {request.subject}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              <MonoValue>{request.id}</MonoValue>
              {request.document ? ` | ${request.document}` : ''}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              status={shown.status}
              label={shown.status}
              tone={statusTone(vocabulary, shown.status)}
            />
            <Countdown request={shown} />
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              Signers
            </p>
            <ul className="mt-2 space-y-2">
              {(shown.signatures || []).map((row) => (
                <li
                  key={row.email}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-sm border border-border-subtle px-3 py-2"
                >
                  <span className="min-w-0">
                    <span className="block text-sm text-foreground">{row.name}</span>
                    <span className="block font-mono text-xs text-muted-foreground">
                      {row.email}
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <SignerStatus statusCode={row.status_code} />
                    {row.status_code === 'awaiting_signature' && (
                      <Button disabled={busy || shown.status !== 'pending'} onClick={() => onSign(row.email)}>
                        Sign
                      </Button>
                    )}
                  </span>
                </li>
              ))}
              {(shown.signatures || []).length === 0 && (
                <li className="text-sm text-muted-foreground">
                  This agreement has no signers, so it cannot expire on anyone.
                </li>
              )}
            </ul>
          </div>

          <div className="space-y-3">
            {signer && <ExpiryBanner banner={signer.expiry_banner} />}
            <div>
              <Row label="Deadline">
                {shown.expires_at ? <MonoValue>{shown.expires_at}</MonoValue> : 'none'}
              </Row>
              <Row label="Reminders due">
                {(shown.reminders_due || []).length
                  ? `${shown.reminders_due.join(' and ')} day(s)`
                  : 'none'}
              </Row>
              <Row label="Delivery">
                {shown.email_muted ? 'event only, email muted' : 'email'}
              </Row>
            </div>
            <div className="flex flex-wrap gap-2">
              {due && shown.status === 'pending' && (
                <Button variant="primary" icon="refresh" disabled={busy} onClick={onRemind}>
                  Run reminders now
                </Button>
              )}
              {shown.status === 'pending' && (
                <Button
                  icon="schema"
                  disabled={busy}
                  onClick={() => setShowDeadline((open) => !open)}
                >
                  {shown.has_expiry ? 'Change the deadline' : 'Set a deadline'}
                </Button>
              )}
            </div>
            {showDeadline && (
              <div className="space-y-2 rounded-sm border border-border-subtle p-3">
                <Field
                  label="Deadline"
                  id={`wf081-deadline-${request.id}`}
                  hint="Between 1 and 90 days from now. The server rounds it down to the hour."
                >
                  <TextInput
                    id={`wf081-deadline-${request.id}`}
                    type="datetime-local"
                    value={deadline}
                    onChange={setDeadline}
                  />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" disabled={busy || !deadline} onClick={() => saveDeadline(false)}>
                    Save
                  </Button>
                  {shown.has_expiry && (
                    <Button variant="danger" disabled={busy} onClick={() => saveDeadline(true)}>
                      Clear the deadline
                    </Button>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {needsAttention(shown) && (
          <p className="mt-3 text-sm text-muted-foreground">
            A reminder window is open. Run the reminders so this signer hears about the deadline.
          </p>
        )}
      </Card>
    </li>
  )
}

/** The ledger, sends and skips alike. A skip is a row, not a silence. */
function Ledger({ roomId }) {
  const ledger = useAsync(() => expiryApi.reminders(roomId), [roomId])
  if (ledger.loading) return <Spinner label="Loading the reminder ledger" />
  if (ledger.error) return <ErrorNote error={ledger.error} onRetry={ledger.refetch} />
  const rows = ledger.data?.reminders || []
  if (rows.length === 0) {
    return (
      <Card>
        <p className="text-sm text-muted-foreground">
          No reminder has been sent or skipped in this room yet.
        </p>
      </Card>
    )
  }
  return (
    <Card>
      <h2 className="font-display text-base font-semibold text-foreground">Reminder ledger</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Every decision this workflow reached, including the ones it skipped.
      </p>
      <ul className="mt-3 space-y-2">
        {rows.map((row) => (
          <li
            key={row.id}
            className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle pb-2 last:border-0"
          >
            <span className="min-w-0">
              <span className="block font-mono text-xs text-foreground">{row.email}</span>
              <span className="block text-xs text-muted-foreground">
                {row.outcome === 'sent'
                  ? `Sent at the ${row.lead_days}-day lead by ${row.channel}`
                  : skipReasonText(row.reason)}
              </span>
            </span>
            <span className="font-mono text-xs text-muted-foreground">{row.at}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/** The judgement-call register, rendered verbatim. */
function Inferences({ inferences }) {
  if (inferences.loading) return <Spinner label="Loading the decision register" />
  if (inferences.error) return <ErrorNote error={inferences.error} onRetry={inferences.refetch} />
  const register = inferences.data
  if (!register) return null
  return (
    <Card>
      <h2 className="font-display text-base font-semibold text-foreground">
        What this build decided, and what it rejected
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The research fixes the numbers and leaves the mechanism open. These are the calls it left
        open, and the reading this build took for each.
      </p>
      <ul className="mt-3 space-y-3">
        {(register.decisions || []).map((entry) => (
          <li key={entry.id} className="border-b border-border-subtle pb-3 last:border-0">
            <p className="text-sm font-medium text-foreground">{entry.question}</p>
            <p className="mt-1 text-sm text-muted-foreground">{entry.chosen}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Rejected: {entry.rejected}
            </p>
            {entry.unsourced && (
              <p className="mt-1 text-xs text-warning">
                The research does not source this, so it is a judgement call.
              </p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

export default AgreementExpiry