import { useCallback, useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  ACKNOWLEDGEMENT_FALLBACK,
  EVENT_TYPES,
  SCOPE_FALLBACK,
  isDownloadable,
  listRooms,
  stateRow,
  webhookApi,
} from './api'
import {
  CheckLadder,
  Digest,
  Notice,
  RetryLadderTable,
  SealedKeyBadge,
  Select,
  SnapshotAge,
  StateBadge,
} from './primitives'

/**
 * WF-082: verify and IP-allowlist inbound provider webhooks.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Say what this check is worth before offering to fire anything.** The handler checks the
 * source address it was given and the two digests the request carried. It cannot prove the
 * request was not replayed from a captured body, because the provider sends no nonce. A security
 * page that opens with a green tick and no caveat is the reading the specification forbids, so
 * the scope sentence is the first thing rendered and the server sends the same sentence with
 * every response.
 *
 * **Draw the three checks as a ladder.** "Verifies authenticity three ways... Only then does the
 * handler act" is a claim about a sequence. Each row names what its check covers, because the two
 * digests cover different bytes and a reader who cannot tell them apart cannot tell which one
 * refused.
 *
 * **Show a duplicate as a duplicate, not as a refusal.** The provider reads anything other than
 * 200 plus the magic string as a failed callback, and a failed callback walks a ladder reaching
 * twenty hours and fifteen minutes. After ten consecutive failures the provider clears the
 * callback URL outright, and nothing reports that on either side. So a repeat delivery is
 * acknowledged, and the row says it was acknowledged.
 *
 * **Show the retry contract as numbers.** The specification's stated reason for a
 * machine-readable catalogue is retry logic built "instead of scraping the human-readable page",
 * so the ladder is a table rather than a sentence.
 *
 * **Say what the allowlist is worth.** The handler reads the socket peer and no forwarded
 * header. Mounted behind a proxy that refuses every delivery. A page that does not say this
 * leaves an operator to discover it by watching a callback silently receive nothing.
 *
 * Every state this page can be in is rendered: loading, error, empty. A security page that goes
 * blank when the API is down looks like the verifier refusing to serve, which is the one reading
 * that must never be possible.
 */

/** The room this panel is looking at, remembered between renders. */
function useRoom() {
  const rooms = useAsync(() => listRooms(), [])
  const [roomId, setRoomId] = useState('')

  const options = useMemo(() => {
    const records = asList(rooms.data?.records)
    return records.map((room) => ({
      id: room.id,
      label: room.data?.name || room.id,
    }))
  }, [rooms.data])

  const selected = roomId || options[0]?.id || ''
  return { ...rooms, options, selected, setRoomId }
}

/** One write at a time, with its field errors and its refusal message kept apart. */
function useAction() {
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState(null)
  const [refusal, setRefusal] = useState(null)

  const run = useCallback(async (fn) => {
    setBusy(true)
    setErrors(null)
    setRefusal(null)
    try {
      const result = await fn()
      return { ok: true, result }
    } catch (error) {
      // A field-keyed `errors` map belongs beside the inputs. A refusal with a `code` belongs
      // above them, because it is not about what was typed: a 403 names the address and a 401
      // names which digest did not verify.
      if (error?.errors) setErrors(error.errors)
      else setRefusal(error)
      return { ok: false, error }
    } finally {
      setBusy(false)
    }
  }, [])

  return { busy, errors, refusal, run, clearErrors: () => setErrors(null), clearRefusal: () => setRefusal(null) }
}

function FieldError({ message }) {
  if (!message) return null
  return <p className="mt-1 text-xs text-destructive">{message}</p>
}

/**
 * A list that is never not-a-list.
 *
 * Every count and every list here arrives from a different route, and a payload that has not
 * resolved is `null` rather than an empty array. `Array.isArray` at the read site means a
 * missing list renders nothing instead of throwing inside a `.filter`, which on a security page
 * reads as the verifier refusing to serve rather than as a request still in flight.
 */
