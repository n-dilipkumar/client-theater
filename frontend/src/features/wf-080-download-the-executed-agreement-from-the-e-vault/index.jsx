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
  DEDUPE_HEADER,
  ENVIRONMENTS,
  NO_POLLING_FALLBACK,
  PDF_READY_TRIGGER,
  SEAL_SCOPE_FALLBACK,
  VARIANT_TRADEOFF_FALLBACK,
  evaultApi,
  listRooms,
  outcomeLabel,
  stateLabel,
} from './api'
import {
  ByteCount,
  Digest,
  DocumentStateBadge,
  Notice,
  OutcomeBadge,
  RetryBanner,
  RetryCountBadge,
  Select,
  SubscriptionStateBadge,
  VariantMatrix,
} from './primitives'

/**
 * WF-080: download the executed agreement from the e-vault, webhook-driven.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Say what the seal is worth before offering to download anything.** A sealed artifact is
 * byte-stable and immutable, and the server records the bytes and their digest, so what
 * this room can prove is that the file has not changed. It does not validate a certificate
 * chain and it cannot see a signature. A security page that opens with a download button
 * and no caveat is the reading the specification forbids, so the scope sentence is the first
 * thing rendered and the server sends the same sentence with every response.
 *
 * **Draw the two endpoints as a comparison.** "The `/download-protected` endpoint always
 * returns the same digitally sealed PDF file, while `/download` allows for watermark
 * customization" is a claim about a pair. One row per endpoint, one column per property,
 * and the two cells that say "No" written as words.
 *
 * **Show the 202 as a wait, not as a failure.** "The signed document file is not ready yet...
 * Retry after the indicated number of seconds. No response body is returned." So the wait is
 * a named banner with the number in it, and the number is read from the attempt log,
 * because the response carries none.
 *
 * **Show the retry.** The `X-PandaDoc-Webhook-Event-Id` header exists "to process each
 * webhook notification once... even when PandaDoc retries delivery", which is only
 * demonstrably true if a repeat is visible. A delivery that arrived twice says so on the row.
 *
 * **Show the two refusals a sandbox caller will hit.** A 401 names the endpoint that does
 * work and a 429 says `throttled` with a wait, because the specification forbids surfacing
 * either as a generic failure.
 *
 * Every state this page can be in is rendered: loading, error, empty. A security page that
 * goes blank when the API is down looks like the vault refusing to serve, which is the one
 * reading that must never be possible.
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
      // A field-keyed `errors` map belongs beside the inputs. A refusal with a `code`
      // belongs above them, because it is not about what was typed: a 401 names the
      // environment and a 409 names the document's state.
      if (error?.errors) setErrors(error.errors)
      else setRefusal(error)
      return { ok: false, error }
    } finally {
      setBusy(false)
    }
  }, [])

  const clearErrors = useCallback(() => setErrors(null), [])
  const clearRefusal = useCallback(() => setRefusal(null), [])

  return { busy, errors, refusal, run, clearErrors, clearRefusal }
}

/** The four honesty sentences, from whatever the last response carried. */
function useHonesty(summary) {
  return {
    effect: summary?.effect || 'recorded_not_verified',
    tradeoff: summary?.tradeoff || VARIANT_TRADEOFF_FALLBACK,
    sealScope: summary?.seal_scope || SEAL_SCOPE_FALLBACK,
    noPolling: summary?.no_polling || NO_POLLING_FALLBACK,
  }
}

