import { useCallback, useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
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
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { CHANGE_TYPE_TONE, eventStateLabel, newEvent, streamApi } from './api'
import { Banner, JsonField, LabelledToggle } from './primitives'

/**
 * WF-043: stream CRM record changes into the room in near real time.
 *
 * The page follows the researched flow rather than the data model, because the
 * flow is what an operator actually does:
 *
 *  1. **Orgs**: register the connected CRM, declare the edition, and enable
 *     Change Data Capture for the objects the room cares about. The edition gate
 *     is the researched part, so an org on a lower edition shows the refusal
 *     rather than a switch that quietly does nothing.
 *  2. **Channels**: the registry, with each channel's enrichment note. The
 *     standard channel is shown with the reason enrichment on it is refused; a
 *     custom channel has the field to add - the research's own example is the
 *     external ID.
 *  3. **Subscriptions**: open a long-lived one for a room and send a FetchRequest,
 *     because "the client can control the flow of events received" and a
 *     subscription starts with nothing requested.
 *  4. **Live activity / Realtime**: deliver a change event and watch it park. The
 *     panel says which events are parked and which reached the replica, because
 *     "only commits to the room's local replica when the key changes" means a
 *     parked event is the normal state, not a failure.
 *  5. **Replica and deal panel**: what the room now believes, grouped by the
 *     buyer whose panel the research says is refreshed.
 *  6. **Dataverse**: the Track changes property (irreversible, and the page says
 *     so before you press it) and the delta-link poll, whose four refused query
 *     options come back with the vendor's own message.
 *  7. **HubSpot**: the workflow webhook targets and the rate-limit exemption.
 *  8. **Judgements**: every inference the workflow rests on, served from the API
 *     so a reviewer reads the list instead of reconstructing it from a diff.
 *
 * Two researched rules are visible in the UI rather than only in the code,
 * because they change what a reader should expect to see:
 *
 *  - an event that carries enriched fields but is a **create or an undelete** has
 *    them dropped, because those events "contain all the populated fields", and the
 *    page says so next to the event rather than quietly ignoring them;
 *  - a **tombstone** is not a missing row. A delete keeps the record's sync key so
 *    an undelete can find it again, and the replica list separates live from
 *    deleted rather than hiding the tombstones.
 *
 * A note on motion: this page animates nothing, has no scroll reveal, and does
 * not poll, so `prefers-reduced-motion` is satisfied by there being nothing to
 * reduce. That is deliberate - an operations page that ticked every two seconds
 * would need the reduced-motion branch, and a page that does not tick does not.
 */

const FIELD_MAP_HINT =
  'CRM field -> room field. sync_key is the field the room resolves a record on, which is the research’s "e.g. the external ID".'

function tone(changeType) {
  return CHANGE_TYPE_TONE[changeType] || 'neutral'
}

/* -------------------------------------------------------------------------
 * Sections
 * ---------------------------------------------------------------------- */

function Summary({ summary, usage }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Change events"
        value={summary.events ?? 0}
        hint={`${summary.buffered_now ?? 0} parked right now`}
        icon="database"
      />
      <StatCard
        label="Replica rows"
        value={summary.replica_rows ?? 0}
        hint={`${summary.replica_deleted ?? 0} tombstoned`}
        icon="schema"
      />
      <StatCard
        label="Open subscriptions"
        value={summary.subscriptions_open ?? 0}
        hint={`${summary.channels ?? 0} channels`}
        icon="rooms"
      />
      <StatCard
        label="Delivered / requested"
        value={`${usage.totals?.events_delivered ?? 0}/${usage.totals?.events_requested ?? 0}`}
        hint={`${usage.totals?.fetch_outstanding ?? 0} outstanding`}
        icon="refresh"
      />
    </div>
  )
}