function asList(value) {
  return Array.isArray(value) ? value : []
}

/**
 * The refusal above the forms, in one place, so four callers do not write it four times.
 *
 * The catalogue entry travels with the error, and the page renders its `cause` and `remediation`
 * rather than inventing a sentence. The whole reason the specification asks for a
 * machine-readable catalogue is retry logic written against the answer rather than scraped from
 * prose, and a page that discards the catalogue and paraphrases it undoes that.
 */
function RefusalNote({ refusal, onDismiss }) {
  if (!refusal) return null
  const status = refusal.status || 0
  const catalogue = refusal.catalogue || {}
  const title =
    catalogue.error_name === 'source_ip_not_allowed'
      ? 'The source address is not in the published range file'
      : status === 401
        ? 'A digest did not verify'
        : status === 403
          ? 'Refused'
          : status === 400
            ? 'That request was not accepted'
            : 'The request failed'
  return (
    <div className="mt-3">
      <Notice
        tone={status === 403 ? 'warning' : 'destructive'}
        title={title}
        action={
          onDismiss ? (
            <Button icon="close" onClick={onDismiss}>
              Dismiss
            </Button>
          ) : null
        }
      >
        <p className="font-mono text-xs">
          {catalogue.error_name || refusal.code || `HTTP ${status}`}
          {refusal.failedCheck ? ` at the ${refusal.failedCheck} check` : ''}
        </p>
        <p className="mt-1">{String(refusal.message || refusal)}</p>
        {catalogue.remediation && <p className="mt-1 text-xs">{catalogue.remediation}</p>}
        {catalogue.retryable !== undefined && catalogue.retryable !== null && (
          <p className="mt-1 text-xs">
            {catalogue.retryable
              ? 'The provider reads this as a failed callback and retries on the ladder below.'
              : 'Retrying will not clear this. Fix the sender before the next attempt.'}
          </p>
        )}
      </Notice>
    </div>
  )
}

function RegistrationSection({ roomId, summary, vocabulary, onChanged }) {
  const create = useAction()
  const [callbackUrl, setCallbackUrl] = useState('https://hooks.example/wf082')
  const [apiKey, setApiKey] = useState('demo-inbound-api-key')

  const existing = useAsync(() => webhookApi.callbacks(roomId), [roomId])
  const registrations = asList(existing.data?.callbacks)
  const keyWarning = vocabulary?.key_warning || ''

  const submit = useCallback(async () => {
    const done = await create.run(() =>
      webhookApi.createCallback(roomId, {
        callback_url: callbackUrl.trim(),
        api_key: apiKey.trim(),
        scope: 'account',
      }),
    )
    if (done.ok) onChanged()
  }, [apiKey, callbackUrl, create, onChanged, roomId])

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">
            Callback registration
          </h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            The URL must use HTTPS and the account API key is required. Plain HTTP callbacks
            stopped receiving events on December 1, 2024, and a registration with no key cannot
            authenticate anything.
          </p>
        </div>
        <Badge tone={summary?.callbacks > 0 ? 'insert' : 'neutral'}>
          {summary?.callbacks ?? 0} registered
        </Badge>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field
          label="Callback URL"
          hint="HTTPS only. This is the URL the provider POSTs to."
          id="wf082-url"
        >
          <input
            id="wf082-url"
            className={inputClass}
            value={callbackUrl}
            onChange={(event) => setCallbackUrl(event.target.value)}
          />
          <FieldError message={create.errors?.callback_url} />
        </Field>
        <Field
          label="Account API key"
          hint="Sealed at rest. Never returned by any read on this page."
          id="wf082-key"
        >
          <input
            id="wf082-key"
            className={inputClass}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
          <FieldError message={create.errors?.api_key} />
        </Field>
      </div>

      <div className="mt-4">
        <Button variant="primary" icon="plus" disabled={create.busy} onClick={submit}>
          Register the callback
        </Button>
      </div>

      <RefusalNote refusal={create.refusal} onDismiss={create.clearRefusal} />

      {registrations.length > 0 && (
        <ul className="mt-4 space-y-2">
          {registrations.map((callback) => (
            <li
              key={callback.id}
              className="flex flex-wrap items-start justify-between gap-3 rounded-sm border border-border-subtle p-3"
            >
              <div className="min-w-0">
                <p className="break-all font-mono text-xs text-foreground">
                  {callback.callback_url}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  scope <span className="font-mono">{callback.scope}</span>
                  {callback.client_id ? ` for ${callback.client_id}` : ''}, sealed under key{' '}
                  <span className="font-mono">{callback.key_fingerprint}</span>
                </p>
              </div>
              <SealedKeyBadge callback={callback} />
            </li>
          ))}
        </ul>
      )}

      {keyWarning && (
        <div className="mt-3">
          <Notice tone="warning" title="This process is running on the published demo key">
            <p>{keyWarning}</p>
          </Notice>
        </div>
      )}
    </Card>
  )
}