/** The refusal above the forms, in one place, so four callers do not write it four times. */
function RefusalNote({ refusal, onDismiss }) {
  if (!refusal) return null
  const status = refusal.status || 0
  const title =
    status === 401
      ? 'The sealed endpoint refused this key'
      : status === 409
        ? 'Nothing is being produced yet'
        : status === 429
          ? `Throttled. Wait ${refusal.retryAfter} seconds`
          : status === 400
            ? 'That request was not accepted'
            : 'The request failed'
  return (
    <div className="mt-3">
      <Notice
        tone={status === 429 || status === 401 ? 'warning' : 'destructive'}
        title={title}
        action={
          onDismiss ? (
            <Button icon="close" onClick={onDismiss}>
              Dismiss
            </Button>
          ) : null
        }
      >
        <p className="font-mono text-xs">{refusal.code || `HTTP ${status}`}</p>
        <p className="mt-1">{String(refusal.message || refusal)}</p>
        {refusal.state && (
          <p className="mt-1 text-xs">
            The document is in <span className="font-mono">{refusal.state}</span>.
            {refusal.retryable === false
              ? ' Nothing is being produced, so there is no wait to offer.'
              : ''}
          </p>
        )}
        {refusal.remedy && <p className="mt-1 text-xs">{refusal.remedy}</p>}
      </Notice>
    </div>
  )
}

function FieldError({ message }) {
  if (!message) return null
  return <p className="mt-1 text-xs text-destructive">{message}</p>
}

/**
 * A list that is never not-a-list.
 *
 * Every count and every list in this feature arrives from a different route, and a payload
 * that has not resolved is `null` rather than an empty array. `Array.isArray` at the read
 * site means a missing list renders nothing instead of throwing inside a `.filter`, which
 * on a security page reads as the vault refusing to serve rather than as a request still
 * in flight.
 */
function asList(value) {
  return Array.isArray(value) ? value : []
}