function Orgs({ orgs, vocabulary, onChanged, onNotice }) {
  const [system, setSystem] = useState('salesforce')
  const [edition, setEdition] = useState('Unlimited')
  // KNOWN GAP (not a lint fix): `enableCdc` below sends this list, but nothing
  // here lets the operator edit it, so enabling from this card always captures
  // the single entity "Opportunity". The Channels card two sections down does
  // bind its own `entities` field. Wiring a picker here changes what the page
  // renders and is a feature decision for the owning branch, not a lint pass.
  const [entities] = useState('Opportunity')

  const register = useCallback(async () => {
    try {
      await streamApi.registerOrg({ system, edition })
      onNotice({ tone: 'ok', text: `${system} org registered on ${edition}.` })
      onChanged()
    } catch (error) {
      onNotice({ tone: 'bad', text: `Could not register the org: ${error.message}` })
    }
  }, [system, edition, onChanged, onNotice])

  const enable = useCallback(
    async (orgId) => {
      try {
        await streamApi.enableCdc(orgId, {
          cdc_enabled: true,
          entities: entities.split(',').map((name) => name.trim()).filter(Boolean),
        })
        onNotice({ tone: 'ok', text: 'Change Data Capture enabled.' })
        onChanged()
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message, detail: error.status ? `HTTP ${error.status}` : '' })
      }
    },
    [entities, onChanged, onNotice],
  )

  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">Orgs and the edition gate</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Change Data Capture is “Available in: Enterprise, Performance, Unlimited, and Developer
        editions.” An org on anything else is refused rather than flagged, because a room that
        looks configured and streams nothing is worse than a room that knows it cannot.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Field id="org-system" label="System" hint="The vendor the research describes.">
          <select id="org-system" className={inputClass} value={system} onChange={(e) => setSystem(e.target.value)}>
            {(vocabulary?.crm_systems || []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        <Field id="org-edition" label="Edition" hint="Declared, not detected: this product holds no OAuth connection to a vendor org.">
          <input
            id="org-edition"
            className={inputClass}
            value={edition}
            list="editions"
            onChange={(e) => setEdition(e.target.value)}
          />
          <datalist id="editions">
            {(vocabulary?.cdc_editions || []).map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </Field>
        <div className="flex items-end">
          <Button icon="plus" onClick={register} className="cursor-pointer">
            Register org
          </Button>
        </div>
      </div>

      <div className="mt-4">
        {orgs.length === 0 ? (
          <EmptyState title="No org registered" description="Register one above to enable Change Data Capture." />
        ) : (
          <ul className="space-y-2">
            {orgs.map((org) => (
              <li
                key={org.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-subtle/40 p-3"
              >
                <div className="min-w-0">
                  <p className="font-mono text-sm text-foreground">
                    {org.system} · {org.edition}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {org.cdc_enabled
                      ? `Tracking ${(org.cdc_entities || []).join(', ')}`
                      : 'Change Data Capture not enabled'}
                    {org.org_name ? ` · ${org.org_name}` : ''}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {/* neutral, not the `delete` tone: an edition that does not
                      carry Change Data Capture is an absence, not a deletion,
                      and the destructive tint measures 3.75:1 against the
                      badge background -- under the 4.5:1 floor. */}
                  <Badge tone={org.edition_covers_cdc ? 'insert' : 'neutral'}>
                    {org.edition_covers_cdc ? 'CDC available' : 'no CDC'}
                  </Badge>
                  {org.cdc_enabled ? (
                    <Badge tone="update">enabled</Badge>
                  ) : (
                    <Button
                      icon="plus"
                      onClick={() => enable(org.id)}
                      className="cursor-pointer"
                      aria-label={`Enable Change Data Capture for the ${org.system} org`}
                    >
                      Enable CDC
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

function Channels({ channels, vocabulary, onChanged, onNotice }) {
  const [name, setName] = useState('/data/dsrOpportunities')
  const [orgId, setOrgId] = useState('')
  const [transport, setTransport] = useState('pubsub')
  const [fieldMap, setFieldMap] = useState(
    JSON.stringify(
      {
        sync_key: 'External_Id__c',
        account_field: 'Account_Name__c',
        fields: { External_Id__c: 'external_id', StageName: 'stage', Amount: 'amount' },
      },
      null,
      2,
    ),
  )
  const [entities, setEntities] = useState('Opportunity')
  const [enrich, setEnrich] = useState('External_Id__c')

  const create = useCallback(async () => {
    try {
      await streamApi.createChannel({
        name,
        org_id: orgId,
        entities: entities.split(',').map((x) => x.trim()).filter(Boolean),
        transport,
        field_map: JSON.parse(fieldMap),
      })
      onNotice({ tone: 'ok', text: `Channel ${name} created.` })
      onChanged()
    } catch (error) {
      onNotice({ tone: 'bad', text: error.message })
    }
  }, [name, orgId, entities, transport, fieldMap, onChanged, onNotice])

  const addEnrichment = useCallback(
    async (channelId) => {
      try {
        const row = await streamApi.enrich(channelId, enrich.split(',').map((x) => x.trim()).filter(Boolean))
        onNotice({
          tone: 'ok',
          text: `${row.added?.length || 0} field(s) added: ${(row.enriched_fields || []).join(', ')}`,
        })
        onChanged()
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message })
      }
    },
    [enrich, onChanged, onNotice],
  )

  const dropEnrichment = useCallback(
    async (channelId, field) => {
      try {
        await streamApi.unenrich(channelId, field)
        onNotice({ tone: 'ok', text: `No longer enriching ${field}.` })
        onChanged()
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message })
      }
    },
    [onChanged, onNotice],
  )

  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">Subscription channels</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        “A subscription channel is a stream of change events that correspond to one or more
        entities.” The channel name is <strong>case-sensitive</strong>, so{' '}
        <code className="font-mono">{vocabulary?.standard_channel}</code> and a lower-case spelling of
        it are two different channels - and the standard one is the one enrichment is refused on.
      </p>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div className="flex flex-col gap-3">
          <Field id="channel-name" label="Channel name" hint="The identity. Case-sensitive.">
            <input id="channel-name" className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field id="channel-org" label="Org" hint="A CDC-enabled org.">
            <input id="channel-org" className={inputClass} value={orgId} onChange={(e) => setOrgId(e.target.value)} />
          </Field>
          <Field id="channel-entities" label="Entities" hint="Comma separated.">
            <input
              id="channel-entities"
              className={inputClass}
              value={entities}
              onChange={(e) => setEntities(e.target.value)}
            />
          </Field>
          <Field id="channel-transport" label="Transport" hint="Decides whether enrichment is available.">
            <select
              id="channel-transport"
              className={inputClass}
              value={transport}
              onChange={(e) => setTransport(e.target.value)}
            >
              {(vocabulary?.transports || []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.id} ({row.vendor})
                </option>
              ))}
            </select>
          </Field>
          <JsonField
            id="channel-field-map"
            label="Field map"
            hint={FIELD_MAP_HINT}
            value={fieldMap}
            onChange={setFieldMap}
            rows={9}
          />
          <div>
            <Button icon="plus" onClick={create} className="cursor-pointer">
              Create channel
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <Field
            id="channel-enrich"
            label="Enriched fields to add"
            hint="The research's example is the external ID: the field the room needs to resolve a record and the change did not touch."
          >
            <input id="channel-enrich" className={inputClass} value={enrich} onChange={(e) => setEnrich(e.target.value)} />
          </Field>
          {channels.length === 0 ? (
            <EmptyState title="No channels" description="Create one above to stream change events." />
          ) : (
            <ul className="space-y-2">
              {channels.map((channel) => {
                const supported = (vocabulary?.transports || []).find((t) => t.id === channel.transport)
                return (
                  <li key={channel.id} className="rounded-lg border border-border-subtle/40 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="min-w-0 truncate font-mono text-sm text-foreground">{channel.name}</p>
                      <div className="flex items-center gap-1">
                        <Badge tone={channel.kind === 'standard' ? 'neutral' : 'insert'}>{channel.kind}</Badge>
                        <Badge tone="update">{channel.transport}</Badge>
                        {channel.buffer_matches_recommendation ? null : (
                          // A buffer below the researched 3 MB is a caution
                          // worth flagging, not a deletion, and the destructive
                          // tint measures 3.75:1 on its own background.
                          <Badge tone="update">non-recommended buffer</Badge>
                        )}
                      </div>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {channel.entities?.join(', ')}
                      {supported?.wire_format ? ` · ${supported.wire_format.toUpperCase()}` : ' · wire format not published'}
                      {` · ${Math.round((channel.buffer_bytes || 0) / (1024 * 1024))} MB buffer`}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">{channel.enrichment_note}</p>
                    {(channel.enriched_fields || []).length > 0 ? (
                      <ul className="mt-2 flex flex-wrap gap-1">
                        {channel.enriched_fields.map((field) => (
                          <li key={field}>
                            <button
                              type="button"
                              onClick={() => dropEnrichment(channel.id, field)}
                              aria-label={`Stop enriching ${field} on ${channel.name}`}
                              className="inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-md border border-accent/30 bg-accent/10 px-2 font-mono text-xs text-accent"
                            >
                              {field}
                              <Icon name="close" size={12} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <div className="mt-2">
                      <Button
                        icon="plus"
                        className="cursor-pointer"
                        onClick={() => addEnrichment(channel.id)}
                        aria-label={`Add enriched fields to ${channel.name}`}
                      >
                        Add enrichment
                      </Button>
                    </div>
                    {(channel.field_map_findings || []).length > 0 ? (
                      <ul className="mt-2 space-y-1">
                        {channel.field_map_findings.map((finding) => (
                          <li key={finding.code} className="text-xs text-muted-foreground">
                            <span className="font-mono text-foreground">{finding.code}</span>: {finding.detail}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>
    </Card>
  )
}

function Subscriptions({ subscriptions, channels, rooms, roomId, onRoom, onChanged, onNotice }) {
  const [channelId, setChannelId] = useState('')
  const [numRequested, setNumRequested] = useState(10)

  const open = useCallback(async () => {
    try {
      await streamApi.openSubscription(roomId, { channel_id: channelId })
      onNotice({ tone: 'ok', text: 'Subscription opened. It starts with nothing requested — send a FetchRequest next.' })
      onChanged()
    } catch (error) {
      onNotice({ tone: 'bad', text: error.message })
    }
  }, [roomId, channelId, onChanged, onNotice])

  const fetchMore = useCallback(
    async (id) => {
      try {
        const row = await streamApi.fetchMore(id, Number(numRequested) || 1)
        onNotice({
          tone: 'ok',
          text: `Requested ${numRequested} more. ${row.usage.fetch_outstanding} outstanding.`,
        })
        onChanged()
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message })
      }
    },
    [numRequested, onChanged, onNotice],
  )

  const close = useCallback(
    async (id) => {
      try {
        const row = await streamApi.close(id)
        const flushed = (row.flushed || []).length
        onNotice({
          tone: 'ok',
          text: `Closed. ${flushed} parked transaction${flushed === 1 ? '' : 's'} flushed.`,
        })
        onChanged()
      } catch (error) {
        onNotice({
          tone: 'bad',
          text: `The close was refused: ${error.message}`,
          detail: 'The transaction is still parked, so nothing was lost.',
        })
      }
    },
    [onChanged, onNotice],
  )

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Long-lived subscriptions</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            “The Subscribe method uses bidirectional streaming, enabling the client to request more
            events as it consumes events.” A subscription therefore starts with nothing outstanding,
            and an event delivered with nothing requested is refused.
          </p>
        </div>
        <RoomPicker rooms={rooms} roomId={roomId} onRoom={onRoom} />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-4">
        <Field id="sub-channel" label="Channel">
          <select id="sub-channel" className={inputClass} value={channelId} onChange={(e) => setChannelId(e.target.value)}>
            <option value="">Choose a channel…</option>
            {channels.map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.name}
              </option>
            ))}
          </select>
        </Field>
        <Field id="sub-fetch" label="FetchRequest" hint="The number of requested events.">
          <input
            id="sub-fetch"
            type="number"
            min="1"
            className={inputClass}
            value={numRequested}
            onChange={(e) => setNumRequested(e.target.value)}
          />
        </Field>
        <div className="flex items-end">
          <Button icon="plus" onClick={open} disabled={!channelId || !roomId} className="cursor-pointer">
            Open
          </Button>
        </div>
      </div>

      <div className="mt-4">
        {subscriptions.length === 0 ? (
          <EmptyState
            title="No subscriptions"
            description="Open one on a channel to start receiving change events."
          />
        ) : (
          <ul className="space-y-2">
            {subscriptions.map((sub) => (
              <li key={sub.id} className="rounded-lg border border-border-subtle/40 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="min-w-0 truncate font-mono text-sm text-foreground">{sub.channel_name}</p>
                  <div className="flex items-center gap-1">
                    <Badge tone={sub.state === 'open' ? 'insert' : 'neutral'}>{sub.state}</Badge>
                    <Badge tone="update">{sub.transport}</Badge>
                    {sub.wire_format ? <Badge>{sub.wire_format}</Badge> : null}
                  </div>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  requested {sub.usage?.events_requested ?? 0} · delivered{' '}
                  {sub.usage?.events_delivered ?? 0} · outstanding {sub.usage?.fetch_outstanding ?? 0} ·
                  committed {sub.usage?.transactions_committed ?? 0} transactions · buffer{' '}
                  {sub.usage?.buffer_bytes ?? 0} B of {sub.usage?.buffer_limit_bytes ?? 0} B
                </p>
                {sub.usage?.buffer_exceeds_recommendation ? (
                  <p className="mt-1 text-xs text-destructive">
                    The buffer is over the vendor’s 3 MB recommendation. The sizing is tunable, so
                    this is a note rather than a refusal.
                  </p>
                ) : null}
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    icon="refresh"
                    className="cursor-pointer"
                    disabled={sub.state !== 'open'}
                    onClick={() => fetchMore(sub.id)}
                    aria-label={`Request more events on ${sub.channel_name}`}
                  >
                    FetchRequest
                  </Button>
                  <Button
                    icon="close"
                    variant="danger"
                    className="cursor-pointer"
                    disabled={sub.state !== 'open'}
                    onClick={() => close(sub.id)}
                    aria-label={`Close the subscription on ${sub.channel_name}`}
                  >
                    Close and flush
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

function LiveActivity({ events, buffer, roomId, onChanged, onNotice }) {
  const openSubs = (buffer?.subscriptions || []).filter((s) => s.state === 'open')
  const [subscriptionId, setSubscriptionId] = useState('')
  const [changeType, setChangeType] = useState('UPDATE')
  const [transactionKey, setTransactionKey] = useState('')
  const [sequenceNumber, setSequenceNumber] = useState(1)
  const [changedFields, setChangedFields] = useState('')
  const [payload, setPayload] = useState('{\n  "StageName": "Negotiation"\n}')
  const [enrichedFields, setEnrichedFields] = useState('{\n  "External_Id__c": "dsr-1"\n}')

  const send = useCallback(async () => {
    try {
      const body = newEvent(subscriptionId, {
        changeType,
        transactionKey: transactionKey || undefined,
        sequenceNumber: Number(sequenceNumber) || 1,
        changedFields: changedFields
          .split(',')
          .map((name) => name.trim())
          .filter(Boolean),
        payload: JSON.parse(payload),
        enrichedFields: JSON.parse(enrichedFields),
      })
      const result = await streamApi.deliver(roomId, body)
      if (result.outcome === 'duplicate') {
        onNotice({
          tone: 'bad',
          text: `Duplicate: sequence ${result.sequence_number} is already parked under ${result.buffer.transaction_key}.`,
        })
      } else {
        const committed = (result.committed || []).length
        onNotice({
          tone: 'ok',
          text: `Parked under ${result.buffer.transaction_key}. ${
            committed
              ? `${committed} transaction(s) committed to the replica.`
              : 'Nothing committed — a change commits when the transactionKey changes.'
          }`,
        })
      }
      onChanged()
    } catch (error) {
      onNotice({ tone: 'bad', text: error.message, detail: error.status ? `HTTP ${error.status}` : '' })
    }
  }, [
    roomId,
    subscriptionId,
    changeType,
    transactionKey,
    sequenceNumber,
    changedFields,
    payload,
    enrichedFields,
    onChanged,
    onNotice,
  ])

  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">Live activity / Realtime</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        “On each event the room checks changeType, buffers the change under its transactionKey, and
        only commits to the room’s local replica when the key changes.” A parked event is the
        normal state, not a failure.
      </p>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div className="flex flex-col gap-3">
          <Field id="event-sub" label="Subscription">
            <select
              id="event-sub"
              className={inputClass}
              value={subscriptionId}
              onChange={(e) => setSubscriptionId(e.target.value)}
            >
              <option value="">Choose a subscription…</option>
              {openSubs.map((sub) => (
                <option key={sub.id} value={sub.id}>
                  {sub.channel_name}
                </option>
              ))}
            </select>
          </Field>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field id="event-type" label="changeType">
              <select id="event-type" className={inputClass} value={changeType} onChange={(e) => setChangeType(e.target.value)}>
                {['CREATE', 'UPDATE', 'DELETE', 'UNDELETE'].map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="event-txn" label="transactionKey" hint="A new key commits the parked one.">
              <input
                id="event-txn"
                className={inputClass}
                value={transactionKey}
                onChange={(e) => setTransactionKey(e.target.value)}
                placeholder="blank = a fresh key"
              />
            </Field>
            <Field id="event-seq" label="sequenceNumber">
              <input
                id="event-seq"
                type="number"
                className={inputClass}
                value={sequenceNumber}
                onChange={(e) => setSequenceNumber(e.target.value)}
              />
            </Field>
          </div>
          <Field id="event-changed" label="changedFields" hint="Empty means the transport did not say. A field not listed is unchanged, so the replica keeps it.">
            <input
              id="event-changed"
              className={inputClass}
              value={changedFields}
              onChange={(e) => setChangedFields(e.target.value)}
            />
          </Field>
          <JsonField id="event-payload" label="Record payload" value={payload} onChange={setPayload} />
          <JsonField
            id="event-enriched"
            label="Enriched fields"
            hint="Only used on update and delete: a create or an undelete contains all the populated fields already."
            value={enrichedFields}
            onChange={setEnrichedFields}
          />
          <div>
            <Button icon="plus" onClick={send} disabled={!subscriptionId || !roomId} className="cursor-pointer">
              Deliver change event
            </Button>
          </div>
        </div>

        <div>
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            What is parked
          </h3>
          <ul className="mt-2 space-y-2">
            {(buffer?.subscriptions || [])
              .filter((sub) => (sub.parked || []).length > 0)
              .map((sub) => (
                <li key={sub.id} className="rounded-lg border border-border-subtle/40 p-3">
                  <p className="font-mono text-sm text-foreground">{sub.channel_name}</p>
                  <ul className="mt-1 space-y-1">
                    {sub.parked.map((parked) => (
                      <li key={parked.transaction_key} className="text-xs text-muted-foreground">
                        <span className="font-mono text-foreground">{parked.transaction_key}</span>:{' '}
                        {parked.event_count} event(s), sequence {parked.sequence_range.join('–')},{' '}
                        {parked.buffer_bytes} B
                        {parked.sequence_gaps.length > 0 ? (
                          <span className="text-destructive">
                            {' '}
                            — gap of {parked.sequence_gaps[0].missing} reported, not reconciled
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            {(buffer?.subscriptions || []).every((sub) => (sub.parked || []).length === 0) ? (
              <li>
                <EmptyState
                  title="Nothing parked"
                  description="Every transaction that arrived has been committed, or none has arrived yet."
                />
              </li>
            ) : null}
          </ul>

          <h3 className="mt-4 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Delivered events
          </h3>
          <ul className="mt-2 max-h-96 space-y-2 overflow-y-auto pr-1">
            {events.length === 0 ? (
              <li>
                <EmptyState title="No events yet" description="Deliver one above." />
              </li>
            ) : (
              events.map((event) => (
                <li key={event.id} className="rounded-lg border border-border-subtle/40 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-1">
                      <Badge tone={tone(event.change_type)}>{event.change_type}</Badge>
                      <Badge>{event.transaction_key}#{event.sequence_number}</Badge>
                    </div>
                    <span className="text-xs text-muted-foreground" title={absoluteTime(event.commit_timestamp)}>
                      {relativeTime(event.commit_timestamp)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{eventStateLabel(event)}</p>
                  {event.enrichment_note ? (
                    <p className="mt-1 text-xs text-amber-300">{event.enrichment_note}</p>
                  ) : null}
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-muted-foreground">Payload</summary>
                    <JsonView value={event.payload} />
                  </details>
                </li>
              ))
            )}
          </ul>
        </div>
      </div>
    </Card>
  )
}

function Replica({ replica, panel, invalidations }) {
  const [showDeleted, setShowDeleted] = useState(true)
  const rows = (replica || []).filter((row) => showDeleted || row.state !== 'deleted')

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-foreground">The room’s local replica</h2>
          <Badge tone="neutral">
            {replica?.live ?? 0} live · {replica?.deleted ?? 0} deleted
          </Badge>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          A delete commits a <strong>tombstone</strong> rather than removing the row, because the
          research guarantees undelete events and a replica with no row to match one against could
          only guess. So the tombstones are shown rather than hidden.
        </p>
        <div className="mt-3">
          <LabelledToggle
            id="show-deleted"
            label="Show tombstones"
            hint="A room asking which records the CRM has taken away is asking exactly this."
            checked={showDeleted}
            onChange={setShowDeleted}
          />
        </div>
        <ul className="mt-3 space-y-2">
          {rows.length === 0 ? (
            <li>
              <EmptyState title="No replica rows" description="Nothing has committed yet." />
            </li>
          ) : (
            rows.map((row) => (
              <li
                key={row.replica_id}
                className={`rounded-lg border p-3 ${
                  row.state === 'deleted' ? 'border-destructive/40' : 'border-border-subtle/40'
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-mono text-sm text-foreground">{row.fields.external_id}</p>
                  <div className="flex items-center gap-1">
                    <Badge tone={tone(row.last_change_type)}>{row.last_change_type}</Badge>
                    <Badge tone={row.state === 'deleted' ? 'delete' : 'insert'}>{row.state}</Badge>
                  </div>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {row.account || 'no buyer named'} · {absoluteTime(row.last_commit_timestamp)}
                </p>
                <JsonView value={row.fields} />
              </li>
            ))
          )}
        </ul>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-foreground">Deal panel and refreshes</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          “The room refreshes the affected buyer’s deal panel.” The research does not say where the
          buyer comes from, so a channel whose field map declares no{' '}
          <code className="font-mono">account_field</code> produces a refresh that says so rather
          than guessing a rep’s buyer.
        </p>

        <ul className="mt-3 space-y-3">
          {(panel?.accounts || []).map((group) => (
            <li key={group.account} className="rounded-lg border border-border-subtle/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-mono text-sm text-foreground">{group.account}</p>
                <Badge>{group.records} record(s)</Badge>
              </div>
              <ul className="mt-2 space-y-1">
                {group.rows.map((row) => (
                  <li key={row.replica_id} className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono text-foreground">{row.fields.external_id}</span>
                    <Badge tone={tone(row.last_change_type)}>{row.last_change_type}</Badge>
                    <Badge tone={row.state === 'deleted' ? 'delete' : 'insert'}>{row.state}</Badge>
                    <span className="text-muted-foreground">
                      {Object.entries(row.fields)
                        .filter(([name]) => name !== 'external_id')
                        .map(([name, value]) => `${name}=${value}`)
                        .join(' · ')}
                    </span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
          {(panel?.accounts || []).length === 0 ? (
            <li>
              <EmptyState title="Panel empty" description="Nothing has committed to the replica yet." />
            </li>
          ) : null}
        </ul>

        <h3 className="mt-4 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          Refreshes
        </h3>
        <ul className="mt-2 space-y-1">
          {(invalidations || []).slice(0, 12).map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-2 text-xs">
              <Badge tone={tone(row.change_type)}>{row.change_type}</Badge>
              <span className="font-mono text-foreground">{row.external_id}</span>
              <span className="text-muted-foreground">{row.account || 'no buyer named'}</span>
              {row.resolved ? null : <Badge tone="delete">unresolved</Badge>}
              <span className="text-muted-foreground" title={absoluteTime(row.created_at)}>
                {relativeTime(row.created_at)}
              </span>
            </li>
          ))}
          {(invalidations || []).length === 0 ? (
            <li className="text-xs text-muted-foreground">No refreshes yet.</li>
          ) : null}
        </ul>
      </Card>
    </div>
  )
}

function Dataverse({ tables, vocabulary, onChanged, onNotice }) {
  const [logicalName, setLogicalName] = useState('account')
  const [deltatoken, setDeltatoken] = useState('')
  const [select, setSelect] = useState('accountid, name')

  const declare = useCallback(async () => {
    try {
      await streamApi.declareTable({ logical_name: logicalName })
      onNotice({ tone: 'ok', text: `Table ${logicalName} declared. Change tracking is off until you turn it on.` })
      onChanged()
    } catch (error) {
      onNotice({ tone: 'bad', text: error.message })
    }
  }, [logicalName, onChanged, onNotice])

  const enable = useCallback(
    async (id) => {
      try {
        const row = await streamApi.enableTracking(id)
        onNotice({
          tone: 'ok',
          text: row.already_enabled
            ? 'Already tracking changes on this table.'
            : 'Track changes is on. It cannot be turned off again.',
        })
        onChanged()
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message })
      }
    },
    [onChanged, onNotice],
  )

  const disable = useCallback(
    async (id) => {
      try {
        await streamApi.disableTracking(id)
        onNotice({ tone: 'ok', text: 'Disabled.' })
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message })
      }
    },
    [onNotice],
  )

  const doPoll = useCallback(
    async (id) => {
      try {
        const options = { select }
        if (deltatoken) options.deltatoken = deltatoken
        const row = await streamApi.poll(id, {
          prefer: vocabulary?.dataverse?.track_changes_preference || 'odata.track-changes',
          options,
        })
        onNotice({ tone: 'ok', text: `Delta link: ${row['@odata.deltaLink']}`, detail: row['@odata.deltaLink'] })
        onChanged()
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message })
      }
    },
    [vocabulary, select, deltatoken, onChanged, onNotice],
  )

  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">Dataverse change tracking</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        “After you enable change tracking for a table, you can’t disable it.” The one genuinely
        irreversible rule in this workflow, so the button says so before you press it and the API
        refuses either way.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Field id="dv-table" label="Logical name" hint="The delta link is scoped to one table.">
          <input id="dv-table" className={inputClass} value={logicalName} onChange={(e) => setLogicalName(e.target.value)} />
        </Field>
        <Field id="dv-select" label="$select" hint="Allowed. $filter, $orderby, $expand and $top are not.">
          <input id="dv-select" className={inputClass} value={select} onChange={(e) => setSelect(e.target.value)} />
        </Field>
        <Field id="dv-token" label="$deltatoken" hint="Blank is a full read; the table’s own token is incremental.">
          <input id="dv-token" className={inputClass} value={deltatoken} onChange={(e) => setDeltatoken(e.target.value)} />
        </Field>
      </div>
      <div className="mt-3">
        <Button icon="plus" onClick={declare} className="cursor-pointer">
          Declare table
        </Button>
      </div>

      <ul className="mt-4 space-y-2">
        {tables.length === 0 ? (
          <li>
            <EmptyState title="No tables" description="Declare one above." />
          </li>
        ) : (
          tables.map((table) => (
            <li key={table.id} className="rounded-lg border border-border-subtle/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-mono text-sm text-foreground">
                  {table.logical_name} → {table.entity_set}
                </p>
                <div className="flex items-center gap-1">
                  <Badge tone={table.track_changes ? 'insert' : 'neutral'}>
                    {table.track_changes ? 'tracking' : 'not tracking'}
                  </Badge>
                  <Badge>{table.poll_count} poll(s)</Badge>
                </div>
              </div>
              {table.delta_link ? (
                <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{table.delta_link}</p>
              ) : null}
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  icon="plus"
                  className="cursor-pointer"
                  onClick={() => enable(table.id)}
                  aria-label={`Turn Track changes on for ${table.logical_name}`}
                >
                  Track changes
                </Button>
                <Button
                  icon="refresh"
                  className="cursor-pointer"
                  disabled={!table.track_changes}
                  onClick={() => doPoll(table.id)}
                  aria-label={`Poll ${table.logical_name} with its delta link`}
                >
                  Delta poll
                </Button>
                <Button
                  icon="close"
                  variant="danger"
                  className="cursor-pointer"
                  onClick={() => disable(table.id)}
                  aria-label={`Try to turn Track changes off for ${table.logical_name}`}
                >
                  Try to disable
                </Button>
              </div>
            </li>
          ))
        )}
      </ul>
    </Card>
  )
}

function HubSpot({ subscriptions, usage, onChanged, onNotice }) {
  const [targetUrl, setTargetUrl] = useState('https://hooks.example.invalid/stage-advanced')
  const [workflowId, setWorkflowId] = useState('wf-8841')

  const register = useCallback(async () => {
    try {
      const row = await streamApi.registerHubspot({
        target_url: targetUrl,
        workflow_id: workflowId,
        workflow_name: workflowId,
      })
      onNotice({ tone: 'ok', text: `Registered. ${row.capacity.remaining} of the cap left.` })
      onChanged()
    } catch (error) {
      onNotice({ tone: 'bad', text: error.message })
    }
  }, [targetUrl, workflowId, onChanged, onNotice])

  const remove = useCallback(
    async (id) => {
      try {
        await streamApi.deleteHubspot(id)
        onNotice({ tone: 'ok', text: 'Cancelled. The slot is free.' })
        onChanged()
      } catch (error) {
        onNotice({ tone: 'bad', text: error.message })
      }
    },
    [onChanged, onNotice],
  )

  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">HubSpot workflow webhooks</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        “Webhooks can be triggered as an action in any workflow, so you can use any workflow starting
        conditions as the criteria.” The criteria belong to HubSpot, not to this room, and there is
        deliberately <strong>no</strong> subscription REST surface here: the research records that
        its documentation could not be read, so no endpoint is claimed.
      </p>

      <div className="mt-3 grid gap-4 sm:grid-cols-3">
        <StatCard label="Live subscriptions" value={usage?.capacity?.live ?? 0} hint={`cap ${usage?.capacity?.limit ?? 1000}`} icon="database" />
        <StatCard label="Calls received" value={usage?.calls_received ?? 0} hint={`${usage?.calls_exempt_from_rate_limit ?? 0} exempt from the rate limit`} icon="refresh" />
        <StatCard label="Budget spent" value={`${usage?.budget?.spent ?? 0}/${usage?.budget?.limit ?? 0}`} hint="App calls. Workflow calls never cost any." icon="audit" />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <Field id="hs-url" label="Target URL">
          <input id="hs-url" className={inputClass} value={targetUrl} onChange={(e) => setTargetUrl(e.target.value)} />
        </Field>
        <Field id="hs-workflow" label="Workflow id" hint="Fired on that workflow’s starting conditions.">
          <input id="hs-workflow" className={inputClass} value={workflowId} onChange={(e) => setWorkflowId(e.target.value)} />
        </Field>
        <div className="flex items-end">
          <Button icon="plus" onClick={register} className="cursor-pointer">
            Register
          </Button>
        </div>
      </div>

      <ul className="mt-4 space-y-2">
        {subscriptions.length === 0 ? (
          <li>
            <EmptyState title="No webhook targets" description="Register one above." />
          </li>
        ) : (
          subscriptions.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-subtle/40 p-3">
              <div className="min-w-0">
                <p className="truncate font-mono text-sm text-foreground">{row.target_url}</p>
                <p className="text-xs text-muted-foreground">
                  {row.workflow_id || 'no workflow'} · {row.criteria_owned_by} · received{' '}
                  {row.calls_received ?? 0}, exempt {row.calls_exempt_from_rate_limit ?? 0}
                </p>
              </div>
              <Button
                icon="trash"
                variant="danger"
                className="cursor-pointer"
                onClick={() => remove(row.id)}
                aria-label={`Cancel the webhook target ${row.target_url}`}
              >
                Cancel
              </Button>
            </li>
          ))
        )}
      </ul>
    </Card>
  )
}

function Judgements({ inferences }) {
  const [open, setOpen] = useState(null)
  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">Every judgement this workflow makes</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        The research names the six fields of a change event, the four change types, the buffering
        rule, the channel-name case rule, the enrichment isolation rule, the flow control, the 3 MB
        buffer, the Dataverse header and its four refused options, the annotation, and HubSpot’s cap
        and exemption. It does not say what happens to the last transaction in a stream, where a
        buyer comes from, or what an unresolvable event should do. Those are below, named, so they can
        be argued with by name rather than found in a comment.
      </p>
      <ul className="mt-3 space-y-1">
        {(inferences?.inferences || []).map((entry) => (
          <li key={entry.id} className="rounded-lg border border-border-subtle/40">
            <button
              type="button"
              onClick={() => setOpen(open === entry.id ? null : entry.id)}
              aria-expanded={open === entry.id}
              className="flex min-h-11 w-full cursor-pointer items-center justify-between gap-2 px-3 text-left"
            >
              <span className="min-w-0">
                <span className="block font-mono text-sm text-foreground">{entry.id}</span>
                <span className="block text-xs text-muted-foreground">{entry.topic}</span>
              </span>
              <Icon name="chevron" size={14} />
            </button>
            {open === entry.id ? (
              <div className="border-t border-border-subtle/40 px-3 py-2 text-xs text-muted-foreground">
                <p>
                  <span className="font-semibold text-foreground">What the research says: </span>
                  {entry.basis}
                </p>
                <p className="mt-2">
                  <span className="font-semibold text-foreground">Why: </span>
                  {entry.why}
                </p>
                <p className="mt-2">
                  <span className="font-semibold text-foreground">Change it: </span>
                  {entry.change_it}
                </p>
                <p className="mt-2">
                  <span className="font-semibold text-foreground">Blast radius: </span>
                  {entry.blast_radius}
                </p>
                <details className="mt-2">
                  <summary className="cursor-pointer">The value</summary>
                  <JsonView value={entry.value} />
                </details>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function RoomPicker({ rooms, roomId, onRoom }) {
  return (
    <Field id="room-picker" label="Room" hint="The changes belong to a buyer inside a room.">
      <select id="room-picker" className={inputClass} value={roomId} onChange={(e) => onRoom(e.target.value)}>
        <option value="">Choose a room…</option>
        {rooms.map((room) => (
          <option key={room.id} value={room.id}>
            {room.name || room.account || room.id}
          </option>
        ))}
      </select>
    </Field>
  )
}

/* -------------------------------------------------------------------------
 * Page
 * ---------------------------------------------------------------------- */

export default function CrmChangeStream() {
  const [roomId, setRoomId] = useState('')
  const [notice, setNotice] = useState(null)
  const [nonce, setNonce] = useState(0)
  const refetch = useCallback(() => setNonce((n) => n + 1), [])

  const vocabulary = useAsync(() => streamApi.vocabulary(), [])
  const rooms = useAsync(() => streamApi.rooms(), [])
  const orgs = useAsync(() => streamApi.orgs(), [nonce])
  const channels = useAsync(() => streamApi.channels(), [nonce])
  const subscriptions = useAsync(() => streamApi.subscriptions(), [nonce])
  const tables = useAsync(() => streamApi.tables(), [nonce])
  const hubspotSubscriptions = useAsync(() => streamApi.hubspotSubscriptions(), [nonce])
  const hubspotUsage = useAsync(() => streamApi.hubspotUsage(), [nonce])
  const usage = useAsync(() => streamApi.usage(), [nonce])
  const summary = useAsync(() => streamApi.summary({ room_id: roomId || undefined }), [roomId, nonce])
  const buffer = useAsync(() => (roomId ? streamApi.buffer(roomId) : Promise.resolve(null)), [roomId, nonce])
  const events = useAsync(
    () => (roomId ? streamApi.events(roomId, { limit: 40 }) : Promise.resolve({ events: [] })),
    [roomId, nonce],
  )
  const replica = useAsync(
    () => (roomId ? streamApi.replica(roomId) : Promise.resolve({ replica: [] })),
    [roomId, nonce],
  )
  const panel = useAsync(() => (roomId ? streamApi.dealPanel(roomId) : Promise.resolve(null)), [roomId, nonce])
  const invalidations = useAsync(
    () => (roomId ? streamApi.invalidations(roomId) : Promise.resolve({ invalidations: [] })),
    [roomId, nonce],
  )
  const inferences = useAsync(() => streamApi.inferences(), [])

  // A room-scoped read that failed while the rest loaded is worth saying out
  // loud, but only once and at the bottom: a rep looking at the channels should
  // still see them while the deal panel recovers. The first error wins, so the
  // note names one thing rather than six.
  const panelError = [buffer, events, replica, panel, invalidations].find((hook) => hook.error)?.error || null

  const roomList = rooms.data?.records || []
  if (!roomId && roomList.length > 0) {
    // Defaulting rather than blocking: the first room is the one the seeder
    // attached the demo rows to, and an operator can change it.
    queueMicrotask(() => setRoomId(roomList[0].id))
  }

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading the change stream" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold text-foreground">Stream CRM record changes into the room</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Change Data Capture channels, per-transaction buffering, the room’s local replica, the
          buyer’s deal panel, the Dataverse delta link and the HubSpot workflow webhook — all from
          one research document, with the judgement calls named and served.
        </p>
      </header>

      <Summary
        summary={summary.data || {}}
        usage={usage.data || { totals: {} }}
      />
      <Banner notice={notice} />

      <Orgs
        orgs={orgs.data?.orgs || []}
        vocabulary={vocabulary.data}
        onChanged={refetch}
        onNotice={setNotice}
      />
      <Channels
        channels={channels.data?.channels || []}
        vocabulary={vocabulary.data}
        onChanged={refetch}
        onNotice={setNotice}
      />
      <Subscriptions
        subscriptions={subscriptions.data?.subscriptions || []}
        channels={channels.data?.channels || []}
        rooms={roomList}
        roomId={roomId}
        onRoom={setRoomId}
        onChanged={refetch}
        onNotice={setNotice}
      />
      <LiveActivity
        events={events.data?.events || []}
        buffer={buffer.data}
        roomId={roomId}
        onChanged={refetch}
        onNotice={setNotice}
      />
      <Replica
        replica={replica.data?.replica || []}
        panel={panel.data}
        invalidations={invalidations.data?.invalidations || []}
      />
      <Dataverse
        tables={tables.data?.tables || []}
        vocabulary={vocabulary.data}
        onChanged={refetch}
        onNotice={setNotice}
      />
      <HubSpot
        subscriptions={hubspotSubscriptions.data?.subscriptions || []}
        usage={hubspotUsage.data}
        onChanged={refetch}
        onNotice={setNotice}
      />
      <Judgements inferences={inferences.data} />
      {panelError ? <ErrorNote error={panelError} onRetry={refetch} /> : null}
    </div>
  )
}
