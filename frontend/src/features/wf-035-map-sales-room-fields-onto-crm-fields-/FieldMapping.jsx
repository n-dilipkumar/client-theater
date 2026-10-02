import { useCallback, useMemo, useState } from 'react'

import { Badge, Button, Card, EmptyState, ErrorNote, Field, Spinner, StatCard, inputClass, useAsync } from '@/components/ui'

import { detailOf, mappingApi, shortMoment, toneFor } from './api'
import { BadgeRow, Glyph, Notice } from './primitives'

/**
 * WF-035: the field-mapping grid.
 *
 * The page follows the researched flow in order, because that is the order the
 * dependencies run in: pick a connection, pick the CRM object, map each sales-room
 * field to a property with a direction and a named transform, pin the sync key, then
 * validate. Validation is the step that reads the CRM's own metadata, so everything
 * before it is setup and everything after it is a consequence.
 *
 * Two things the page refuses to hide:
 *
 * 1. **A finding's message is always rendered**, not only its badge. A row that says
 *    `unsupported_option` and nothing else is a row an admin has to go and read the
 *    API docs to act on. The message is a sentence; the badge is the handle.
 * 2. **The sourced/unsourced line on the sync-key plan is prominent.** A plan marked
 *    `sourced: false` is this build's reading, not the vendor's documentation, and a
 *    plan that looks like the other one invites someone to send it.
 */

const DIRECTION_LABELS = { in: 'CRM into the room', out: 'Room into the CRM', both: 'Both ways' }

const PROVIDER_LABELS = { hubspot: 'HubSpot', dataverse: 'Dataverse', salesforce: 'Salesforce' }

/* ------------------------------------------------------------------ the header */

function Tiles({ summary }) {
  const errors = (summary.validations || 0) > 0 ? 'Last validation is stored' : 'No validation run yet'
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard
        label="Mappings"
        value={summary.mappings}
        hint={`${summary.connections} connection${summary.connections === 1 ? '' : 's'}`}
        icon="schema"
      />
      <StatCard
        label="Live"
        value={summary.active}
        hint={`${summary.draft} still in draft`}
        icon="database"
      />
      <StatCard
        label="Grid rows"
        value={summary.rows}
        hint={`${summary.sync_keys_pinned} sync key${summary.sync_keys_pinned === 1 ? '' : 's'} pinned`}
        icon="audit"
      />
      <StatCard
        label="Key ceiling"
        value={summary.unique_key_limit}
        hint={errors}
        icon="search"
      />
    </div>
  )
}

/* ------------------------------------------------------------------ connections */