function SubscriptionSection({ roomId, summary, vocabulary, onChanged }) {
  const create = useAction()
  const [triggers, setTriggers] = useState(PDF_READY_TRIGGER)
  const [environment, setEnvironment] = useState('production')
  const [extraTrigger, setExtraTrigger] = useState('')

  const list = useAsync(() => evaultApi.subscriptions(roomId), [roomId])
  const rows = asList(list.data?.subscriptions)

  const submit = useCallback(async () => {
    const names = triggers
      .split(',')
      .map((name) => name.trim())
      .filter(Boolean)
    const extra = extraTrigger.trim()
    if (extra && !names.includes(extra)) names.push(extra)
    const done = await create.run(() =>
      evaultApi.createSubscription(roomId, { triggers: names, environment }),
    )
    if (done.ok) {
      setExtraTrigger('')
      onChanged()
    }
  }, [create, environment, extraTrigger, onChanged, roomId, triggers])

  const cancel = useCallback(async (subscriptionId) => {
    const done = await create.run(() => evaultApi.cancelSubscription(subscriptionId))
    if (done.ok) onChanged()
  }, [create, onChanged])

  const listening = summary?.active_subscriptions ?? rows.filter((row) => row.active).length

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">Webhook subscription</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            One trigger is required. This workflow subscribes to the ready event and does not
            poll, so a subscription that cannot hear it has no second mechanism.
          </p>
        </div>
        <Badge tone={listening > 0 ? 'insert' : 'neutral'}>
          {listening} listening, {summary?.subscriptions ?? rows.length} total
        </Badge>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Field label="Triggers" hint="Comma separated. The ready event is required." id="wf080-triggers">
          <input
            id="wf080-triggers"
            className={inputClass}
            value={triggers}
            onChange={(event) => setTriggers(event.target.value)}
          />
          <FieldError message={create.errors?.triggers} />
        </Field>

        <Field label="Key environment" hint="The sealed endpoint answers production only." id="wf080-env">
          <Select id="wf080-env" value={environment} onChange={setEnvironment}>
            {ENVIRONMENTS.map((row) => (
              <option key={row.id} value={row.id}>
                {row.label}
              </option>
            ))}
          </Select>
          <FieldError message={create.errors?.environment} />
        </Field>

        <Field label="Another trigger, optional" hint="Stored, not interpreted here." id="wf080-extra">
          <input
            id="wf080-extra"
            className={inputClass}
            value={extraTrigger}
            placeholder="document_completed"
            onChange={(event) => setExtraTrigger(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4">
        <Button variant="primary" icon="plus" disabled={create.busy} onClick={submit}>
          Subscribe to the ready event
        </Button>
      </div>

      <RefusalNote refusal={create.refusal} onDismiss={create.clearRefusal} />

      <div className="mt-4">
        {list.loading ? (
          <Spinner label="Loading subscriptions" />
        ) : list.error ? (
          <ErrorNote error={list.error} onRetry={list.refetch} />
        ) : rows.length ? (
          <ul className="space-y-3">
            {rows.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-start justify-between gap-3 rounded-sm border border-border-subtle p-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <SubscriptionStateBadge subscription={row} />
                    {row.hears_ready_event ? (
                      <Badge tone="insert">Hears the ready event</Badge>
                    ) : (
                      <Badge tone="restore">Cannot hear the ready event</Badge>
                    )}
                    <Badge tone={row.environment === 'production' ? 'update' : 'neutral'}>
                      {row.environment}
                    </Badge>
                  </div>
                  <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
                    {row.triggers.join(', ')}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Wait {row.retry_after_seconds}s on a 202. Shared key{' '}
                    <span className="font-mono">{row.shared_key}</span>. It is a join value, not
                    a credential.
                  </p>
                </div>
                {row.active && (
                  <Button icon="close" disabled={create.busy} onClick={() => cancel(row.id)}>
                    Stop listening
                  </Button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">
            No subscription yet. The room hears nothing until one exists.
          </p>
        )}
      </div>

      {vocabulary?.dedupe_header && (
        <p className="mt-4 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
          The vendor deduplicates on{' '}
          <span className="font-mono">{vocabulary.dedupe_header}</span>. A repeat is applied once
          and counted, never applied twice.
        </p>
      )}
    </Card>
  )
}

function DocumentSection({ roomId, summary, documents, onChanged }) {
  const create = useAction()
  const [vendorId, setVendorId] = useState('')
  const [subject, setSubject] = useState('')

  const submit = useCallback(async () => {
    const done = await create.run(() =>
      evaultApi.createDocument(roomId, {
        vendor_document_id: vendorId.trim(),
        subject: subject.trim() || undefined,
      }),
    )
    if (done.ok) {
      setVendorId('')
      setSubject('')
      onChanged()
    }
  }, [create, onChanged, roomId, subject, vendorId])

  const rows = asList(documents)
  const byState = summary?.documents_by_state ?? {}

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">Executed agreements</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            The vendor document id is the join key the ready event carries, so it is required
            and unique per room.
          </p>
        </div>
        <Badge tone={rows.length > 0 ? 'update' : 'neutral'}>
          {summary?.documents ?? rows.length} recorded
        </Badge>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field label="Vendor document id" hint="The id the webhook payload will name." id="wf080-vendor">
          <input
            id="wf080-vendor"
            className={inputClass}
            value={vendorId}
            placeholder="pd_doc_northwind_001"
            onChange={(event) => setVendorId(event.target.value)}
          />
          <FieldError message={create.errors?.vendor_document_id} />
        </Field>
        <Field label="Subject, optional" hint="What the room calls it." id="wf080-subject">
          <input
            id="wf080-subject"
            className={inputClass}
            value={subject}
            placeholder="Master services agreement"
            onChange={(event) => setSubject(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4">
        <Button variant="primary" icon="plus" disabled={create.busy} onClick={submit}>
          Record the agreement
        </Button>
      </div>

      <RefusalNote refusal={create.refusal} onDismiss={create.clearRefusal} />

      {Object.keys(byState).length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {Object.entries(byState).map(([state, count]) => (
            <Badge key={state} tone={state === 'sealed' ? 'insert' : 'neutral'}>
              {count} {stateLabel(state)}
            </Badge>
          ))}
        </div>
      )}
    </Card>
  )
}

function DocumentPanel({ roomId, document, onChanged }) {
  const retrieve = useAction()
  const deliver = useAction()
  const download = useAction()
  const [environment, setEnvironment] = useState('production')
  const [watermark, setWatermark] = useState('')
  const [deliveryId, setDeliveryId] = useState('evt_demo_1')
  const [downloadReport, setDownloadReport] = useState(null)

  const sealedAllowedHere = document.sealed_allowed_here !== false
  const backPressure = document.back_pressure === true
  const waiting = document.latest_attempt?.outcome === 'back_pressure'

  const fire = useCallback(async () => {
    const done = await deliver.run(() =>
      evaultApi.deliverReady(roomId, document.vendor_document_id, deliveryId.trim() || 'evt_demo_1'),
    )
    if (done.ok) onChanged()
  }, [deliver, deliveryId, document.vendor_document_id, onChanged, roomId])

  const fetchSealed = useCallback(async () => {
    const done = await retrieve.run(() =>
      evaultApi.retrieveSealed(roomId, document.id, { environment }),
    )
    if (done.ok) onChanged()
  }, [environment, onChanged, retrieve, roomId, document.id])

  const fetchPlain = useCallback(async () => {
    const done = await retrieve.run(() =>
      evaultApi.retrievePlain(roomId, document.id, { environment, watermark }),
    )
    if (done.ok) onChanged()
  }, [environment, onChanged, retrieve, roomId, document.id, watermark])

  const fetchBytes = useCallback(async () => {
    setDownloadReport(null)
    const done = await download.run(() => evaultApi.fetchSealed(roomId, document.id))
    if (done.ok) setDownloadReport(done.result)
  }, [download, document.id, roomId])

  const artifacts = asList(document.artifacts)
  const sealed = artifacts.find((row) => row.variant === 'sealed') || null
  const plain = artifacts.filter((row) => row.variant === 'plain')

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-base font-semibold text-foreground">
            {document.subject || document.vendor_document_id}
          </h3>
          <p className="mt-0.5 break-all font-mono text-xs text-muted-foreground">
            {document.vendor_document_id}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DocumentStateBadge document={document} />
          <Badge tone={document.environment === 'production' ? 'update' : 'neutral'}>
            {document.environment} key
          </Badge>
        </div>
      </div>

      {/* The three facts a seller needs before pressing anything. */}
      <div className="mt-3 flex flex-wrap gap-2">
        <Badge tone={backPressure ? 'update' : 'neutral'}>
          {backPressure ? 'PDF being produced' : 'No PDF in progress'}
        </Badge>
        <Badge tone={sealed ? 'insert' : 'neutral'}>
          {sealed ? `Sealed: ${sealed.byte_length} bytes` : 'No sealed artifact yet'}
        </Badge>
        <Badge tone={plain.length > 0 ? 'restore' : 'neutral'}>
          {plain.length > 0 ? `${plain.length} plain copy` : 'No watermarked copy'}
        </Badge>
      </div>

      <RefusalNote refusal={retrieve.refusal} onDismiss={retrieve.clearRefusal} />

      {/* The 202 case, before the buttons, because it is the answer and not an error. */}
      {waiting && document.latest_attempt?.retry_after_seconds > 0 && (
        <div className="mt-3">
          <RetryBanner
            seconds={document.latest_attempt.retry_after_seconds}
            action={
              <Button icon="refresh" onClick={fetchBytes} disabled={download.busy}>
                Try the download
              </Button>
            }
          />
        </div>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Field label="Key environment" id="wf080-doc-env">
          <Select
            id="wf080-doc-env"
            value={environment}
            onChange={setEnvironment}
            disabled={document.environment === 'sandbox'}
          >
            {ENVIRONMENTS.map((row) => (
              <option key={row.id} value={row.id}>
                {row.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Delivery id for the next event" hint="Send it twice to see the dedupe." id="wf080-delivery">
          <input
            id="wf080-delivery"
            className={inputClass}
            value={deliveryId}
            onChange={(event) => setDeliveryId(event.target.value)}
          />
        </Field>
        <Field label="Watermark for the plain copy" hint="Plain endpoint only." id="wf080-watermark">
          <input
            id="wf080-watermark"
            className={inputClass}
            value={watermark}
            placeholder="NORTHWIND CONFIDENTIAL"
            onChange={(event) => setWatermark(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" icon="refresh" disabled={deliver.busy} onClick={fire}>
          Deliver the ready event
        </Button>
        <Button icon="database" disabled={retrieve.busy || !sealedAllowedHere} onClick={fetchSealed}>
          Retrieve the sealed PDF
        </Button>
        <Button icon="database" disabled={retrieve.busy} onClick={fetchPlain}>
          Retrieve with a watermark
        </Button>
        <Button icon="audit" disabled={download.busy} onClick={fetchBytes}>
          Download the sealed bytes
        </Button>
      </div>

      {!sealedAllowedHere && (
        <div className="mt-3">
          <Notice tone="warning" title="This key cannot reach the sealed endpoint">
            {document.environment === 'sandbox'
              ? 'The sealed endpoint is production key only. Use the watermarked plain endpoint instead. It returns the PDF without the digital seal.'
              : 'The sealed endpoint is production key only.'}
          </Notice>
        </div>
      )}

      <RefusalNote refusal={deliver.refusal} onDismiss={deliver.clearRefusal} />

      {downloadReport && (
        <div className="mt-3">
          {downloadReport.status === 202 ? (
            <RetryBanner
              seconds={downloadReport.retryAfter}
              source="the Retry-After header on the 202"
              action={
                <Button icon="refresh" onClick={fetchBytes} disabled={download.busy}>
                  Try again
                </Button>
              }
            />
          ) : (
            <Notice tone="success" title="The sealed bytes arrived">
              <p>
                <ByteCount value={downloadReport.bytes} /> of{' '}
                <span className="font-mono">{downloadReport.mediaType}</span>. The vendor sends
                no body with a 202, so this 200 is the only shape that carries bytes.
              </p>
              <p className="mt-1">
                <Digest value={downloadReport.digest} />
              </p>
            </Notice>
          )}
        </div>
      )}
      <RefusalNote refusal={download.refusal} onDismiss={download.clearRefusal} />

      {artifacts.length > 0 && (
        <div className="mt-4">
          <h4 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Artifacts
          </h4>
          <ul className="mt-2 space-y-2">
            {artifacts.map((artifact) => (
              <li key={artifact.id} className="rounded-sm border border-border-subtle p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={artifact.variant === 'sealed' ? 'insert' : 'restore'}>
                    {artifact.variant}
                  </Badge>
                  <OutcomeBadge
                    outcome={artifact.byte_stable ? 'retrieved' : 'duplicate'}
                    label={artifact.byte_stable ? 'Byte-stable' : 'Bytes vary per request'}
                  />
                  <ByteCount value={artifact.byte_length} />
                </div>
                {artifact.watermark && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Watermark: <span className="font-mono">{artifact.watermark}</span>
                  </p>
                )}
                <p className="mt-1">
                  <Digest value={artifact.sha256} />
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <AttemptLog document={document} />
      <DeliveryLog document={document} />
    </Card>
  )
}

/** The retrieval history, and where a 202's wait comes from. */
function AttemptLog({ document }) {
  const attempts = asList(document.attempts)
  if (attempts.length === 0) {
    return (
      <p className="mt-4 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
        No retrieval yet. A 202 is the answer to record, because the response carries no body
        and the record is what survives the tab closing.
      </p>
    )
  }
  return (
    <div className="mt-4 border-t border-border-subtle pt-3">
      <h4 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Retrievals
      </h4>
      <ul className="mt-2 space-y-1.5">
        {attempts.map((attempt) => (
          <li key={attempt.id} className="flex flex-wrap items-center gap-2 text-xs">
            <OutcomeBadge outcome={attempt.outcome} label={outcomeLabel(attempt.outcome)} />
            <span className="font-mono text-muted-foreground">{attempt.variant}</span>
            <span className="font-mono text-muted-foreground">{attempt.at}</span>
            {attempt.retry_after_seconds ? (
              <span className="font-mono text-muted-foreground">
                Retry-After {attempt.retry_after_seconds}s
              </span>
            ) : null}
            <span className="text-muted-foreground">{attempt.summary}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The webhook history, with the retry visible. */
function DeliveryLog({ document }) {
  const deliveries = asList(document.deliveries)
  if (deliveries.length === 0) {
    return (
      <p className="mt-3 text-xs text-muted-foreground">
        No delivery yet. This room hears nothing until the vendor fires the ready event.
      </p>
    )
  }
  return (
    <div className="mt-3">
      <h4 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Deliveries
      </h4>
      <ul className="mt-2 space-y-1.5">
        {deliveries.map((delivery) => (
          <li key={delivery.id} className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono text-foreground">{delivery.event || 'ready event'}</span>
            <span className="break-all font-mono text-muted-foreground">
              {delivery.delivery_id}
            </span>
            <span className="text-muted-foreground">from {delivery.delivery_id_source}</span>
            <RetryCountBadge deliveries={delivery.deliveries} />
            <span className="text-muted-foreground">
              {delivery.state_before} then {delivery.state_after}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function DecisionsSection({ vocabulary, decisions }) {
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        What the research left open
      </h2>
      <p className="mt-0.5 text-sm text-muted-foreground">
        The specification names each of these as undecided. Each one records the option this
        build took and the option it rejected, with the cost of the choice.
      </p>

      {vocabulary?.variants && (
        <div className="mt-4">
          <VariantMatrix variants={vocabulary.variants} />
        </div>
      )}

      {vocabulary && (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Throttle
            </h3>
            <p className="mt-1 text-sm text-foreground">
              {vocabulary.throttle.limit} retrievals per document inside{' '}
              {vocabulary.throttle.window_seconds} seconds. Past that the endpoint answers{' '}
              <span className="font-mono">429</span> and the code is{' '}
              <span className="font-mono">{vocabulary.throttle.code}</span>.
            </p>
          </div>
          <div>
            <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Dedupe
            </h3>
            <p className="mt-1 text-sm text-foreground">
              <span className="font-mono">{DEDUPE_HEADER}</span>. A repeat is applied once and
              counted on the row that was kept.
            </p>
          </div>
        </div>
      )}

      {decisions?.decisions?.length ? (
        <ul className="mt-4 space-y-3">
          {decisions.decisions.map((decision) => (
            <li key={decision.id} className="rounded-sm border border-border-subtle p-3">
              <p className="font-mono text-xs text-foreground">{decision.id}</p>
              <p className="mt-1 text-sm text-foreground">{decision.question}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Chose <span className="font-mono">{decision.chosen}</span>. {decision.rejected_because}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          The derivation register has not loaded. The API is reachable, so this is a request in
          flight rather than a missing record.
        </p>
      )}
    </Card>
  )
}

function EvaultDownloadPage() {
  const room = useRoom()
  const vocabulary = useAsync(() => evaultApi.vocabulary(), [])
  const decisions = useAsync(() => evaultApi.decisions(), [])
  const summary = useAsync(() => evaultApi.summary(room.selected), [room.selected])
  const documents = useAsync(() => evaultApi.documents(room.selected), [room.selected])

  const honesty = useHonesty(summary.data)
  const refreshAll = useCallback(() => {
    summary.refetch()
    documents.refetch()
  }, [documents, summary])

  const rows = asList(documents.data?.documents)

  if (room.loading) return <Spinner label="Loading rooms" />
  if (room.error) return <ErrorNote error={room.error} onRetry={room.refetch} />

  if (room.options.length === 0) {
    return (
      <EmptyState
        title="No rooms yet"
        description="This workflow attaches a webhook subscription and an executed agreement to a room, and there is no room to attach them to."
      />
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Download the executed agreement from the e-vault
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Subscribe to the PDF-ready event, take one delivery once even when the vendor
            retries it, then fetch the sealed artifact. A 202 with a Retry-After header is a
            normal outcome, not a failure.
          </p>
        </div>
        <div className="w-full sm:w-72">
          <Field label="Room" id="wf080-room">
            <Select id="wf080-room" value={room.selected} onChange={room.setRoomId}>
              {room.options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </div>

      {/* What the seal is worth, before anything that could download it. */}
      <Notice tone="info" title="What this room can verify">
        <p>{honesty.sealScope}</p>
        <p className="mt-1 text-xs">{honesty.noPolling}</p>
      </Notice>

      <div className="mt-3">
        <Notice tone="neutral" title="The trade-off between the two endpoints">
          <p>{honesty.tradeoff}</p>
        </Notice>
      </div>

      {summary.loading ? (
        <Spinner label="Loading the e-vault board" />
      ) : summary.error ? (
        <ErrorNote error={summary.error} onRetry={summary.refetch} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Listening"
            value={summary.data?.active_subscriptions ?? 0}
            hint={`${summary.data?.subscriptions ?? 0} subscription(s) held`}
            icon="audit"
          />
          <StatCard
            label="Agreements"
            value={summary.data?.documents ?? 0}
            hint={`${summary.data?.sealed_documents ?? 0} sealed`}
            icon="rooms"
          />
          <StatCard
            label="Deliveries"
            value={summary.data?.deliveries ?? 0}
            hint={`${summary.data?.retries_deduped ?? 0} retry deduped`}
            icon="database"
          />
          <StatCard
            label="Retrievals"
            value={summary.data?.attempts ?? 0}
            hint={`${summary.data?.artifacts ?? 0} artifact(s) stored`}
            icon="schema"
          />
        </div>
      )}

      <SubscriptionSection
        roomId={room.selected}
        summary={summary.data}
        vocabulary={vocabulary.data}
        onChanged={refreshAll}
      />

      <DocumentSection
        roomId={room.selected}
        summary={summary.data}
        documents={rows}
        onChanged={refreshAll}
      />

      {documents.loading ? (
        <Spinner label="Loading executed agreements" />
      ) : documents.error ? (
        <ErrorNote error={documents.error} onRetry={documents.refetch} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No executed agreement recorded"
          description="Record one above. The vendor document id is the join key the ready event carries, so nothing can be fetched without it."
        />
      ) : (
        <div className="space-y-4">
          {rows.map((row) => (
            <DocumentPanelRoom
              key={row.id}
              roomId={room.selected}
              documentId={row.id}
              onChanged={refreshAll}
            />
          ))}
        </div>
      )}

      <DecisionsSection vocabulary={vocabulary.data} decisions={decisions.data} />

      {honesty.effect === 'recorded_not_verified' && (
        <p className="text-xs text-muted-foreground">
          Every JSON response from this feature carries <span className="font-mono">effect</span>,{' '}
          <span className="font-mono">tradeoff</span>,{' '}
          <span className="font-mono">seal_scope</span> and{' '}
          <span className="font-mono">no_polling</span>, so a client cannot read a control here
          without also reading what it is worth. The one exception is the mirrored download
          route, which answers the vendor's own shape: a 202 with a Retry-After header and no
          body at all.
        </p>
      )}
    </div>
  )
}

/**
 * One agreement, fetched in full.
 *
 * The list route returns the projections; this fetches the detail, because the detail
 * carries the artifacts, the deliveries and the retrieval history, and a list endpoint
 * that returned all of it would make the list cost grow with every retrieval a room has
 * ever made.
 */
function DocumentPanelRoom({ roomId, documentId, onChanged }) {
  const detail = useAsync(() => evaultApi.document(roomId, documentId), [roomId, documentId])

  if (detail.loading) return <Spinner label="Loading the agreement" />
  if (detail.error) return <ErrorNote error={detail.error} onRetry={detail.refetch} />
  if (!detail.data) return null

  return <DocumentPanel roomId={roomId} document={detail.data} onChanged={onChanged} />
}

export default {
  id: 'wf-080-download-the-executed-agreement-from-the-e-vault',
  label: 'E-vault download',
  icon: 'database',
  iconPath:
    'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zm8-3v14c0 1.7-3.6 3-8 3s-8-1.3-8-3V5m16 7c0 1.7-3.6 3-8 3s-8-1.3-8-3',
  order: 460,
  Component: EvaultDownloadPage,
}