function RangeSection({ roomId, vocabulary, onChanged }) {
  const refresh = useAction()
  const [ranges, setRanges] = useState('203.0.113.0/24\n198.51.100.0/24\n127.0.0.1/32')

  const submit = useCallback(async () => {
    const list = ranges
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => ({ ip: line }))
    const done = await refresh.run(() => webhookApi.refreshRanges(roomId, { ranges: list }))
    if (done.ok) onChanged()
  }, [onChanged, ranges, refresh, roomId])

  const stored = useAsync(() => webhookApi.ranges(roomId), [roomId])

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">
            Published IP ranges
          </h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            The published file is fetched by a deployment that has an HTTP client for it. This
            handler opens no socket while a delivery is waiting, so a refresh is an explicit call.
          </p>
        </div>
        <Badge tone={(stored.data?.range_count ?? 0) > 0 ? 'update' : 'neutral'}>
          {stored.data?.range_count ?? 0} range(s) held
        </Badge>
      </div>

      {stored.data && (
        <div className="mt-3">
          <SnapshotAge snapshot={stored.data} />
        </div>
      )}

      <div className="mt-4">
        <Field
          label="Ranges to store"
          hint="One per line. CIDR or a single address. Replaces the stored snapshot."
          id="wf082-ranges"
        >
          <textarea
            id="wf082-ranges"
            className={`${inputClass} min-h-24 font-mono text-xs`}
            value={ranges}
            onChange={(event) => setRanges(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button icon="refresh" disabled={refresh.busy} onClick={submit}>
          Store the range snapshot
        </Button>
      </div>

      <RefusalNote refusal={refresh.refusal} onDismiss={refresh.clearRefusal} />

      {asList(stored.data?.ranges).length > 0 && (
        <ul className="mt-4 flex flex-wrap gap-2">
          {stored.data.ranges.map((row) => (
            <li key={row.range}>
              <Badge tone="neutral">{row.range}</Badge>
            </li>
          ))}
        </ul>
      )}

      {vocabulary?.ip_ranges && (
        <p className="mt-4 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
          Published at <span className="font-mono">{vocabulary.ip_ranges.url}</span>. A snapshot
          older than {vocabulary.ip_ranges.stale_after_seconds}s is marked stale, because an
          allowlist of unknown vintage is exactly when a person needs to be told.
        </p>
      )}
    </Card>
  )
}