function ConnectionList({ connections, selectedId, onSelect, busy }) {
  if (!connections.length) {
    return (
      <Card>
        <EmptyState
          title="No CRM connection yet"
          description="Field mapping is per connection, the way the research stores it. A connection the OAuth workflow already recorded works too — pass its id straight to a mapping."
          action={
            <Button
              variant="primary"
              icon="plus"
              disabled={busy}
              onClick={() => onSelect('__new__')}
            >
              Register a connection
            </Button>
          }
        />
      </Card>
    )
  }
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-foreground">Connections</h2>
        <Button icon="plus" disabled={busy} onClick={() => onSelect('__new__')}>
          Add
        </Button>
      </div>
      <ul className="mt-3 space-y-2">
        {connections.map((connection) => (
          <li key={connection.id}>
            <button
              type="button"
              onClick={() => onSelect(connection.id)}
              aria-current={connection.id === selectedId ? 'true' : undefined}
              className={`w-full cursor-pointer rounded-lg border p-3 text-left transition-colors duration-200 ${
                connection.id === selectedId
                  ? 'border-accent/50 bg-accent/10'
                  : 'border-border-subtle/40 hover:bg-muted'
              }`}
            >
              <p className="text-sm font-medium text-foreground">{connection.name}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {PROVIDER_LABELS[connection.provider] || connection.provider}
                {connection.account ? ` · ${connection.account}` : ''}
                {connection.room_id ? '' : ' · every room'}
              </p>
              <p className="mt-1 font-mono text-xs text-muted-foreground/80">
                {connection.mapping_count} mapping{connection.mapping_count === 1 ? '' : 's'}
                {connection.connected ? '' : ' · not connected'}
              </p>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/* ------------------------------------------------------------------ new connection */

const NEW_CONNECTION = { name: '', provider: 'hubspot', account: '', property_group: '' }

function NewConnection({ providers, onCreate, onCancel, busy }) {
  const [form, setForm] = useState(NEW_CONNECTION)
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })
  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">Register a connection</h2>
      <p className="mt-0.5 text-xs text-muted-foreground">
        A reference for mapping. The OAuth exchange and the token vault are WF-034&rsquo;s; a
        deployment that already has connections can use theirs without registering anything here.
      </p>
      <div className="mt-3 space-y-3">
        <Field label="Name" id="wf035-conn-name" hint="What a reader calls this connection.">
          <input id="wf035-conn-name" className={inputClass} value={form.name} onChange={set('name')} />
        </Field>
        <Field label="Provider" id="wf035-conn-provider">
          <select id="wf035-conn-provider" className={inputClass} value={form.provider} onChange={set('provider')}>
            {providers.map((provider) => (
              <option key={provider} value={provider}>
                {PROVIDER_LABELS[provider] || provider}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Account" id="wf035-conn-account" hint="Optional. The vendor org or portal.">
          <input id="wf035-conn-account" className={inputClass} value={form.account} onChange={set('account')} />
        </Field>
        {form.provider === 'hubspot' && (
          <Field
            label="Property group"
            id="wf035-conn-group"
            hint="Required in a HubSpot property create. Left empty, the sync-key request is refused rather than guessed."
          >
            <input
              id="wf035-conn-group"
              className={inputClass}
              value={form.property_group}
              onChange={set('property_group')}
              placeholder="contactinformation"
            />
          </Field>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={busy || !form.name.trim()}
            onClick={() => onCreate({ ...form, name: form.name.trim() })}
          >
            Create
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ the grid */

const EMPTY_ROW = { source_field: '', source_type: 'text', target_property: '', direction: 'out', transform: 'identity' }

function GridRow({ row, vocabulary, properties, onSave, onDelete, busy }) {
  // The editable form is derived from the row it was opened for. `edits` keeps
  // the operator's own values keyed by the row they belong to, so a different
  // `row` (a refetch, or another row rendered into this component) derives a
  // fresh form during render rather than one render late via an effect.
  const [edits, setEdits] = useState({ forRow: null, form: null })
  const [open, setOpen] = useState(false)

  const form = edits.forRow === row ? edits.form : row

  const setForm = (next) =>
    setEdits({ forRow: row, form: typeof next === 'function' ? next(form) : next })

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })
  const findings = row.findings || []
  const optionValues = properties.find((prop) => prop.name === form.target_property)?.options || []

  return (
    <tr className="border-t border-border-subtle/30 align-top">
      <td className="py-2 pr-3">
        <p className="font-mono text-[13px] text-foreground">{row.source_field}</p>
        <p className="text-xs text-muted-foreground">{row.source_type}</p>
      </td>
      <td className="py-2 pr-3">
        {open ? (
          <select
            className={inputClass}
            value={form.target_property}
            onChange={set('target_property')}
            aria-label={`CRM property for ${row.source_field}`}
          >
            <option value="">Not mapped</option>
            {properties.map((prop) => (
              <option key={prop.name} value={prop.name}>
                {prop.name} ({prop.value_type})
              </option>
            ))}
          </select>
        ) : (
          <p className="font-mono text-[13px] text-foreground">
            {row.target_property || <span className="text-muted-foreground">not mapped</span>}
          </p>
        )}
        {findings.length > 0 && (
          <div className="mt-1 space-y-1">
            {findings.map((finding) => (
              <BadgeRow key={`${row.row_id}-${finding.flag}`} finding={{ ...finding, tone: toneFor(finding.severity) }} />
            ))}
          </div>
        )}
      </td>
      <td className="py-2 pr-3">
        {open ? (
          <select
            className={inputClass}
            value={form.direction}
            onChange={set('direction')}
            aria-label={`Direction for ${row.source_field}`}
          >
            {vocabulary.directions.map((direction) => (
              <option key={direction} value={direction}>
                {DIRECTION_LABELS[direction] || direction}
              </option>
            ))}
          </select>
        ) : (
          <Badge tone={row.direction === 'in' ? 'update' : 'neutral'}>{row.direction}</Badge>
        )}
      </td>
      <td className="py-2 pr-3">
        {open ? (
          <select
            className={inputClass}
            value={form.transform}
            onChange={set('transform')}
            aria-label={`Transform for ${row.source_field}`}
          >
            {vocabulary.transforms.map((transform) => (
              <option key={transform.key} value={transform.name}>
                {transform.name}@{transform.version}
              </option>
            ))}
          </select>
        ) : (
          <p className="font-mono text-[13px] text-foreground">
            {row.transform}
            {row.transform_version ? `@${row.transform_version}` : ''}
          </p>
        )}
        {row.transform === 'picklist.map' && optionValues.length > 0 && (
          <p className="text-xs text-muted-foreground">
            internal: {optionValues.map((option) => option.value).join(', ')}
          </p>
        )}
      </td>
      <td className="py-2 text-right">
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            icon={open ? 'close' : 'schema'}
            onClick={() => {
              if (open) onSave(row.id, form)
              setOpen(!open)
            }}
            disabled={busy}
          >
            {open ? 'Save' : 'Edit'}
          </Button>
          <Button variant="danger" icon="trash" disabled={busy} onClick={() => onDelete(row.id)}>
            Remove
          </Button>
        </div>
      </td>
    </tr>
  )
}

function NewRowForm({ vocabulary, properties, onCreate, busy }) {
  const [form, setForm] = useState(EMPTY_ROW)
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })
  return (
    <div className="mt-3 space-y-2 rounded-lg border border-dashed border-border-subtle/50 p-3">
      <p className="text-xs font-medium text-muted-foreground">Add a row to the grid</p>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <input
          className={inputClass}
          placeholder="Sales-room field"
          value={form.source_field}
          onChange={set('source_field')}
          aria-label="Sales-room field"
        />
        <select className={inputClass} value={form.source_type} onChange={set('source_type')} aria-label="Field type">
          {vocabulary.room_field_types.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <select
          className={inputClass}
          value={form.target_property}
          onChange={set('target_property')}
          aria-label="CRM property"
        >
          <option value="">Not mapped</option>
          {properties.map((prop) => (
            <option key={prop.name} value={prop.name}>
              {prop.name} ({prop.value_type})
            </option>
          ))}
        </select>
        <select className={inputClass} value={form.direction} onChange={set('direction')} aria-label="Direction">
          {vocabulary.directions.map((direction) => (
            <option key={direction} value={direction}>
              {DIRECTION_LABELS[direction] || direction}
            </option>
          ))}
        </select>
        <select className={inputClass} value={form.transform} onChange={set('transform')} aria-label="Transform">
          {vocabulary.transforms.map((transform) => (
            <option key={transform.key} value={transform.name}>
              {transform.name}@{transform.version}
            </option>
          ))}
        </select>
      </div>
      <Button
        variant="primary"
        icon="plus"
        disabled={busy || !form.source_field.trim()}
        onClick={() => {
          onCreate({ ...form, source_field: form.source_field.trim() })
          setForm(EMPTY_ROW)
        }}
      >
        Add row
      </Button>
    </div>
  )
}

/* ------------------------------------------------------------------ the sync key */

function SyncKeyPanel({ keyState, onPin, onUnpin, busy, hasProperties }) {
  const [property, setProperty] = useState('')
  const pinned = keyState?.pinned
  const usage = keyState?.usage || {}
  const section = keyState?.key_section

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Glyph name="key" />
          Sync key
        </h2>
        {pinned && (
          <Button variant="ghost" icon="close" disabled={busy} onClick={onUnpin}>
            Unpin
          </Button>
        )}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        The CRM property that carries the sales room&rsquo;s own row id, marked unique so the CRM
        itself rejects a collision.
      </p>

      {!pinned ? (
        <div className="mt-3 space-y-2">
          <Field
            label="Property"
            id="wf035-key-property"
            hint="A HubSpot key is one property. A Dataverse alternate key may name several, and the vendor's eligible attribute types are the only ones accepted."
          >
            <input
              id="wf035-key-property"
              className={inputClass}
              value={property}
              onChange={(event) => setProperty(event.target.value)}
              placeholder="dsr_row_id"
            />
          </Field>
          <Button
            variant="primary"
            icon="plus"
            disabled={busy || !hasProperties || !property.trim()}
            onClick={() => onPin({ properties: property.split(',').map((part) => part.trim()).filter(Boolean) })}
          >
            Pin the key
          </Button>
          {!hasProperties && (
            <p className="text-xs text-amber-200">
              No property metadata recorded for this object, so there is nothing to check the key
              against yet.
            </p>
          )}
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-2">
            {keyState.sync_key.properties.map((name) => (
              <Badge key={name} tone="insert">
                {name}
              </Badge>
            ))}
            {keyState.sync_key.unique && <Badge tone="neutral">unique</Badge>}
          </div>
          {usage.limit && (
            <p className="text-xs text-muted-foreground">
              {usage.used} of {usage.limit} unique keys in use, {usage.remaining} left after this one.
              Pinned {shortMoment(keyState.sync_key.pinned_at)}.
            </p>
          )}
          {section && section.findings.length > 0 && (
            <div className="space-y-1">
              {section.findings.map((finding) => (
                <BadgeRow key={finding.flag} finding={{ ...finding, tone: toneFor(finding.severity) }} />
              ))}
            </div>
          )}
        </div>
      )}

      {keyState?.plan_error && (
        <div className="mt-3">
          <Notice tone="warn" title="No create request for this provider">
            <p>{keyState.plan_error}</p>
            {keyState.plan_gap && <p className="mt-1 italic">{keyState.plan_gap}</p>}
          </Notice>
        </div>
      )}

      {keyState?.plan && (
        <div className="mt-3 space-y-2">
          <Notice
            tone={keyState.plan.sourced ? 'good' : 'warn'}
            title={keyState.plan.sourced ? 'Sourced create request' : 'Un-sourced request plan'}
          >
            <p>{keyState.plan.note}</p>
            {keyState.plan.gap && <p className="mt-1 italic">{keyState.plan.gap}</p>}
          </Notice>
          {keyState.plan.steps.map((step) => (
            <div key={step.step} className="rounded-lg border border-border-subtle/40 p-3">
              <p className="font-mono text-xs text-muted-foreground">
                {step.method} {step.url || '(URL not cited)'}
              </p>
              {step.url_note && <p className="mt-1 text-xs text-amber-200">{step.url_note}</p>}
              <pre className="mt-2 overflow-x-auto font-mono text-xs text-foreground">
                {JSON.stringify(step.body, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------------ the preview */

const SAMPLE_RECORD = {
  id: 'engagement_8f2c',
  primary_contact_email: '  Ada.Lovelace@Example.COM ',
  buyer_stage: 'Discovery',
  seats: '40',
  renewal_date: '2026-11-15T09:00:00Z',
}

function PreviewPanel({ properties, onPreview }) {
  const [record, setRecord] = useState(JSON.stringify(SAMPLE_RECORD, null, 2))
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const parsed = JSON.parse(record)
      setResult(await onPreview(parsed))
    } catch (exc) {
      setError(exc?.detail || exc?.message || String(exc))
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">What a sync cycle would send</h2>
      <p className="mt-0.5 text-xs text-muted-foreground">
        The research says no automation is required at write time: the mapping is evaluated per
        record on every cycle. This is that evaluation against one record, and it writes nothing.
      </p>
      <textarea
        className={`${inputClass} mt-3 h-40 font-mono text-xs`}
        value={record}
        onChange={(event) => setRecord(event.target.value)}
        aria-label="The sales-room record to evaluate the mapping against"
        spellCheck={false}
      />
      <div className="mt-2 flex items-center gap-2">
        <Button icon="refresh" disabled={busy || !properties.length} onClick={run}>
          Evaluate
        </Button>
        {!properties.length && (
          <span className="text-xs text-muted-foreground">
            Record the property metadata first: the evaluator checks values against the CRM&rsquo;s
            own option set.
          </span>
        )}
      </div>
      {error && (
        <div className="mt-3">
          <Notice tone="bad" title="That record could not be evaluated">
            <p>{error}</p>
          </Notice>
        </div>
      )}
      {result && (
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <div>
            <p className="text-xs font-medium text-muted-foreground">Sent to the CRM</p>
            <pre className="mt-1 overflow-x-auto rounded-lg bg-muted/40 p-2 font-mono text-xs text-foreground">
              {JSON.stringify(result.out, null, 2)}
            </pre>
            {result.sync_key && (
              <p className="mt-1 text-xs text-muted-foreground">
                Key {result.sync_key.property} = {String(result.sync_key.value)} (
                {result.sync_key.injected ? 'injected' : 'from a mapped row'})
              </p>
            )}
          </div>
          <div>
            <p className="text-xs font-medium text-muted-foreground">Read back into the room</p>
            <pre className="mt-1 overflow-x-auto rounded-lg bg-muted/40 p-2 font-mono text-xs text-foreground">
              {JSON.stringify(result.in, null, 2)}
            </pre>
          </div>
          {result.trace.some((item) => item.status !== 'ok') && (
            <div className="lg:col-span-2">
              <Notice
                tone={result.writable ? 'warn' : 'bad'}
                title={result.writable ? 'Some rows were skipped' : 'This record would be refused'}
              >
                <ul className="mt-1 space-y-0.5">
                  {result.trace
                    .filter((item) => item.status !== 'ok')
                    .map((item) => (
                      <li key={`${item.row_id}-${item.direction}-${item.reason}`}>
                        <span className="font-mono">{item.source_field}</span> ({item.direction}): {item.reason}
                        {item.detail?.error ? ` — ${item.detail.error}` : ''}
                      </li>
                    ))}
                </ul>
              </Notice>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------------ inferences */

function Inferences({ inferences }) {
  if (!inferences) return null
  return (
    <Card>
      <h2 className="text-sm font-semibold text-foreground">
        What the research does not decide ({inferences.count})
      </h2>
      <p className="mt-0.5 text-xs text-muted-foreground">
        Every judgement call this workflow makes, and what would change it. A decision a reviewer
        cannot find is a decision a reviewer cannot disagree with.
      </p>
      <div className="mt-3 space-y-2">
        {inferences.inferences.map((entry) => (
          <details key={entry.id} className="rounded-lg border border-border-subtle/40 p-3">
            <summary className="cursor-pointer text-sm font-medium text-foreground">{entry.question}</summary>
            <p className="mt-2 text-xs text-muted-foreground">
              <span className="font-semibold text-foreground">Taken:</span> {entry.decision}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              <span className="font-semibold text-foreground">Why:</span> {entry.why}
            </p>
            <p className="mt-1 font-mono text-xs text-muted-foreground/80">{entry.change_it}</p>
          </details>
        ))}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ the page */

export default function FieldMapping() {
  const [connectionId, setConnectionId] = useState('')
  const [mappingId, setMappingId] = useState('')
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)

  const vocabulary = useAsync(() => mappingApi.vocabulary(), [])
  const summary = useAsync(() => mappingApi.summary(), [])
  const connections = useAsync(() => mappingApi.connections(), [])
  const inferences = useAsync(() => mappingApi.inferences(), [])
  const transforms = useAsync(() => mappingApi.transforms(), [])

  const mappings = useAsync(
    () => (connectionId ? mappingApi.mappings(connectionId) : Promise.resolve(null)),
    [connectionId],
  )
  const mapping = useAsync(
    () => (connectionId && mappingId ? mappingApi.readMapping(connectionId, mappingId) : Promise.resolve(null)),
    [connectionId, mappingId],
  )
  const keyState = useAsync(
    () => (connectionId && mappingId ? mappingApi.syncKey(connectionId, mappingId) : Promise.resolve(null)),
    [connectionId, mappingId],
  )
  const validation = useAsync(
    () => (connectionId && mappingId ? mappingApi.validation(connectionId, mappingId) : Promise.resolve(null)),
    [connectionId, mappingId],
  )

  const connection = useMemo(
    () => (connections.data?.connections || []).find((item) => item.id === connectionId) || null,
    [connections.data, connectionId],
  )

  /**
   * The CRM's own property list, for the grid's pickers.
   *
   * Read from its own route rather than carried on the mapping view, because the
   * pickers need every name the object has - including the ones this mapping does
   * not use - and a list view would then have to fetch metadata per mapping. A 409
   * here is the researched precondition rather than a failure: no read is recorded,
   * and the endpoints a connector should call come from the vocabulary the page
   * already holds.
   */
  const crmObject = mapping.data?.crm_object || ''
  const propertiesRead = useAsync(
    () => (connectionId && crmObject ? mappingApi.properties(connectionId, crmObject) : Promise.resolve(null)),
    [connectionId, crmObject],
  )
  const gridProperties = useMemo(
    () =>
      (propertiesRead.data?.properties || []).map((prop) => ({
        name: prop.name,
        value_type: prop.value_type,
        options: prop.options,
      })),
    [propertiesRead.data],
  )
  const noMetadata = propertiesRead.error?.status === 409
  const endpoints = connection ? vocabulary.data.metadata_endpoints[connection.provider] || [] : []
  const hasProperties = gridProperties.length > 0

  const refresh = useCallback(() => {
    connections.refetch()
    summary.refetch()
    if (connectionId) {
      mappings.refetch()
      if (mappingId) {
        mapping.refetch()
        keyState.refetch()
        validation.refetch()
      }
    }
  }, [connectionId, mappingId, connections, summary, mappings, mapping, keyState, validation])

  const act = useCallback(
    async (label, run) => {
      setBusy(true)
      setFlash(null)
      try {
        await run()
        setFlash({ tone: 'good', title: label })
        refresh()
      } catch (exc) {
        setFlash({ tone: 'bad', title: label, body: detailOf(exc) })
      } finally {
        setBusy(false)
      }
    },
    [refresh],
  )

  if (vocabulary.loading || summary.loading) return <Spinner label="Loading field mapping" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (summary.error) return <ErrorNote error={summary.error} onRetry={summary.refetch} />

  const data = mapping.data
  const report = validation.data?.validation || null
  const rows = report?.rows || []
  const counts = report?.counts || { ok: 0, warning: 0, error: 0 }
  const canActivate = Boolean(validation.data?.can_activate) && data?.state !== 'active'

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-foreground">Field mapping</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Map each sales-room field onto a CRM property with a direction and a named transform,
            pin the sync key, and validate the whole thing against the CRM&rsquo;s own property
            metadata before any data is written.
          </p>
        </div>
        <Button icon="refresh" onClick={refresh}>
          Refresh
        </Button>
      </header>

      <Tiles summary={summary.data} />

      {summary.data.objects_without_metadata?.length > 0 && (
        <Notice
          tone="warn"
          title={`${summary.data.objects_without_metadata.length} mapping(s) have no property metadata`}
        >
          <p>
            Validation needs the CRM&rsquo;s own view of its properties. Read them through the
            connector and post the document; the endpoints are listed per connection below.
          </p>
        </Notice>
      )}

      {flash && (
        <Notice tone={flash.tone} title={flash.title}>
          {flash.body ? <p>{flash.body}</p> : <p>Done.</p>}
        </Notice>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <ConnectionList
            connections={connections.data?.connections || []}
            selectedId={connectionId}
            busy={busy}
            onSelect={(id) => {
              setMappingId('')
              setConnectionId(id === '__new__' ? '' : id)
            }}
          />
          {connectionId === '' && (connections.data?.connections || []).length > 0 && (
            <NewConnection
              providers={vocabulary.data.providers}
              busy={busy}
              onCancel={() => setConnectionId((connections.data?.connections || [])[0]?.id || '')}
              onCreate={(payload) =>
                act('Connection registered', async () => {
                  const created = await mappingApi.createConnection(payload)
                  setConnectionId(created.connection.id)
                })
              }
            />
          )}
          <Inferences inferences={inferences.data} />
        </div>

        <div className="space-y-4">
          {!connectionId && (
            <Card>
              <EmptyState
                title="Pick a connection"
                description="Field mapping hangs off a connection, the way the research stores it. Choose one on the left to see its mappings."
              />
            </Card>
          )}

          {connectionId && mappings.loading && <Spinner label="Loading mappings" />}
          {connectionId && mappings.error && (
            <ErrorNote error={mappings.error} onRetry={mappings.refetch} />
          )}

          {connectionId && mappings.data && (
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-foreground">
                  Mappings on {connection?.name || 'this connection'}
                </h2>
                <NewMappingForm
                  onCreate={(payload) =>
                    act('Mapping created', async () => {
                      const created = await mappingApi.createMapping(connectionId, payload)
                      setMappingId(created.id)
                    })
                  }
                  busy={busy}
                />
              </div>
              {mappings.data.count === 0 ? (
                <p className="mt-3 text-sm text-muted-foreground">
                  No mapping yet. One mapping is for one CRM object.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {mappings.data.mappings.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => setMappingId(item.id)}
                        aria-current={item.id === mappingId ? 'true' : undefined}
                        className={`w-full cursor-pointer rounded-lg border p-3 text-left transition-colors duration-200 ${
                          item.id === mappingId
                            ? 'border-accent/50 bg-accent/10'
                            : 'border-border-subtle/40 hover:bg-muted'
                        }`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="font-mono text-[13px] text-foreground">{item.crm_object}</p>
                          <Badge tone={item.state === 'active' ? 'insert' : 'neutral'}>{item.state}</Badge>
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {item.row_count} row{item.row_count === 1 ? '' : 's'} ·{' '}
                          {item.sync_key?.properties?.length
                            ? `key ${item.sync_key.properties.join(', ')}`
                            : 'no sync key'}
                          {item.validation
                            ? ` · ${item.validation.counts?.error || 0} error(s)`
                            : ' · not validated'}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}

          {mappingId && mapping.loading && <Spinner label="Loading the mapping" />}
          {mappingId && mapping.error && <ErrorNote error={mapping.error} onRetry={mapping.refetch} />}

          {mappingId && data && (
            <>
              <Card>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="text-sm font-semibold text-foreground">
                      {data.crm_object_label || data.crm_object}
                    </h2>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {PROVIDER_LABELS[data.provider] || data.provider} · {data.state} ·{' '}
                      {data.row_count} row{data.row_count === 1 ? '' : 's'}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      icon="audit"
                      disabled={busy}
                      onClick={() =>
                        act('Validated', () => mappingApi.validate(connectionId, mappingId, { record: true }))
                      }
                    >
                      Validate mapping
                    </Button>
                    {data.state === 'active' ? (
                      <Button
                        variant="ghost"
                        disabled={busy}
                        onClick={() => act('Deactivated', () => mappingApi.deactivate(connectionId, mappingId))}
                      >
                        Deactivate
                      </Button>
                    ) : (
                      <Button
                        variant="primary"
                        icon="check"
                        disabled={busy || !canActivate}
                        title={
                          canActivate
                            ? 'Make this mapping live'
                            : 'Activation needs a stored validation with no error findings'
                        }
                        onClick={() => act('Activated', () => mappingApi.activate(connectionId, mappingId))}
                      >
                        Activate
                      </Button>
                    )}
                  </div>
                </div>

                {!data.metadata.recorded && (
                  <div className="mt-3 space-y-2">
                    <Notice tone="warn" title="No property metadata recorded for this object">
                      <p>
                        Validation reads the CRM&rsquo;s own property and type metadata, so until a
                        read is recorded every target name here is unverified. The connector calls
                        one of these:
                      </p>
                      <ul className="mt-1 space-y-0.5 font-mono text-xs">
                        {endpoints.map((entry) => (
                          <li key={entry.url}>
                            {entry.method} {entry.url}
                          </li>
                        ))}
                        {endpoints.length === 0 && (
                          <li className="font-sans text-muted-foreground">
                            No documented read for this provider: the research records
                            Salesforce&rsquo;s field pages as client-rendered and unreadable.
                          </li>
                        )}
                      </ul>
                    </Notice>
                    {noMetadata && (
                      <p className="font-mono text-xs text-muted-foreground/80">
                        POST /wf-035/connections/{connectionId}/properties
                      </p>
                    )}
                  </div>
                )}
                {data.metadata.recorded && (
                  <p className="mt-3 text-xs text-muted-foreground">
                    Validated against {data.metadata.property_count} properties read from{' '}
                    <span className="font-mono">{data.metadata.document}</span>, fetched{' '}
                    {shortMoment(data.metadata.fetched_at)}.
                  </p>
                )}
                {validation.data?.reason && (
                  <p className="mt-2 text-xs text-amber-200">{validation.data.reason}</p>
                )}
                {report && (
                  <p className="mt-2 font-mono text-xs text-muted-foreground">
                    {counts.ok} ok · {counts.warning} warning · {counts.error} error
                    {report.blocking?.length ? ` · blocking: ${report.blocking.join(', ')}` : ''}
                  </p>
                )}
              </Card>

              <Card>
                <h2 className="text-sm font-semibold text-foreground">The grid</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  One row per sales-room field. A field with no target is a warning, not an error:
                  most of a field dictionary is never mapped.
                </p>
                {rows.length === 0 ? (
                  <p className="mt-3 text-sm text-muted-foreground">
                    {hasProperties
                      ? 'No rows yet, so there is nothing to validate.'
                      : 'Record the property metadata, then add rows.'}
                  </p>
                ) : (
                  <div className="mt-3 overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="text-xs uppercase tracking-wide text-muted-foreground">
                          <th className="py-1 pr-3 font-medium">Room field</th>
                          <th className="py-1 pr-3 font-medium">CRM property</th>
                          <th className="py-1 pr-3 font-medium">Direction</th>
                          <th className="py-1 pr-3 font-medium">Transform</th>
                          <th className="py-1 text-right font-medium">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row) => (
                          <GridRow
                            key={row.row_id || row.source_field}
                            row={row}
                            vocabulary={vocabulary.data}
                            properties={gridProperties}
                            busy={busy}
                            onSave={(rowId, form) =>
                              act('Row saved', () => mappingApi.patchRow(connectionId, mappingId, rowId, form))
                            }
                            onDelete={(rowId) =>
                              act('Row removed', () => mappingApi.deleteRow(connectionId, mappingId, rowId))
                            }
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <NewRowForm
                  vocabulary={vocabulary.data}
                  properties={gridProperties}
                  busy={busy}
                  onCreate={(payload) => act('Row added', () => mappingApi.putRow(connectionId, mappingId, payload))}
                />
              </Card>

              <SyncKeyPanel
                keyState={keyState.data}
                busy={busy}
                hasProperties={hasProperties}
                onPin={(payload) => act('Sync key pinned', () => mappingApi.pinSyncKey(connectionId, mappingId, payload))}
                onUnpin={() => act('Sync key unpinned', () => mappingApi.unpinSyncKey(connectionId, mappingId))}
              />

              <PreviewPanel
                properties={gridProperties}
                onPreview={(record) => mappingApi.preview(connectionId, mappingId, record)}
              />

              {transforms.data?.declared?.length > 0 && (
                <Card>
                  <h2 className="text-sm font-semibold text-foreground">Declared transforms</h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Declared as data, so a deployment can write down the transform it is adding. A
                    row naming one gets a <span className="font-mono">transform_unavailable</span>{' '}
                    badge until the code is registered in the connector.
                  </p>
                  <ul className="mt-2 space-y-1">
                    {transforms.data.declared.map((entry) => (
                      <li key={entry.id} className="font-mono text-xs text-muted-foreground">
                        {entry.key} — {entry.description || 'no description'}{' '}
                        {entry.executable ? '' : '(not executable yet)'}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- new mapping form */

function NewMappingForm({ onCreate, busy }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ crm_object: '', apply_defaults: false })
  if (!open) {
    return (
      <Button icon="plus" disabled={busy} onClick={() => setOpen(true)}>
        Add mapping
      </Button>
    )
  }
  return (
    <div className="flex flex-wrap items-end gap-2">
      <Field label="CRM object" id="wf035-mapping-object">
        <input
          id="wf035-mapping-object"
          className={inputClass}
          value={form.crm_object}
          onChange={(event) => setForm({ ...form, crm_object: event.target.value })}
          placeholder="contacts"
        />
      </Field>
      <label className="flex min-h-11 items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          className="h-4 w-4"
          checked={form.apply_defaults}
          onChange={(event) => setForm({ ...form, apply_defaults: event.target.checked })}
        />
        Apply the vendor default
      </label>
      <Button
        variant="primary"
        disabled={busy || !form.crm_object.trim()}
        onClick={() => onCreate({ ...form, crm_object: form.crm_object.trim() })}
      >
        Create
      </Button>
      <Button variant="ghost" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </div>
  )
}
