/**
 * Meeting reminders: conditional pre- and post-meeting reminders and SMS nudges
 * (WF-061).
 *
 * Five sections, in the order the researched flow happens in them.
 *
 * **Setup** first, because two of the researched rules make it a precondition
 * rather than a detail: an SMS reminder is refused without a connected Twilio
 * account and a number behind it, and a no-reply sender is refused without the
 * organisation's own domain. Finding that out while configuring a reminder is
 * much better than finding it out when a customer does not get a text - and the
 * research flags reply forwarding as a *third*, separate requirement, so the
 * form shows all three independently.
 *
 * **Reminders** is the asset list. A reminder is a reusable thing attachable to
 * many meeting types, which is why "Remove from Meeting Type" and "Delete" are
 * two different buttons on two different rows: the first survives, the second
 * does not. Each card quotes the researched sentence its options come from.
 *
 * **Meeting types** is where the attachment happens, and where the derived
 * `phone_required` is shown - Cal's `attendee.phoneNumber` "becomes required
 * when SMS reminders are enabled for the event type" is a fact about the booking
 * form, so it belongs where the form is configured.
 *
 * **Activity** is *Meetings Activity* from step 7: per reminder, a status, the
 * vendor's own skip reason when it did not go out, the resolved recipients, and
 * the message that was composed. Every row expands to its check trail, so a
 * skip that failed two gates at once shows both rather than one.
 *
 * **What this infers** is the research's own gaps made arguable: which skip
 * reason wins when two gates fail, where a follow-up is anchored from, and why
 * the two Cal translation switches are recorded but not acted on. Those are
 * product behaviour, and a reviewer should be able to disagree with one by name.
 *
 * The pickers come from `/vocabulary` and `/tags`, never from a list compiled
 * into this file, so a team that adds a condition or a dynamic tag ships a record
 * rather than a change here.
 */