function DeliverSection({ roomId, vocabulary, onChanged }) {
  const fire = useAction()
  const [eventId, setEventId] = useState('evt_demo_001')
  const [eventType, setEventType] = useState(EVENT_TYPES[0].id)
  const [eventTime, setEventTime] = useState('2026-03-04T18:22:31Z')
  const [apiKey, setApiKey] = useState('demo-inbound-api-key')
  const [lastReport, setLastReport] = useState(null)

  const deliver = useCallback(async () => {
    const done = await fire.run(() =>
      webhookApi.deliver(roomId, {
        apiKey: apiKey.trim(),
        eventId: eventId.trim() || 'evt_demo_001',
        eventType,
        eventTime,
      }),
    )
    if (done.ok) {
      setLastReport(done.result)
      onChanged()
    }
  }, [apiKey, eventId, eventTime, eventType, fire, onChanged, roomId])

  const type = EVENT_TYPES.find((row) => row.id === eventType)
  const acknowledgement = vocabulary?.acknowledgement?.body || ACKNOWLEDGEMENT_FALLBACK

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">
            Fire a signed delivery
          </h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            The body is a real <span className="font-mono">multipart/form-data</span> message with
            the payload in a part named <span className="font-mono">json</span>, and both digests
            are computed in this browser. The key you type is not read back from the server.
          </p>
        </div>
        <Badge tone="update">Signs locally</Badge>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field label="Event id" hint="The dedupe key. Send it twice to see the retry." id="wf082-evid">
          <input
            id="wf082-evid"
            className={inputClass}
            value={eventId}
            onChange={(event) => setEventId(event.target.value)}
          />
        </Field>
        <Field label="Event type" hint={type?.note} id="wf082-evtype">
          <Select id="wf082-evtype" value={eventType} onChange={setEventType}>
            {EVENT_TYPES.map((row) => (
              <option key={row.id} value={row.id}>
                {row.label} ({row.id})
              </option>
            ))}
          </Select>
        </Field>
        <Field label="event_time" hint="The HMAC input is this joined to the type, with no separator." id="wf082-evtime">
          <input
            id="wf082-evtime"
            className={`${inputClass} font-mono text-xs`}
            value={eventTime}
            onChange={(event) => setEventTime(event.target.value)}
          />
        </Field>
        <Field label="API key to sign with" hint="Must match the registration, or the digests refuse." id="wf082-dkey">
          <input
            id="wf082-dkey"
            className={inputClass}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" icon="refresh" disabled={fire.busy} onClick={deliver}>
          Deliver the event
        </Button>
      </div>

      <RefusalNote refusal={fire.refusal} onDismiss={fire.clearRefusal} />

      {lastReport && (
        <div className="mt-4">
          <Notice
            tone={lastReport.state === 'rejected' ? 'destructive' : 'success'}
            title={
              lastReport.state === 'duplicate'
                ? 'A repeat delivery, acknowledged'
                : lastReport.state === 'verified'
                  ? 'All three checks passed'
                  : 'Refused'
            }
          >
            <p className="font-mono text-xs">state: {lastReport.state}</p>
            <p className="mt-1">
              Answered <span className="font-mono">200</span> with the body{' '}
              <span className="font-mono">{lastReport.acknowledgement || acknowledgement}</span>.
              The provider checks that string, so a 200 with an empty body would still be read as
              a failed callback.
            </p>
            <div className="mt-3">
              <CheckLadder
                checks={lastReport.checks}
                failedCheck={lastReport.failed_check || null}
              />
            </div>
          </Notice>
        </div>
      )}

      {isDownloadable(eventType) && (
        <p className="mt-3 text-xs text-muted-foreground">
          {type?.note} Final document generation lags signing, so a download attempted on the
          all-signed event races the file.
        </p>
      )}
    </Card>
  )
}

