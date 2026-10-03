/**
 * WF-044: a CRM-side webhook, aimed at one room.
 *
 * The page follows the researched flow in the order the flow implies, and every
 * panel exists to answer one question a rep actually has:
 *
 *   1. **Endpoint** - the room's *Webhook settings*: the URL to paste into
 *      *Data ops -> Send a webhook*, the method, which of the three researched
 *      authentication types to use, and the two researched body modes. The secret
 *      is shown because the research says the page shows it, and because it is one
 *      secret for the whole endpoint: *"it does not need a per-workflow secret"*,
 *      so any number of CRM-side automations can aim at this one URL.
 *   2. **Automations** - step 2 of the flow, the start conditions. Unbounded here,
 *      and bounded by the vendor's own 1,000 per app, which is reported beside the
 *      count rather than discovered at the limit.
 *   3. **Deliveries** - what arrived, what it did, and what it changed. Refused
 *      deliveries are in this list by default, because "my automation reached the
 *      room and nothing happened" is answered here and not in a support ticket.
 *   4. **Deal panel** - what the room now believes about each deal, and which
 *      properties it is holding but no longer being told about.
 *   5. **Rep** - the notices, and the button that deals with them.
 *   6. **Decisions** - every judgement call this build made, with the reason.
 *
 * Two things this page refuses to imply. It does not offer a "test" button,
 * because the researched **Test** control is a CRM-side control - the room shows
 * the bytes it expects instead, and says so. And it does not show a stage as
 * current unless a delivery actually said so: a property the room is holding but
 * the CRM has stopped sending is labelled stale rather than quietly believed.
 *
 * Nothing here reaches upwards with a relative path and nothing was added to a
 * shared file: the API calls are in `./api.js` and the small pieces are in
 * `./primitives.jsx`.
 */