import { useEffect, useMemo, useState } from 'react'
import { absoluteTime, api, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { reminderApi } from './api'
import Glyphs from './icons'
import { Caveat, ChannelChip, Fact, Note, ReasonTally, StatusChip } from './primitives'

const SECTIONS = [
  { id: 'activity', label: 'Activity', glyph: Glyphs.clock },
  { id: 'reminders', label: 'Reminders', glyph: Glyphs.envelope },
  { id: 'types', label: 'Meeting types', glyph: Glyphs.booker },
  { id: 'setup', label: 'Setup', glyph: Glyphs.gate },
  { id: 'inferences', label: 'What this infers', glyph: Glyphs.workflow },
]

function Stat({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className="rounded-lg bg-muted p-2 text-accent">
          <Icon path={glyph} size={20} />
        </span>
      </div>
    </Card>
  )
}

/** One delivery, expanding to the checks, the message and the forwarded replies. */
function DeliveryRow({ delivery }) {
  const [open, setOpen] = useState(false)
  const data = delivery.data || {}
  const message = data.message || {}
  const failedChecks = (data.checks || []).filter((check) => !check.passed)

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 flex-wrap items-center gap-3 py-2 text-left
          transition-colors duration-150 hover:bg-muted/40"
      >
        <StatusChip status={data.status} reasonText={data.reason_text} />
        <ChannelChip channel={data.channel || 'email'} />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {data.reminderName || data.reminderId}
        </span>
        {data.fire_at && (
          <span className="shrink-0 font-mono text-xs text-muted-foreground" title={absoluteTime(data.fire_at)}>
            {absoluteTime(data.fire_at)}
          </span>
        )}
        {data.replies?.length > 0 && (
          <span className="flex items-center gap-1 text-xs text-sky-300">
            <Icon path={Glyphs.reply} size={14} />
            {data.replies.length} repl{data.replies.length === 1 ? 'y' : 'ies'}
          </span>
        )}
        <span className="shrink-0 font-mono text-xs text-muted-foreground" title={absoluteTime(delivery.created_at)}>
          {relativeTime(delivery.created_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 sm:grid-cols-2">
            <Fact label="Status">{data.status}</Fact>
            <Fact label="Fires at">{absoluteTime(data.fire_at)}</Fact>
            <Fact label="From">{data.from_address || '—'}</Fact>
            <Fact label="Replies to">{(data.replies_to || []).join(', ') || '—'}</Fact>
            <Fact label="Recipients">
              {(data.recipients || []).map((r) => r.email || r.phone).join(', ') || 'none'}
            </Fact>
            <Fact label="Locale">{message.locale || '—'}</Fact>
          </dl>

          {data.detail && <p className="text-sm text-foreground/90">{data.detail}</p>}

          {failedChecks.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Every gate, not just the deciding one
              </p>
              <ul className="space-y-1">
                {failedChecks.map((check) => (
                  <li key={check.check} className="flex items-start gap-2 text-xs">
                    <Icon path={Glyphs.skipped} size={13} className="mt-0.5 shrink-0 text-amber-300" />
                    <span className="font-mono text-foreground">{check.check}</span>
                    <span className="text-muted-foreground">{check.detail}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {message.subject || message.body ? (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                The message that went out
              </p>
              {message.subject && (
                <p className="text-sm font-medium text-foreground">{message.subject}</p>
              )}
              {message.body && (
                <p className="mt-1 font-mono text-xs whitespace-pre-wrap text-foreground/90">
                  {message.body}
                </p>
              )}
              {message.missing_tags?.length > 0 && (
                <p className="mt-2 text-xs text-amber-300">
                  Unresolved tags, left in the message on purpose:{' '}
                  {message.missing_tags.join(', ')}
                </p>
              )}
              {message.attachments?.length > 0 && (
                <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                  <Icon path={Glyphs.envelope} size={13} />
                  {message.attachments.map((entry) => entry.filename).join(', ')} attached
                </p>
              )}
            </div>
          ) : null}

          {data.unaddressable?.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Not addressable, so they were left out: {data.unaddressable.join(', ')}
            </p>
          )}
        </div>
      )}
    </li>
  )
}

/** A reminder card: its options, and the two researched actions on it. */
function ReminderCard({ reminder, vocabulary, meetingTypes, onDetach, onDelete, busy }) {
  const data = reminder.data || {}
  const channel = data.channel || 'email'
  const isSms = channel === 'sms'
  const attached = (reminder.attached_meeting_types || []).length

  return (
    <Card className="card-hover flex h-full flex-col">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-mono text-sm font-semibold text-foreground">{data.name}</h3>
          <p className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
            <Icon path={isSms ? Glyphs.phone : Glyphs.envelope} size={13} />
            {channel.toUpperCase()}
            {data.enabled === false && ' · disabled'}
          </p>
        </div>
        <Badge tone={data.enabled === false ? 'neutral' : 'insert'}>
          {data.offset?.value} {data.offset?.unit}
        </Badge>
      </div>

      <p className="mt-3 flex-1 text-sm text-muted-foreground">
        {vocabulary?.condition_detail?.[data.condition] || data.condition}
      </p>

      <dl className="mt-3 space-y-1.5 text-xs">
        <Fact label={isSms ? 'From' : 'To'}>
          {isSms
            ? vocabulary?.sms_from_roles?.[data.smsFrom] || data.smsFrom
            : vocabulary?.email_to_roles?.[data.emailTo] || data.emailTo}
        </Fact>
        {!isSms && (
          <Fact label="Send from">
            {vocabulary?.email_from_roles?.[data.emailFrom] || data.emailFrom}
          </Fact>
        )}
        <Fact label="Replies to">
          {vocabulary?.replies_to_roles?.[data.repliesTo] || data.repliesTo}
        </Fact>
        <Fact label="Gates">
          {(data.conditions?.rules || []).length === 0
            ? 'No Restriction'
            : (data.conditions.rules || []).map((rule) => rule.kind).join(', ')}
        </Fact>
        <Fact label="Attached to">{attached} meeting type{attached === 1 ? '' : 's'}</Fact>
      </dl>

      {data.conditions?.rules?.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
          {(data.conditions.rules || []).map((rule) => (
            <li key={rule.kind} className="flex items-start gap-2">
              <Icon path={Glyphs.gate} size={12} className="mt-0.5 shrink-0" />
              <span>
                {rule.kind === 'weekday'
                  ? `Only when the meeting starts on ${(rule.weekdays || []).join(', ')}`
                  : `Only when booked ${rule.value} ${rule.unit} ahead`}
              </span>
            </li>
          ))}
        </ul>
      )}

      {data.autoTranslateEnabled && (
        <div className="mt-3">
          <Caveat>
            autoTranslateEnabled is on with sourceLocale {data.sourceLocale}. The researched switch is
            recorded and the locale the message went out in is on the delivery, but no translation is
            performed - the research names no catalogue.
          </Caveat>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          icon="close"
          variant="secondary"
          disabled={busy || attached === 0}
          onClick={() => onDetach(reminder, meetingTypes)}
        >
          Remove from meeting type
        </Button>
        <Button icon="trash" variant="danger" disabled={busy} onClick={() => onDelete(reminder)}>
          Delete
        </Button>
      </div>
    </Card>
  )
}

export default function MeetingReminders() {
  const [section, setSection] = useState('activity')
  const [statusFilter, setStatusFilter] = useState('')
  const [reasonFilter, setReasonFilter] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [notice, setNotice] = useState(null)
  const [noticeError, setNoticeError] = useState(null)

  const vocabulary = useAsync(() => reminderApi.vocabulary(), [])
  const tags = useAsync(() => reminderApi.tags(), [])
  const summary = useAsync(() => reminderApi.summary(), [])
  const reminders = useAsync(() => reminderApi.listReminders({ limit: 100 }), [])
  const meetingTypes = useAsync(() => reminderApi.listMeetingTypes({ limit: 100 }), [])
  const messaging = useAsync(() => reminderApi.messaging(), [])
  const inferences = useAsync(() => reminderApi.inferences(), [])
  const deliveries = useAsync(
    () => reminderApi.listDeliveries({ status: statusFilter, reason: reasonFilter, limit: 80 }),
    [statusFilter, reasonFilter],
  )
  // Rooms are a core collection, not this feature's, so they come from the core
  // client's own reader. This feature's routes are reached through `reminderApi`.
  const rooms = useAsync(() => api.listRecords('room', { limit: 100 }), [])

  // -- the setup form
  const [noreplyDomain, setNoreplyDomain] = useState('')
  const [number, setNumber] = useState('')
  const [localNumber, setLocalNumber] = useState('')
  const [connected, setConnected] = useState(false)
  const [ownAccount, setOwnAccount] = useState(false)
  useEffect(() => {
    const settings = messaging.data?.settings
    if (!settings) return
    setNoreplyDomain(settings.noreply_domain || '')
    setNumber(settings.number || '')
    setLocalNumber(settings.localNumber || '')
    setConnected(Boolean(settings.connected))
    setOwnAccount(Boolean(settings.own_account))
  }, [messaging.data])

  // -- the new-reminder form
  const [draft, setDraft] = useState({
    name: '',
    channel: 'email',
    condition: 'before_meeting',
    offsetValue: 24,
    offsetUnit: 'hours',
    emailTo: 'primary_guest',
    emailFrom: 'host',
    repliesTo: 'host',
    smsFrom: 'any_number',
    subject: 'Your evaluation on {CP.Meeting.Date}',
    body: 'Hi {CP.Guest.FirstName},\n\nA quick reminder about {CP.Meeting.Name}.\n\n{CP.Meeting.RescheduleUrl}',
  })

  const reasons = vocabulary.data?.skip_reasons || {}

  async function guard(busyKey, successMessage, work) {
    setBusyId(busyKey)
    setNoticeError(null)
    setNotice(null)
    try {
      const result = await work()
      setNotice(successMessage(result))
    } catch (error) {
      setNoticeError(error)
    } finally {
      setBusyId(null)
    }
  }

  function saveSetup() {
    guard('setup', () => 'Messaging setup saved.', async () => {
      await reminderApi.saveMessaging({
        noreply_domain: noreplyDomain,
        number,
        localNumber: localNumber,
        connected,
        own_account: ownAccount,
      })
      messaging.refetch()
    })
  }

  function createReminder() {
    guard('create', (record) => `Created "${record.data.name}".`, async () => {
      const isSms = draft.channel === 'sms'
      const record = await reminderApi.createReminder({
        name: draft.name,
        channel: draft.channel,
        condition: draft.condition,
        offset: { value: Number(draft.offsetValue), unit: draft.offsetUnit },
        emailTo: draft.emailTo,
        emailFrom: draft.emailFrom,
        repliesTo: draft.repliesTo,
        smsFrom: draft.smsFrom,
        subject: draft.subject,
        body: draft.body,
      })
      reminders.refetch()
      summary.refetch()
      return record
    })
  }

  function removeFromMeetingType(reminder) {
    const meetingTypeId = reminder.attached_meeting_types?.[0]
    if (!meetingTypeId) return
    guard(reminder.id, () => 'Removed from that meeting type. The reminder itself is still here.', async () => {
      await reminderApi.detach(meetingTypeId, reminder.id)
      reminders.refetch()
    })
  }

  function deleteReminder(reminder) {
    guard(reminder.id, () => `Deleted "${reminder.data.name}". Its delivery history stays.`, async () => {
      await reminderApi.deleteReminder(reminder.id)
      reminders.refetch()
      summary.refetch()
    })
  }

  function runAutomation() {
    guard('fire', (result) => `Fired ${result.fired}: ${result.sent} sent, ${result.skipped} skipped.`, async () => {
      const result = await reminderApi.fire()
      deliveries.refetch()
      summary.refetch()
      return result
    })
  }

  const stats = useMemo(() => {
    const byStatus = summary.data?.by_status || {}
    return [
      { label: 'Deliveries', value: summary.data?.deliveries ?? 0, hint: 'every booking', glyph: Glyphs.clock },
      { label: 'Sent', value: byStatus.sent ?? 0, hint: 'the message went out', glyph: Glyphs.sent },
      { label: 'Skipped', value: byStatus.skipped ?? 0, hint: 'with a recorded reason', glyph: Glyphs.skipped },
      { label: 'Scheduled', value: byStatus.scheduled ?? 0, hint: 'not due yet', glyph: Glyphs.pending },
    ]
  }, [summary.data])

  const deliveryRows = deliveries.data?.deliveries || []
  const reminderRows = reminders.data?.reminders || []
  const meetingTypeRows = meetingTypes.data?.meeting_types || []
  const isSmsDraft = draft.channel === 'sms'

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Meeting reminders</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Declare a reusable reminder &mdash; email or SMS, before or after the meeting, optionally
          only if the primary guest has not responded &mdash; attach it to many meeting types, and let
          it fire on a schedule with a recorded reason for every message that did not go out.
        </p>
      </header>

      {noticeError && <ErrorNote error={noticeError} />}
      {notice && <Note>{notice}</Note>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <Stat key={stat.label} {...stat} />
        ))}
      </div>

      {/* Section switcher. A tablist, so the arrow-key semantics and the aria
          relationship are right and the choice can be shared by URL. */}
      <div role="tablist" aria-label="Meeting reminder sections" className="flex flex-wrap gap-2">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={section === item.id}
            aria-controls={`panel-${item.id}`}
            onClick={() => setSection(item.id)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
              transition-colors duration-200 ${
                section === item.id
                  ? 'bg-accent/15 font-medium text-accent'
                  : 'bg-muted text-muted-foreground hover:border-border-subtle hover:text-foreground'
              }`}
          >
            <Icon path={item.glyph} size={16} />
            {item.label}
          </button>
        ))}
      </div>

      {/* -- activity -- */}
      {section === 'activity' && (
        <div id="panel-activity" role="tabpanel" aria-labelledby="tab-activity" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">Meetings activity</h2>
              <p className="text-sm text-muted-foreground">
                Every reminder, a status, and the vendor&rsquo;s own reason when it did not go out.
                Select a row for the check trail, the resolved recipients and the message.
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Status" id="filter-status">
                <select
                  id="filter-status"
                  className={inputClass}
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value)}
                >
                  <option value="">All</option>
                  {(vocabulary.data?.statuses || []).map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Skipped because" id="filter-reason">
                <select
                  id="filter-reason"
                  className={inputClass}
                  value={reasonFilter}
                  onChange={(event) => setReasonFilter(event.target.value)}
                >
                  <option value="">All</option>
                  {Object.entries(reasons).map(([slug, text]) => (
                    <option key={slug} value={slug}>
                      {text}
                    </option>
                  ))}
                </select>
              </Field>
              <Button icon="refresh" onClick={deliveries.refetch}>
                Refresh
              </Button>
              <Button variant="primary" icon="clock" onClick={runAutomation} disabled={busyId === 'fire'}>
                Run the automation
              </Button>
            </div>
          </div>

          {deliveries.loading && <Spinner label="Loading activity" />}
          {deliveries.error && <ErrorNote error={deliveries.error} onRetry={deliveries.refetch} />}

          {!deliveries.loading && !deliveries.error && deliveryRows.length === 0 && (
            <EmptyState
              title="Nothing has fired yet"
              description="A reminder is planned onto a booking when it is attached, and fires at its moment."
            />
          )}

          {deliveryRows.length > 0 && (
            <Card className="p-4">
              <ul>
                {deliveryRows.map((delivery) => (
                  <DeliveryRow key={delivery.id} delivery={delivery} />
                ))}
              </ul>
            </Card>
          )}

          <Card className="p-4">
            <h3 className="mb-2 font-mono text-base font-semibold">Why messages were skipped</h3>
            <ReasonTally byReason={summary.data?.by_reason} reasons={reasons} />
          </Card>
        </div>
      )}

      {/* -- reminders -- */}
      {section === 'reminders' && (
        <div id="panel-reminders" role="tabpanel" aria-labelledby="tab-reminders" className="space-y-5">
          <div>
            <h2 className="font-mono text-lg font-semibold">Reminders</h2>
            <p className="text-sm text-muted-foreground">
              A reminder is a reusable asset, so it can be attached to many meeting types. That is why
              &ldquo;remove from a meeting type&rdquo; and &ldquo;delete&rdquo; are two different buttons:
              the first leaves the reminder and its other attachments alone, the second does not.
            </p>
          </div>

          {reminders.loading && <Spinner label="Loading reminders" />}
          {reminders.error && <ErrorNote error={reminders.error} onRetry={reminders.refetch} />}

          {!reminders.loading && !reminders.error && reminderRows.length === 0 && (
            <EmptyState
              title="No reminders yet"
              description="Add one below. Email needs nothing extra; SMS needs the setup in the Setup tab."
            />
          )}

          <ul className="grid gap-4 lg:grid-cols-2">
            {reminderRows.map((reminder) => (
              <li key={reminder.id}>
                <ReminderCard
                  reminder={reminder}
                  vocabulary={vocabulary.data}
                  meetingTypes={meetingTypeRows}
                  onDetach={removeFromMeetingType}
                  onDelete={deleteReminder}
                  busy={busyId === reminder.id}
                />
              </li>
            ))}
          </ul>

          <section aria-labelledby="new-reminder-heading" className="space-y-3">
            <h3 id="new-reminder-heading" className="font-mono text-base font-semibold">
              Add a reminder
            </h3>
            <Card>
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  createReminder()
                }}
                className="space-y-4"
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Name" id="draft-name" hint="The vendor's list is of named assets.">
                    <input
                      id="draft-name"
                      className={inputClass}
                      required
                      value={draft.name}
                      onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                    />
                  </Field>
                  <Field label="Reminder type" id="draft-channel">
                    <select
                      id="draft-channel"
                      className={inputClass}
                      value={draft.channel}
                      onChange={(event) => setDraft({ ...draft, channel: event.target.value })}
                    >
                      {(vocabulary.data?.channels || []).map((name) => (
                        <option key={name} value={name}>
                          {name.toUpperCase()}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>

                <div className="grid gap-4 sm:grid-cols-3">
                  <Field label="Send when" id="draft-condition" className="sm:col-span-1">
                    <select
                      id="draft-condition"
                      className={inputClass}
                      value={draft.condition}
                      onChange={(event) => setDraft({ ...draft, condition: event.target.value })}
                    >
                      {(vocabulary.data?.conditions || []).map((name) => (
                        <option key={name} value={name}>
                          {name.replace(/_/g, ' ')}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Offset" id="draft-offset-value">
                    <input
                      id="draft-offset-value"
                      className={inputClass}
                      type="number"
                      min="1"
                      required
                      value={draft.offsetValue}
                      onChange={(event) => setDraft({ ...draft, offsetValue: event.target.value })}
                    />
                  </Field>
                  <Field label="Unit" id="draft-offset-unit">
                    <select
                      id="draft-offset-unit"
                      className={inputClass}
                      value={draft.offsetUnit}
                      onChange={(event) => setDraft({ ...draft, offsetUnit: event.target.value })}
                    >
                      {(vocabulary.data?.offset_units || []).map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>

                <p className="text-xs text-muted-foreground">
                  {vocabulary.data?.condition_detail?.[draft.condition]}
                </p>

                <div className="grid gap-4 sm:grid-cols-3">
                  {isSmsDraft ? (
                    <Field label="Send SMS from" id="draft-sms-from">
                      <select
                        id="draft-sms-from"
                        className={inputClass}
                        value={draft.smsFrom}
                        onChange={(event) => setDraft({ ...draft, smsFrom: event.target.value })}
                      >
                        {(vocabulary.data?.sms_from || []).map((name) => (
                          <option key={name} value={name}>
                            {name.replace(/_/g, ' ')}
                          </option>
                        ))}
                      </select>
                    </Field>
                  ) : (
                    <>
                      <Field label="Send email to" id="draft-email-to">
                        <select
                          id="draft-email-to"
                          className={inputClass}
                          value={draft.emailTo}
                          onChange={(event) => setDraft({ ...draft, emailTo: event.target.value })}
                        >
                          {(vocabulary.data?.email_to || []).map((name) => (
                            <option key={name} value={name}>
                              {vocabulary.data?.email_to_roles?.[name] || name}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Send email from" id="draft-email-from">
                        <select
                          id="draft-email-from"
                          className={inputClass}
                          value={draft.emailFrom}
                          onChange={(event) => setDraft({ ...draft, emailFrom: event.target.value })}
                        >
                          {(vocabulary.data?.email_from || []).map((name) => (
                            <option key={name} value={name}>
                              {vocabulary.data?.email_from_roles?.[name] || name}
                            </option>
                          ))}
                        </select>
                      </Field>
                    </>
                  )}
                  <Field label="Send replies to" id="draft-replies-to">
                    <select
                      id="draft-replies-to"
                      className={inputClass}
                      value={draft.repliesTo}
                      onChange={(event) => setDraft({ ...draft, repliesTo: event.target.value })}
                    >
                      {(vocabulary.data?.replies_to || []).map((name) => (
                        <option key={name} value={name}>
                          {vocabulary.data?.replies_to_roles?.[name] || name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>

                <Field label="Subject" id="draft-subject">
                  <input
                    id="draft-subject"
                    className={inputClass}
                    value={draft.subject}
                    onChange={(event) => setDraft({ ...draft, subject: event.target.value })}
                  />
                </Field>
                <Field
                  label="Body"
                  id="draft-body"
                  hint="Dynamic tags are rendered per booking. An unresolved one is left in the message and reported."
                >
                  <textarea
                    id="draft-body"
                    className={`${inputClass} min-h-32 py-2`}
                    value={draft.body}
                    onChange={(event) => setDraft({ ...draft, body: event.target.value })}
                  />
                </Field>

                {tags.data && (
                  <details className="rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                    <summary className="min-h-11 cursor-pointer text-sm text-foreground">
                      {tags.data.chili_piper.count} Chili Piper tags and {tags.data.cal.count} Cal
                      tokens
                    </summary>
                    <div className="mt-2 space-y-4">
                      {[
                        ['Chili Piper', tags.data.chili_piper.tags, 'tag'],
                        ['Cal', tags.data.cal.tokens, 'token'],
                      ].map(([label, entries, key]) => (
                        <div key={label}>
                          <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                            {label}
                          </p>
                          <ul className="space-y-1">
                            {entries.slice(0, 12).map((entry) => (
                              <li key={entry[key]} className="flex flex-wrap items-baseline gap-2 text-xs">
                                <span className="font-mono text-foreground">{entry[key]}</span>
                                <span className="truncate text-muted-foreground">{entry.example}</span>
                                {entry.origin === 'named in the research' && (
                                  <Badge tone="insert">named in the research</Badge>
                                )}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  </details>
                )}

                <Button type="submit" variant="primary" icon="plus" disabled={busyId === 'create'}>
                  {busyId === 'create' ? 'Creating…' : 'Create reminder'}
                </Button>
              </form>
            </Card>
          </section>
        </div>
      )}

      {/* -- meeting types -- */}
      {section === 'types' && (
        <div id="panel-types" role="tabpanel" aria-labelledby="tab-types" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">Meeting types</h2>
            <p className="text-sm text-muted-foreground">
              A booking is made against a meeting type, and reminders attach to one. When an enabled
              SMS reminder is attached, the booking form must collect a phone &mdash; Cal&rsquo;s
              <code className="mx-1 font-mono">attendee.phoneNumber</code> &ldquo;becomes required when
              SMS reminders are enabled for the event type&rdquo;.
            </p>
          </div>

          {meetingTypes.loading && <Spinner label="Loading meeting types" />}
          {meetingTypes.error && <ErrorNote error={meetingTypes.error} onRetry={meetingTypes.refetch} />}

          {!meetingTypes.loading && !meetingTypes.error && meetingTypeRows.length === 0 && (
            <EmptyState title="No meeting types yet" description="Declare one before attaching a reminder." />
          )}

          <ul className="grid gap-4 lg:grid-cols-2">
            {meetingTypeRows.map((meetingType) => (
              <li key={meetingType.id}>
                <Card className="card-hover">
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="truncate font-mono text-sm font-semibold text-foreground">
                      {meetingType.data?.name || meetingType.name}
                    </h3>
                    <Badge tone={meetingType.phone_required ? 'restore' : 'neutral'}>
                      {meetingType.reminder_count} reminder{meetingType.reminder_count === 1 ? '' : 's'}
                    </Badge>
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {meetingType.phone_required
                      ? 'The booking form requires a phone, because an SMS reminder is enabled here.'
                      : 'No phone required on the booking form.'}
                  </p>
                  <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                    {meetingType.id}
                  </p>
                </Card>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* -- setup -- */}
      {section === 'setup' && (
        <div id="panel-setup" role="tabpanel" aria-labelledby="tab-setup" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">Messaging setup</h2>
            <p className="text-sm text-muted-foreground">
              Three researched preconditions, and they are independent. Without a connected Twilio
              account an SMS reminder cannot be enabled at all. Without a number behind the connection
              it could be saved and never sent. Reply forwarding needs an organisation-owned account
              on top of both.
            </p>
          </div>

          {messaging.loading && <Spinner label="Loading setup" />}
          {messaging.error && <ErrorNote error={messaging.error} onRetry={messaging.refetch} />}

          {messaging.data && (
            <Card>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="No-reply sending domain"
                  id="setup-domain"
                  hint="Required before a no-reply sender is accepted."
                >
                  <input
                    id="setup-domain"
                    className={inputClass}
                    value={noreplyDomain}
                    onChange={(event) => setNoreplyDomain(event.target.value)}
                  />
                </Field>
                <Field label="Sending number" id="setup-number" hint="Used by “any number”.">
                  <input
                    id="setup-number"
                    className={inputClass}
                    value={number}
                    onChange={(event) => setNumber(event.target.value)}
                  />
                </Field>
                <Field label="Local area number" id="setup-local" hint="Used by “local area number”.">
                  <input
                    id="setup-local"
                    className={inputClass}
                    value={localNumber}
                    onChange={(event) => setLocalNumber(event.target.value)}
                  />
                </Field>
                <div className="flex flex-col justify-end gap-2">
                  <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      className="h-4 w-4"
                      checked={connected}
                      onChange={(event) => setConnected(event.target.checked)}
                    />
                    <Icon path={Glyphs.phone} size={15} />
                    Twilio connected
                  </label>
                  <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      className="h-4 w-4"
                      checked={ownAccount}
                      onChange={(event) => setOwnAccount(event.target.checked)}
                    />
                    <Icon path={Glyphs.reply} size={15} />
                    Our own Twilio account
                  </label>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Button variant="primary" onClick={saveSetup} disabled={busyId === 'setup'}>
                  {busyId === 'setup' ? 'Saving…' : 'Save setup'}
                </Button>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Badge tone={messaging.data.sms_ready ? 'insert' : 'delete'}>
                    SMS {messaging.data.sms_ready ? 'ready' : 'blocked'}
                  </Badge>
                  <Badge tone={messaging.data.reply_forwarding_ready ? 'insert' : 'neutral'}>
                    reply forwarding {messaging.data.reply_forwarding_ready ? 'ready' : 'blocked'}
                  </Badge>
                </span>
              </div>
            </Card>
          )}

          {messaging.data?.quotes && (
            <Card className="space-y-2 p-4">
              <h3 className="font-mono text-base font-semibold">What the research says</h3>
              {Object.entries(messaging.data.quotes).map(([key, text]) => (
                <p key={key} className="text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">{key.replace(/_/g, ' ')}: </span>
                  &ldquo;{text}&rdquo;
                </p>
              ))}
            </Card>
          )}

          {messaging.data && !messaging.data.reply_forwarding_ready && (
            <Caveat>
              Inbound SMS replies cannot be forwarded while the account is not organisation-owned.
              The research flags that separately, so this build keeps it as its own requirement rather
              than assuming a connection is enough.
            </Caveat>
          )}
        </div>
      )}

      {/* -- what this infers -- */}
      {section === 'inferences' && (
        <div id="panel-inferences" role="tabpanel" aria-labelledby="tab-inferences" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">What this infers</h2>
            <p className="text-sm text-muted-foreground">
              The three firing conditions, the four offset units, the three statuses, the five skip
              reasons and Cal&rsquo;s four enums are all sourced. The rest is judgement: which reason
              wins when two gates fail, where a follow-up is anchored from, what a booking&rsquo;s
              timezone is, and whether a message was actually translated. Each is listed with the
              reason for the choice and how to change it.
            </p>
          </div>

          {inferences.loading && <Spinner label="Loading inferences" />}
          {inferences.error && <ErrorNote error={inferences.error} onRetry={inferences.refetch} />}

          {inferences.data && (
            <>
              <Card className="p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">From the research: </span>
                  &ldquo;{inferences.data.sourced_quote}&rdquo;
                </p>
              </Card>

              <Card className="p-4">
                <ul>
                  {(inferences.data.inferences || []).map((entry) => (
                    <InferenceRow key={entry.id} entry={entry} />
                  ))}
                </ul>
              </Card>

              {inferences.data.sourced && (
                <Card className="p-4">
                  <h3 className="mb-2 font-mono text-base font-semibold">The sourced half</h3>
                  <JsonView value={inferences.data.sourced} />
                </Card>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** One inferred behaviour: what it is, why, and how to change it. */
function InferenceRow({ entry }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-3 py-2 text-left transition-colors
          duration-150 hover:bg-muted/40"
      >
        <Icon path={Glyphs.workflow} size={14} className="shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {entry.topic}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{entry.id}</span>
      </button>

      {open && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What the research says
            </p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Why</p>
            <p className="mt-0.5 text-foreground/90">{entry.why}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What this build chose
            </p>
            <JsonView value={entry.value} />
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">How to change it</p>
            <p className="mt-0.5 font-mono text-foreground/90">{entry.change_it}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Affects</p>
            <p className="mt-0.5 text-foreground/90">{entry.blast_radius}</p>
          </div>
        </div>
      )}
    </li>
  )
}