function DeliveryRow({ row }) {
  const entry = stateRow(row.state)
  const [open, setOpen] = useState(false)
  return (
    <li className="rounded-sm border border-border-subtle p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StateBadge state={row.state} label={entry.label} />
            {row.error_name && <Badge tone="restore">{row.error_name}</Badge>}
            {row.failed_check && <Badge tone="neutral">at {row.failed_check}</Badge>}
          </div>
          <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
            {row.event_type || 'no event_type'} / {row.event_id || 'no event_id'}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            from <span className="font-mono">{row.source_ip}</span>
            {row.signature_request_ref ? ` for ${row.signature_request_ref}` : ''}
            {row.recorded_at ? ` at ${row.recorded_at}` : ''}
          </p>
        </div>
        <Button icon="chevron" onClick={() => setOpen(!open)}>
          {open ? 'Hide the three checks' : 'Show the three checks'}
        </Button>
      </div>
      {open && (
        <div className="mt-3">
          <CheckLadder checks={row.checks} failedCheck={row.failed_check || null} />
          {asList(row.checks).length > 1 && row.checks[1]?.expected && (
            <p className="mt-2">
              <Digest value={row.checks[1].expected} />
            </p>
          )}
        </div>
      )}
    </li>
  )
}

function DeliveryLog({ deliveries, loading, error, onRetry }) {
  const rows = asList(deliveries)
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">Delivery log</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Every delivery with its three checks intact, because a log that records only a pass or
            a fail cannot answer a question a month later: which range matched, which digest was
            expected, and whether the event id had already been seen.
          </p>
        </div>
        <Badge tone={rows.length > 0 ? 'update' : 'neutral'}>{rows.length} recorded</Badge>
      </div>

      <div className="mt-4">
        {loading ? (
          <Spinner label="Loading deliveries" />
        ) : error ? (
          <ErrorNote error={error} onRetry={onRetry} />
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No delivery yet. This room hears nothing until one arrives.
          </p>
        ) : (
          <ul className="space-y-2">
            {rows.map((row) => (
              <DeliveryRow key={row.id} row={row} />
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

function RetryContract({ retryPolicy }) {
  if (!retryPolicy) {
    return (
      <Card>
        <h2 className="font-display text-lg font-semibold text-foreground">The retry contract</h2>
        <p className="mt-0.5 text-sm text-muted-foreground">
          The retry ladder has not loaded. The API is reachable, so this is a request in flight.
        </p>
      </Card>
    )
  }
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">The retry contract</h2>
      <p className="mt-0.5 text-sm text-muted-foreground">
        The provider retries six times, then clears the callback URL after ten consecutive
        failures. Clearing is silent data loss: nothing reports it on either side. This handler
        answers inside {retryPolicy.provider_timeout_seconds} seconds and never fetches on the
        delivery path.
      </p>

      <div className="mt-4">
        <RetryLadderTable ladder={retryPolicy.retry_ladder} />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Badge tone="neutral">Timeout {retryPolicy.provider_timeout_seconds}s</Badge>
        <Badge tone="restore">Cleared after {retryPolicy.consecutive_failure_limit} failures</Badge>
        <Badge tone="update">Acknowledged with {retryPolicy.acknowledgement?.body}</Badge>
      </div>

      <p className="mt-3 text-xs text-muted-foreground">{retryPolicy.self_disable}</p>
    </Card>
  )
}

function DecisionsSection({ decisions }) {
  const rows = asList(decisions?.decisions)
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        What the research left open
      </h2>
      <p className="mt-0.5 text-sm text-muted-foreground">
        The specification instructs an implementer who needs a flow the evidence does not contain
        to derive it and record the derivation. Each of these names the option this build took and
        the option it rejected.
      </p>

      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">The derivation register has not loaded.</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {rows.map((decision) => (
            <li key={decision.id} className="rounded-sm border border-border-subtle p-3">
              <p className="font-mono text-xs text-foreground">{decision.id}</p>
              <p className="mt-1 text-sm text-foreground">{decision.question}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Chose <span className="font-mono">{decision.chosen}</span>. {decision.rejected_because}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function InboundVerificationPage() {
  const room = useRoom()
  const vocabulary = useAsync(() => webhookApi.vocabulary(), [])
  const retryPolicy = useAsync(() => webhookApi.retryPolicy(), [])
  const decisions = useAsync(() => webhookApi.decisions(), [])
  const summary = useAsync(() => webhookApi.summary(room.selected), [room.selected])
  const deliveries = useAsync(() => webhookApi.deliveries(room.selected), [room.selected])

  const refreshAll = useCallback(() => {
    summary.refetch()
    deliveries.refetch()
  }, [deliveries, summary])

  const scope = vocabulary.data?.verification_scope || SCOPE_FALLBACK
  const wiring = vocabulary.data?.transparency?.wiring_risk || ''

  if (room.loading) return <Spinner label="Loading rooms" />
  if (room.error) return <ErrorNote error={room.error} onRetry={room.refetch} />

  if (room.options.length === 0) {
    return (
      <EmptyState
        title="No rooms yet"
        description="This workflow registers a callback and verifies deliveries against a room, and there is no room to bind it to."
      />
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Verify and IP-allowlist inbound provider webhooks
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Accept a callback only after its source IP, its Content-Sha256 payload digest and its
            event_hash HMAC have all verified. A delivery that passes is recorded once and answered
            with the magic string the provider checks, including when the provider retries it.
          </p>
        </div>
        <div className="w-full sm:w-72">
          <Field label="Room" id="wf082-room">
            <Select id="wf082-room" value={room.selected} onChange={room.setRoomId}>
              {room.options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </div>

      {/* What this check is worth, before anything that could fire one. */}
      <Notice tone="info" title="What this handler can verify">
        <p>{scope}</p>
        <p className="mt-1 text-xs">{vocabulary.data?.no_fetches_on_delivery}</p>
      </Notice>

      {wiring && (
        <Notice tone="warning" title="This route needs direct network wiring">
          <p>{wiring}</p>
        </Notice>
      )}

      {summary.loading ? (
        <Spinner label="Loading the inbound board" />
      ) : summary.error ? (
        <ErrorNote error={summary.error} onRetry={summary.refetch} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Verified"
            value={summary.data?.verified ?? 0}
            hint={`${summary.data?.deliveries ?? 0} delivery recorded`}
            icon="audit"
          />
          <StatCard
            label="Retries deduped"
            value={summary.data?.duplicates ?? 0}
            hint="Acknowledged, not refused"
            icon="refresh"
          />
          <StatCard
            label="Refused"
            value={summary.data?.rejected ?? 0}
            hint={
              Object.keys(summary.data?.by_error_name || {}).length
                ? Object.entries(summary.data.by_error_name)
                    .map(([name, count]) => `${count} ${name}`)
                    .join(', ')
                : 'no refusals yet'
            }
            icon="close"
          />
          <StatCard
            label="Ranges held"
            value={summary.data?.ranges ?? 0}
            hint={summary.data?.snapshot_stale ? 'Snapshot is stale' : 'Snapshot is fresh'}
            icon="database"
          />
        </div>
      )}

      <RegistrationSection
        roomId={room.selected}
        summary={summary.data}
        vocabulary={vocabulary.data}
        onChanged={refreshAll}
      />

      <RangeSection
        roomId={room.selected}
        vocabulary={vocabulary.data}
        onChanged={refreshAll}
      />

      <DeliverSection
        roomId={room.selected}
        vocabulary={vocabulary.data}
        onChanged={refreshAll}
      />

      <DeliveryLog
        deliveries={deliveries.data?.deliveries}
        loading={deliveries.loading}
        error={deliveries.error}
        onRetry={deliveries.refetch}
      />

      <RetryContract retryPolicy={retryPolicy.data} />

      <DecisionsSection decisions={decisions.data} />
    </div>
  )
}

export default {
  id: 'wf-082-verify-and-ip-allowlist-inbound-provider-webhooks',
  label: 'Inbound verification',
  icon: 'audit',
  iconPath:
    'M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3zM9 12l2 2 4-4',
  order: 462,
  Component: InboundVerificationPage,
}