import { useEffect, useState } from 'react'
import { relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import { authRule, notifiedByEffect, objectLabel, reasonText, webhookApi } from './api'
import { CopyField, Facts, Glyph, ResearchNote, WEBHOOK_ICON } from './primitives'

const TABS = [
  { id: 'endpoint', label: 'Endpoint' },
  { id: 'automations', label: 'Automations' },
  { id: 'deliveries', label: 'Deliveries' },
  { id: 'deals', label: 'Deal panel' },
  { id: 'notices', label: 'Rep' },
  { id: 'decisions', label: 'Decisions' },
]

/**
 * What the operator holds in the CRM.
 *
 * This product has no login of its own, so the researched permission rule - *"To
 * set up webhook actions in workflows, users must have Edit permissions for
 * workflows or Super Admin permissions. To publish workflows, users must have
 * Publish permissions for workflows."* - cannot be answered from a session here.
 * The page asks, the server enforces, and choosing the wrong one produces the
 * researched 403 rather than a disabled button that would hide the rule.
 */
const PERMISSION_CHOICES = [
  { id: 'edit_workflows', label: 'Edit permissions for workflows' },
  { id: 'publish_workflows', label: 'Publish permissions for workflows' },
  { id: 'edit_workflows,publish_workflows', label: 'Edit and Publish (both)' },
  { id: 'super_admin', label: 'Super Admin' },
]

const OUTCOME_TONE = { accepted: 'insert', duplicate: 'update', refused: 'delete' }
const EFFECT_TONE = {
  stage_changed: 'insert',
  stage_unchanged: 'neutral',
  properties_only: 'update',
  none: 'neutral',
}

function TabBar({ current, onChange }) {
  return (
    <div role="tablist" aria-label="CRM webhook sections" className="flex flex-wrap gap-1.5">
      {TABS.map((tab) => {
        const selected = tab.id === current
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`wf044-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`wf044-panel-${tab.id}`}
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

/** One served value, printed as a monospace fact rather than a paragraph. */
function Code({ children }) {
  return <span className="font-mono text-[13px] text-foreground">{children}</span>
}

// --------------------------------------------------------------------------- //
// The endpoint: steps 1 to 4, and 5
// --------------------------------------------------------------------------- //

function EndpointForm({ vocabulary, permissions, onDone, onError }) {
  const [form, setForm] = useState({
    url: '',
    method: 'POST',
    object: 'deals',
    auth_mode: 'signature',
    app_id: '',
    secret: '',
    api_key_name: 'api_key',
    api_key_location: 'header',
    body_mode: 'include_all',
    id_key: '',
    stage_key: '',
  })

  const set = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))

  async function submit(event) {
    event.preventDefault()
    const auth = { mode: form.auth_mode, secret: form.secret }
    if (form.auth_mode === 'signature') auth.app_id = form.app_id
    if (form.auth_mode === 'api_key') {
      auth.name = form.api_key_name
      auth.location = form.api_key_location
    }
    const payload = {
      url: form.url,
      method: form.method,
      object: form.object,
      auth,
      body: { mode: form.body_mode, keys: [] },
    }
    if (form.id_key) payload.id_key = form.id_key
    if (form.stage_key) payload.stage_key = form.stage_key
    try {
      await onDone(payload)
    } catch (error) {
      onError(error)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        In the CRM: <Code>Automation -&gt; Workflows -&gt; edit workflow -&gt; + -&gt; Data ops -&gt; Send a
        webhook</Code>. Paste the room's URL below and the authentication the panel offers.
      </p>

      <Field
        label="Endpoint URL"
        hint={`${vocabulary?.url?.rule || 'Webhook URLs are restricted to a secure protocol and must begin with HTTPS.'}`}
      >
        <input
          className={inputClass}
          value={form.url}
          onChange={set('url')}
          placeholder="https://rooms.example.com/api/wf-044/rooms/.../crm/v1/webhook"
          required
        />
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Method" hint={vocabulary?.methods?.find((m) => m.name === form.method)?.note}>
          <select className={inputClass} value={form.method} onChange={set('method')}>
            {(vocabulary?.methods || []).map((row) => (
              <option key={row.name} value={row.name}>
                {row.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Object" hint="The object the **Send a webhook** panel is pointed at.">
          <select className={inputClass} value={form.object} onChange={set('object')}>
            {(vocabulary?.objects || []).map((row) => (
              <option key={row.name} value={row.name}>
                {row.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field
        label="Authentication type"
        hint={authRule(vocabulary, form.auth_mode)}
      >
        <select className={inputClass} value={form.auth_mode} onChange={set('auth_mode')}>
          {(vocabulary?.auth_modes || []).map((row) => (
            <option key={row.mode} value={row.mode}>
              {row.label}
            </option>
          ))}
        </select>
      </Field>

      {form.auth_mode === 'signature' && (
        <Field label="HubSpot App ID" hint="The panel asks for it before the room can verify a signature.">
          <input className={inputClass} value={form.app_id} onChange={set('app_id')} required />
        </Field>
      )}

      {form.auth_mode === 'api_key' && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="API key name">
            <input className={inputClass} value={form.api_key_name} onChange={set('api_key_name')} />
          </Field>
          <Field label="API key location">
            <select className={inputClass} value={form.api_key_location} onChange={set('api_key_location')}>
              {(vocabulary?.api_key_locations || []).map((row) => (
                <option key={row} value={row}>
                  {row}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}

      <Field
        label="Secret"
        hint={
          form.auth_mode === 'bearer'
            ? 'The token itself. The vendor documents the value in the form `Bearer [YOUR_TOKEN]`.'
            : 'One secret for this endpoint. Any number of automations may aim at it.'
        }
      >
        <input className={inputClass} type="password" value={form.secret} onChange={set('secret')} required />
      </Field>

      <Field label="Request body" hint={vocabulary?.body_quote}>
        <select className={inputClass} value={form.body_mode} onChange={set('body_mode')}>
          {(vocabulary?.body_modes || []).map((row) => (
            <option key={row.mode} value={row.mode}>
              {row.label}
            </option>
          ))}
        </select>
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Property carrying the record id" hint="Left blank, the room uses its own default for the object.">
          <input className={inputClass} value={form.id_key} onChange={set('id_key')} placeholder="deal_id" />
        </Field>
        <Field label="Property carrying the stage" hint="Left blank, the room uses its own default for the object.">
          <input className={inputClass} value={form.stage_key} onChange={set('stage_key')} placeholder="stage" />
        </Field>
      </div>

      <div>
        <Button type="submit" variant="primary" icon="plus" disabled={!permissions}>
          Save the endpoint
        </Button>
        <span className="ml-3 text-xs text-muted-foreground">
          Saved, not live: the CRM-side workflow must be published before it goes live, and so must this.
        </span>
      </div>
    </form>
  )
}

function EndpointPanel({ roomId, registered, vocabulary, nonce, permissions, onAction, onError }) {
  const endpoint = useAsync(
    () => (registered ? webhookApi.endpoint(roomId) : Promise.resolve(null)),
    [roomId, registered, nonce],
  )

  if (registered && endpoint.loading) return <Spinner label="Reading the webhook settings" />
  if (endpoint.error) return <ErrorNote error={endpoint.error} onRetry={endpoint.refetch} />

  if (!registered) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyState
          title="No endpoint yet"
          description="The room exposes one inbound endpoint per tenant, and this room has not set one up. Until it does there is no URL to give the CRM."
        />
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">Set one up</h2>
          <div className="mt-3">
            <EndpointForm
              vocabulary={vocabulary}
              permissions={permissions}
              onDone={(payload) => onAction(() => webhookApi.register(roomId, payload, permissions))}
              onError={onError}
            />
          </div>
        </Card>
      </div>
    )
  }

  const data = endpoint.data?.endpoint
  if (!data) return <Spinner label="Reading the webhook settings" />

  const auth = data.auth || {}
  const body = data.body || {}
  const published = data.status === 'published'

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={published ? 'insert' : 'update'}>{data.status}</Badge>
            <Badge tone="neutral">{data.method}</Badge>
            <Badge tone="neutral">{objectLabel(vocabulary, data.object)}</Badge>
            <Badge tone="neutral">{auth.mode}</Badge>
            <span className="text-xs text-muted-foreground">
              {published ? `Published ${relativeTime(data.published_at)}` : 'Saved, never published'}
            </span>
          </div>
          <Facts
            rows={[
              { label: 'Contract version', value: data.contract_version },
              { label: 'Automation count', value: data.automation_count },
              { label: 'App ID', value: auth.app_id },
              { label: 'API key', value: auth.mode === 'api_key' ? `${auth.name} (${auth.location})` : '' },
              { label: 'Record id property', value: data.id_key },
              { label: 'Stage property', value: data.stage_key },
              { label: 'Body mode', value: body.mode },
            ]}
          />
          <p className="text-xs text-muted-foreground">{data.notes}</p>
        </div>
      </Card>

      <Card>
        <div className="flex flex-col gap-4">
          <h2 className="font-mono text-sm font-semibold text-foreground">Paste these into the CRM</h2>
          <CopyField label="Endpoint URL" value={data.url} />
          <CopyField
            label={auth.mode === 'bearer' ? 'Secret (the token)' : 'Secret'}
            value={auth.secret}
            secret
            hint="One secret for the whole endpoint: the room verifies the request signature, so it does not need a per-workflow secret."
          />
          <p className="text-xs text-muted-foreground">{authRule(vocabulary, auth.mode)}</p>
        </div>
      </Card>

      {body.mode === 'customize' && (
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">Customized request body</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The keys the room expects to be told about. A payload missing one is accepted and the missing
            key is reported on the delivery - a customised body sends only the keys it names.
          </p>
          <ul className="mt-3 flex flex-col">
            {(body.keys || []).map((row) => (
              <li key={row.key} className="flex flex-wrap items-center gap-2 border-t border-border-subtle/30 py-2 first:border-t-0">
                <Code>{row.key}</Code>
                <Badge tone="neutral">{row.kind}</Badge>
                <Code>{row.kind === 'property' ? row.property : String(row.value)}</Code>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Publish</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {vocabulary?.publish_rule} Until it is published, a correctly signed delivery is recorded and
          refused - so a rep who forgot this button can see it in the log.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={published || !permissions}
            onClick={() =>
              onAction(() => webhookApi.publish(roomId, permissions), 'Published. Deliveries are taken from now on.')
            }
          >
            Publish
          </Button>
          <Button
            disabled={!published || !permissions}
            onClick={() =>
              onAction(
                () => webhookApi.unpublish(roomId, permissions),
                'Unpublished. Deliveries are recorded and refused until it is published again.',
              )
            }
          >
            Unpublish
          </Button>
        </div>
      </Card>

      <SampleCard nonce={nonce} vocabulary={vocabulary} roomId={roomId} />
    </div>
  )
}

function SampleCard({ nonce, vocabulary, roomId }) {
  const loaded = useAsync(() => (roomId ? webhookApi.sample(roomId) : Promise.resolve(null)), [roomId, nonce])
  if (loaded.loading) return <Spinner label="Building the sample payload" />
  if (loaded.error) return <ErrorNote error={loaded.error} onRetry={loaded.refetch} />
  const data = loaded.data
  if (!data) return null

  return (
    <Card>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-mono text-sm font-semibold text-foreground">What the Test control will send</h2>
          <Badge tone="update">preview</Badge>
        </div>
        <p className="text-sm text-muted-foreground">{data.note}</p>
        <Facts
          rows={[
            { label: 'Method', value: data.method },
            { label: 'URI', value: data.uri },
            { label: 'Body mode configured', value: data.body_mode_configured },
            { label: 'Signature header', value: data.signature.header },
          ]}
        />
        <pre className="overflow-x-auto rounded-lg border border-border-subtle/40 bg-background/60 p-3 font-mono text-[12px] text-foreground">
          {JSON.stringify(data.body, null, 2)}
        </pre>
        <details>
          <summary className="min-h-11 cursor-pointer py-2 text-sm text-accent">
            Show the canonical string the signature covers
          </summary>
          <pre className="mt-2 overflow-x-auto rounded-lg border border-border-subtle/40 bg-background/60 p-3 font-mono text-[12px] text-muted-foreground">
            {data.signature.canonical_string}
          </pre>
          <p className="mt-2 text-xs text-muted-foreground">
            The signature scheme itself is the one thing this workflow&apos;s sources name but do not
            publish. It is the vendor&apos;s own, and the reading is recorded in the Decisions tab.
          </p>
        </details>
        {vocabulary?.settings_page_note && (
          <p className="text-xs text-muted-foreground">{vocabulary.settings_page_note}</p>
        )}
      </div>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Automations: step 2
// --------------------------------------------------------------------------- //

function AutomationForm({ permissions, onAdd, onError }) {
  const [form, setForm] = useState({
    name: '',
    property: 'dealstage',
    equals: 'Contract Sent',
    kind: 'becomes',
    key: '',
    value: '',
  })
  const set = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))

  async function submit(event) {
    event.preventDefault()
    const trigger =
      form.kind === 'becomes'
        ? { property: form.property, equals: form.equals }
        : { property: form.property, changed: true }
    const payload = { name: form.name, trigger }
    if (form.key) {
      payload.static_values = [{ key: form.key, value: form.value }]
    }
    try {
      await onAdd(payload)
      setForm((prev) => ({ ...prev, name: '', key: '', value: '' }))
    } catch (error) {
      onError(error)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field label="Automation name" hint="The name a rep would recognise in the CRM's workflow list.">
        <input className={inputClass} value={form.name} onChange={set('name')} required />
      </Field>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Field label="Start condition: property">
          <input className={inputClass} value={form.property} onChange={set('property')} required />
        </Field>
        <Field label="Condition">
          <select className={inputClass} value={form.kind} onChange={set('kind')}>
            <option value="becomes">becomes a value</option>
            <option value="changes">the property changes</option>
          </select>
        </Field>
        {form.kind === 'becomes' ? (
          <Field label="Becomes">
            <input className={inputClass} value={form.equals} onChange={set('equals')} />
          </Field>
        ) : (
          <p className="self-end pb-3 text-xs text-muted-foreground">
            &quot;or a contact property changes&quot; - the researched second condition.
          </p>
        )}
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field
          label="Static value key (optional)"
          hint="To add a static field, enter the Key and Value. A delivery_id here makes retries unambiguous."
        >
          <input className={inputClass} value={form.key} onChange={set('key')} placeholder="delivery_id" />
        </Field>
        <Field label="Static value">
          <input className={inputClass} value={form.value} onChange={set('value')} />
        </Field>
      </div>
      <div>
        <Button type="submit" variant="primary" disabled={!permissions}>
          Add the automation
        </Button>
      </div>
    </form>
  )
}

function AutomationsPanel({ roomId, permissions, nonce, onAction, onError }) {
  const listed = useAsync(() => webhookApi.automations(roomId, { include_retired: true }), [roomId, nonce])

  if (listed.loading) return <Spinner label="Reading the automations" />
  if (listed.error) return <ErrorNote error={listed.error} onRetry={listed.refetch} />

  const rows = listed.data?.automations || []
  const retired = listed.data?.retired || 0
  const used = listed.data?.subscriptions_used ?? 0
  const limit = listed.data?.subscription_limit ?? 1000

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-col gap-2">
          <h2 className="font-mono text-sm font-semibold text-foreground">Subscriptions this app is using</h2>
          <p className="text-sm text-muted-foreground">
            <Code>
              {used} / {limit}
            </Code>{' '}
            webhook subscriptions. The limit is the vendor&apos;s and it is counted per app, across every
            room, so it is reported here rather than discovered at the limit.
          </p>
          <p className="text-xs text-muted-foreground">
            App ID {listed.data?.app_id}. {retired} retired automation(s) are listed below, because a
            delivery may still name one.
          </p>
        </div>
      </Card>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Workflows pointed at this endpoint</h2>
        {rows.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">
            Nothing yet. The endpoint takes any number of automations, so a new CRM-side workflow can be
            aimed at it without the room&apos;s say-so.
          </p>
        ) : (
          <ul className="mt-3 flex flex-col">
            {rows.map((row) => {
              const trigger = row.trigger || {}
              return (
                <li key={row.id} className="flex flex-col gap-1 border-t border-border-subtle/30 py-3 first:border-t-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Code>{row.name}</Code>
                    {row.deleted_at && <Badge tone="delete">retired</Badge>}
                    {!row.enabled && !row.deleted_at && <Badge tone="neutral">disabled</Badge>}
                    <Badge tone="neutral">
                      {trigger.property} {trigger.kind === 'becomes' ? `becomes ${trigger.equals}` : 'changes'}
                    </Badge>
                    {(row.body?.keys || []).map((key) => (
                      <Badge key={key.key} tone="update">
                        {key.key}={key.kind === 'static' ? String(key.value) : key.property}
                      </Badge>
                    ))}
                  </div>
                  {row.description && <p className="text-xs text-muted-foreground">{row.description}</p>}
                  {!row.deleted_at && permissions && (
                    <div>
                      <button
                        type="button"
                        onClick={() =>
                          onAction(
                            () => webhookApi.retireAutomation(roomId, row.id, permissions),
                            'Retired. The deliveries that named it still resolve.',
                          )
                        }
                        className="inline-flex min-h-11 cursor-pointer items-center rounded-lg px-3 text-sm text-accent transition-colors duration-200 hover:bg-accent/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      >
                        Retire
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </Card>

      <Card>
        <h2 className="font-mono text-sm font-semibold text-foreground">Add an automation</h2>
        <div className="mt-3">
          <AutomationForm
            permissions={permissions}
            onAdd={async (payload) => onAction(() => webhookApi.addAutomation(roomId, payload, permissions))}
            onError={onError}
          />
        </div>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Deliveries: what arrived
// --------------------------------------------------------------------------- //

function DeliveriesPanel({ roomId, vocabulary, nonce }) {
  const [outcome, setOutcome] = useState('')
  const [effect, setEffect] = useState('')
  const listed = useAsync(
    () => webhookApi.deliveries(roomId, { outcome, effect, limit: 100 }),
    [roomId, outcome, effect, nonce],
  )

  if (listed.loading) return <Spinner label="Reading the delivery log" />
  if (listed.error) return <ErrorNote error={listed.error} onRetry={listed.refetch} />

  const rows = listed.data?.deliveries || []

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-3">
        <Field label="Outcome" id="wf044-outcome">
          <select
            id="wf044-outcome"
            className={inputClass}
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
          >
            <option value="">all, refused included</option>
            {(vocabulary?.outcomes || []).map((row) => (
              <option key={row} value={row}>
                {row}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Effect" id="wf044-effect">
          <select
            id="wf044-effect"
            className={inputClass}
            value={effect}
            onChange={(event) => setEffect(event.target.value)}
          >
            <option value="">all</option>
            {(vocabulary?.effects || []).map((row) => (
              <option key={row} value={row}>
                {row}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="No deliveries yet"
          description="A delivery lands here the moment the CRM sends one - including the ones this endpoint turned away."
        />
      ) : (
        <ul className="flex flex-col">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-col gap-1.5 border-t border-border-subtle/30 py-3 first:border-t-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5">
                  <Glyph name={row.outcome === 'refused' ? 'refused' : 'applied'} size={16} />
                  <Badge tone={OUTCOME_TONE[row.outcome] || 'neutral'}>{row.outcome}</Badge>
                </span>
                <Badge tone={EFFECT_TONE[row.effect] || 'neutral'}>{row.effect}</Badge>
                <span className="text-xs text-muted-foreground">
                  {row.method} &middot; {relativeTime(row.at || row.updated_at || row.created_at)}
                </span>
                {row.duplicate_attempts > 0 && (
                  <Badge tone="update">
                    {row.duplicate_attempts} retr{row.duplicate_attempts === 1 ? 'y' : 'ies'}, applied once
                  </Badge>
                )}
                {row.notified && <Badge tone="insert">notified the rep</Badge>}
                {!row.notified && notifiedByEffect(row.effect) === false && (
                  <span className="text-xs text-muted-foreground">changed nothing, so nobody was told</span>
                )}
              </div>
              {row.stage && (
                <p className="text-sm text-foreground">
                  {row.object} {row.external_id}:{' '}
                  {row.previous_stage ? `${row.previous_stage} -> ` : 'now '}
                  <Code>{row.stage}</Code>
                </p>
              )}
              {row.reason && <p className="text-xs text-muted-foreground">{reasonText(vocabulary, row.reason)}</p>}
              {row.detail && <p className="text-xs text-muted-foreground">{row.detail}</p>}
              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                {row.automation && <span>automation: {row.automation}</span>}
                {row.automation_known === false && <Badge tone="update">not registered here</Badge>}
                {row.automation_retired && <Badge tone="neutral">retired</Badge>}
                {(row.missing_keys || []).length > 0 && (
                  <span>body keys not sent: {row.missing_keys.join(', ')}</span>
                )}
                {(row.stale_keys || []).length > 0 && (
                  <span>no longer sent: {row.stale_keys.join(', ')}</span>
                )}
                {row.body_mode_configured && row.body_mode_observed !== row.body_mode_configured && (
                  <span>
                    body mode configured {row.body_mode_configured}, arrived as {row.body_mode_observed}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// The deal panel
// --------------------------------------------------------------------------- //

function DealsPanel({ roomId, nonce }) {
  const listed = useAsync(() => webhookApi.deals(roomId), [roomId, nonce])
  if (listed.loading) return <Spinner label="Reading the deal panel" />
  if (listed.error) return <ErrorNote error={listed.error} onRetry={listed.refetch} />

  const deals = listed.data?.deals || []
  if (deals.length === 0) {
    return (
      <EmptyState
        title="Nothing on the panel yet"
        description="A delivery is what puts a deal here. The first signed delivery about a deal this room has not met creates its row."
      />
    )
  }

  return (
    <ul className="flex flex-col gap-4">
      {deals.map((deal) => (
        <li key={deal.id}>
          <Card>
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Code>{deal.external_id}</Code>
                <Badge tone="neutral">{deal.object}</Badge>
                <Badge tone={deal.stage ? 'insert' : 'neutral'}>{deal.stage || 'no stage'}</Badge>
                {deal.previous_stage && (
                  <span className="text-xs text-muted-foreground">was {deal.previous_stage}</span>
                )}
              </div>
              {deal.stage_at && (
                <p className="text-xs text-muted-foreground">stage last moved {relativeTime(deal.stage_at)}</p>
              )}
              {Object.keys(deal.properties || {}).length === 0 ? (
                <p className="text-sm text-muted-foreground">No properties.</p>
              ) : (
                <Facts
                  rows={Object.entries(deal.properties).map(([key, value]) => ({
                    label: key,
                    value: typeof value === 'object' ? JSON.stringify(value) : String(value),
                  }))}
                />
              )}
              {(deal.stale_keys || []).length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Not confirmed by the last delivery: {deal.stale_keys.join(', ')}. A customised body
                  sends only the keys it names, so a missing key is reported rather than treated as a
                  deletion.
                </p>
              )}
            </div>
          </Card>
        </li>
      ))}
    </ul>
  )
}

// --------------------------------------------------------------------------- //
// Notices
// --------------------------------------------------------------------------- //

function NoticesPanel({ roomId, nonce, onAction, onError }) {
  const listed = useAsync(() => webhookApi.notices(roomId, { limit: 100 }), [roomId, nonce])
  if (listed.loading) return <Spinner label="Reading what the endpoint told the rep" />
  if (listed.error) return <ErrorNote error={listed.error} onRetry={listed.refetch} />

  const notices = listed.data?.notices || []
  if (notices.length === 0) {
    return (
      <EmptyState
        title="Nothing to act on"
        description="One notice per delivery that changed the deal panel. A delivery that changed nothing does not produce one - a notification that always says the same thing is a notification people mute."
      />
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button
          onClick={() => onAction(() => webhookApi.acknowledge(roomId, { all: true }))}
        >
          Acknowledge all ({listed.data?.unread || 0} unread)
        </Button>
      </div>
      <ul className="flex flex-col">
        {notices.map((notice) => (
          <li key={notice.id} className="flex flex-col gap-1 border-t border-border-subtle/30 py-3 first:border-t-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={notice.read ? 'neutral' : 'insert'}>{notice.read ? 'read' : 'unread'}</Badge>
              <Badge tone={EFFECT_TONE[notice.effect] || 'neutral'}>{notice.effect}</Badge>
              <span className="text-xs text-muted-foreground">{relativeTime(notice.at)}</span>
            </div>
            <p className="text-sm text-foreground">{notice.summary}</p>
            {notice.automation && <p className="text-xs text-muted-foreground">via {notice.automation}</p>}
          </li>
        ))}
      </ul>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// The decisions register
// --------------------------------------------------------------------------- //

function DecisionsPanel({ inferences }) {
  if (inferences.loading) return <Spinner label="Reading the decision register" />
  if (inferences.error) return <ErrorNote error={inferences.error} onRetry={inferences.refetch} />

  const entries = inferences.data?.inferences || []
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        The research names the CRM-side editor in detail and says almost nothing about the receiving end.
        These are the edges, and what this build chose for each. The first one is the largest: the
        request-signature scheme is named by the sources but not published by them.
      </p>
      {entries.map((entry) => (
        <Card key={entry.id}>
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Code>{entry.id}</Code>
              <span className="text-sm font-medium text-foreground">{entry.topic}</span>
            </div>
            <p className="text-sm text-muted-foreground">{entry.basis}</p>
            <p className="text-sm text-foreground">{entry.why}</p>
            <div>
              <span className="text-xs text-muted-foreground">Change it at: </span>
              <Code>{entry.change_it}</Code>
            </div>
          </div>
        </Card>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// The page
// --------------------------------------------------------------------------- //

export default function CrmWebhook() {
  const [tab, setTab] = useState('endpoint')
  const [roomId, setRoomId] = useState('')
  const [permission, setPermission] = useState('edit_workflows,publish_workflows')
  const [nonce, setNonce] = useState(0)
  const [actionError, setActionError] = useState(null)
  const [done, setDone] = useState('')

  const vocabulary = useAsync(() => webhookApi.vocabulary(), [])
  const inferences = useAsync(() => webhookApi.inferences(), [])
  const rooms = useAsync(() => webhookApi.rooms(), [])
  const summary = useAsync(
    () => (roomId ? webhookApi.summary(roomId) : Promise.resolve(null)),
    [roomId, nonce],
  )

  useEffect(() => {
    if (!roomId && rooms.data?.records?.length) setRoomId(rooms.data.records[0].id)
  }, [rooms.data, roomId])

  useEffect(() => {
    if (done) {
      const timer = setTimeout(() => setDone(''), 6000)
      return () => clearTimeout(timer)
    }
    return undefined
  }, [done])

  /** Run a write, then re-read everything and say what happened in words. */
  const act = async (call, message) => {
    setActionError(null)
    try {
      await call()
      setDone(message || 'Saved.')
      setNonce((n) => n + 1)
    } catch (error) {
      setActionError(error)
    }
  }

  const failure = [vocabulary, inferences, rooms, summary].find((state) => state.error)
  if (failure) return <ErrorNote error={failure.error} onRetry={failure.refetch} />

  if (rooms.loading) return <Spinner label="Loading rooms" />

  const roomOptions = rooms.data?.records || []
  if (roomOptions.length === 0) {
    return (
      <div className="flex flex-col gap-6">
        <h1 className="font-mono text-xl font-semibold text-foreground">CRM webhook</h1>
        <EmptyState
          title="No rooms yet"
          description="The research exposes one inbound endpoint per tenant, so this needs a room to expose an endpoint for."
        />
      </div>
    )
  }

  const numbers = summary.data
  const endpointState = numbers?.endpoint

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-mono text-xl font-semibold text-foreground">CRM webhook</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            One inbound endpoint per room, any number of CRM-side automations aimed at it, and a signed
            POST or GET taken from the CRM: verify the signature, resolve the record, update the deal
            panel, tell the rep.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Room" id="wf044-room">
            <select
              id="wf044-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              {roomOptions.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.data?.account || room.id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="This operator holds, in the CRM" id="wf044-permission">
            <select
              id="wf044-permission"
              className={inputClass}
              value={permission}
              onChange={(event) => setPermission(event.target.value)}
            >
              {PERMISSION_CHOICES.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choice.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard
          label="Endpoint"
          value={endpointState?.registered ? endpointState.status : 'not set up'}
          hint={endpointState?.registered ? `${endpointState.method} ${endpointState.auth_mode}` : 'no URL to give the CRM'}
        />
        <StatCard label="Automations" value={numbers?.automations ?? 0} hint={`${numbers?.subscriptions_used ?? 0}/1000 subscriptions`} />
        <StatCard
          label="Deliveries"
          value={numbers?.deliveries ?? 0}
          hint={`${numbers?.duplicate_attempts ?? 0} retr${(numbers?.duplicate_attempts ?? 0) === 1 ? 'y' : 'ies'}`}
        />
        <StatCard label="Deals" value={numbers?.deals ?? 0} hint="on the panel" />
        <StatCard label="Unread" value={numbers?.unread ?? 0} hint="notices for the rep" />
      </div>

      <ResearchNote>
        {vocabulary.data?.publish_rule} Until then a correctly signed delivery is recorded and refused.
        Because the room verifies the request signature, the endpoint needs no per-workflow secret - one
        URL takes any number of CRM-side automations.
      </ResearchNote>

      {numbers?.rate_limit_note && (
        <p className="text-xs text-muted-foreground">
          {numbers.rate_limit_note} {numbers.slow_sender_note}
        </p>
      )}

      <TabBar current={tab} onChange={setTab} />

      {actionError && (
        <ErrorNote
          error={actionError}
          onRetry={() => setActionError(null)}
        />
      )}
      {done && (
        <p role="status" className="rounded-lg border border-border-subtle/40 bg-muted/40 p-3 text-sm text-foreground">
          {done}
        </p>
      )}

      <section id={`wf044-panel-${tab}`} role="tabpanel" aria-labelledby={`wf044-tab-${tab}`}>
        {tab === 'endpoint' && (
          <EndpointPanel
            roomId={roomId}
            registered={Boolean(endpointState?.registered)}
            vocabulary={vocabulary.data}
            nonce={nonce}
            permissions={permission}
            onAction={act}
            onError={setActionError}
          />
        )}
        {tab === 'automations' && (
          <AutomationsPanel
            roomId={roomId}
            permissions={permission}
            nonce={nonce}
            onAction={act}
            onError={setActionError}
          />
        )}
        {tab === 'deliveries' && (
          <DeliveriesPanel roomId={roomId} vocabulary={vocabulary.data} nonce={nonce} />
        )}
        {tab === 'deals' && <DealsPanel roomId={roomId} nonce={nonce} />}
        {tab === 'notices' && (
          <NoticesPanel roomId={roomId} nonce={nonce} onAction={act} onError={setActionError} />
        )}
        {tab === 'decisions' && <DecisionsPanel inferences={inferences} />}
      </section>

      <footer className="flex items-center gap-2 border-t border-border-subtle/30 pt-4 text-xs text-muted-foreground">
        <Icon path={WEBHOOK_ICON} size={14} />
        <span>
          The outbound side is the CRM&apos;s. Nothing on this page calls a vendor API - this is the
          receiving end of the researched flow.
        </span>
      </footer>
    </div>
  )
}
