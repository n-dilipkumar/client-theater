/**
 * CRM connections: authorize a CRM org over OAuth 2.0 (WF-034).
 *
 * The page is the researched flow, in the order an admin walks it, plus the two
 * things a person has to read afterwards:
 *
 *   1. pick a vendor. Each card says which of its facts are quoted from the
 *      research and which are this build's inference, because the research read
 *      HubSpot's authorize URL and could not read the Salesforce flow pages or the
 *      Dataverse auth page at all.
 *   2. register the connection: the client id and secret the vendor's own app
 *      screen gave them, the scopes to ask for, and the org/account the consent
 *      screen will confirm.
 *   3. **Authorize.** The room builds the vendor's URL and opens it. On the way
 *      back the vendor hands over `?code=…&state=…`; a pending authorization here
 *      carries the `state` to paste it with, which is what a deployment whose
 *      redirect lands on a different host does.
 *   4. **Test connection.** One low-cost authenticated call with the bearer.
 *   5. what became of the token: the TTL, the next refresh, the next poll.
 *
 * Two things this page makes loud, because the research says they matter:
 *
 *   - **A 401 is not a refresh.** When the vendor rejects a token the row keeps
 *     its status and its expiry and says the remedy is the consent screen. The
 *     page shows those three facts side by side, so nobody reads "unauthorized"
 *     as "the token ran out" and reaches for Refresh.
 *   - **Readiness.** A connection that cannot be authorized is a state with named
 *     missing fields, and a half-configured connection never reaches a consent
 *     screen at all.
 *
 * No credential is ever rendered. The client secret is sealed in the vault, the
 * token is sealed, and there is no endpoint that returns either; the page shows
 * `has_client_secret` and the sealed row's field *names*.
 *
 * The pickers are rendered from `/vocabulary` and `/connectors` rather than from a
 * list compiled into this file, and every form field is free text, so a team that
 * adds a fourth CRM publishes a connector rather than changing this page.
 */

import { useCallback, useMemo, useState } from 'react'
import { absoluteTime, apiRequest, relativeTime } from '@/lib/api'
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
import { crmApi } from './api'
import Glyph from './icons'
import { Note, StatTile, Toggle } from './primitives'

const SECTIONS = [
  { id: 'vendors', label: 'The three vendors', icon: 'consent' },
  { id: 'connections', label: 'Connections', icon: 'link' },
  { id: 'readiness', label: 'What is missing', icon: 'pulse' },
  { id: 'pending', label: 'Authorizations in flight', icon: 'consent' },
  { id: 'events', label: 'Token lifecycle', icon: 'keyhole' },
  { id: 'contract', label: 'The researched contract', icon: 'schema' },
]

/** The status badge tones. `unauthorized` is not an error, so it is not red. */
const STATUS_TONE = {
  authorized: 'insert',
  expired: 'restore',
  pending_authorization: 'neutral',
  disconnected: 'neutral',
}
const HEALTH_TONE = { ok: 'insert', unauthorized: 'restore', error: 'delete', unknown: 'neutral' }

/** A human phrasing for a remaining-seconds count, or a dash when there is none. */
function countdown(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const value = Math.max(0, Math.round(seconds))
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.round(value / 60)}m`
  if (value < 86400) return `${Math.round(value / 3600)}h`
  return `${Math.round(value / 86400)}d`
}

/** One registered connector, with its provenance. */
function VendorCard({ entry }) {
  const info = entry.info
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold text-foreground">{info.label}</h3>
        <Badge tone="neutral">{entry.vendor}</Badge>
      </div>
      <dl className="mt-3 space-y-2 text-xs">
        <div>
          <dt className="tracking-wide text-muted-foreground uppercase">Authorize URL</dt>
          <dd className="mt-0.5 font-mono break-all text-foreground">{info.authorize_endpoint}</dd>
        </div>
        <div>
          <dt className="tracking-wide text-muted-foreground uppercase">API base</dt>
          <dd className="mt-0.5 font-mono break-all text-foreground">{info.api_base_url}</dd>
        </div>
        <div>
          <dt className="tracking-wide text-muted-foreground uppercase">Test connection calls</dt>
          <dd className="mt-0.5 font-mono break-all text-foreground">
            {info.probe_path}
            {Object.keys(info.probe_query || {}).length > 0 && (
              <span className="text-muted-foreground">
                {' '}
                ?{new URLSearchParams(info.probe_query).toString()}
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="tracking-wide text-muted-foreground uppercase">Suggested scopes</dt>
          <dd className="mt-0.5 font-mono break-all text-foreground/90">
            {(info.scopes.suggested || []).join(' ') || 'none recorded'}
          </dd>
        </div>
      </dl>
      {info.grant_requirements.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Before you can grant it
          </p>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
            {info.grant_requirements.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
      <details className="mt-3">
        <summary className="min-h-11 cursor-pointer py-2 text-xs text-muted-foreground">
          What is quoted, and what is this build&apos;s inference
        </summary>
        <ul className="mt-1 space-y-1 text-xs">
          {info.researched.map((id) => (
            <li key={id} className="flex items-start gap-2">
              <Badge tone="insert">quoted</Badge>
              <span className="font-mono text-foreground/90">{id}</span>
            </li>
          ))}
          {info.inferences.map((line) => (
            <li key={line} className="flex items-start gap-2">
              <Badge tone="restore">inferred</Badge>
              <span className="text-muted-foreground">{line}</span>
            </li>
          ))}
        </ul>
      </details>
    </Card>
  )
}

/** One connection, its credential state, and the actions the research allows. */
function ConnectionRow({ row, busy, onTest, onRefresh, onAuthorize, onToggle, onDisconnect, expanded, onExpand }) {
  const action = row.needs_action
  const tone = STATUS_TONE[row.status] || 'neutral'

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <div className="flex flex-wrap items-center gap-3 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] text-foreground">{row.label || row.vendor_label}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {row.vendor_label} · org {row.org_id || row.credential_org_key || 'not chosen yet'} ·{' '}
            {row.scope} scope · expires {row.expires_in === null ? 'unknown' : countdown(row.expires_in)}
            {!row.ttl_known && ' (no expires_in from the vendor)'}
          </p>
        </div>
        <Badge tone={tone}>{row.status}</Badge>
        <Badge tone={HEALTH_TONE[row.health] || 'neutral'}>
          {row.health === 'unauthorized' ? '401 from the vendor' : row.health}
        </Badge>
        <Toggle
          checked={row.enabled}
          disabled={busy}
          label={`${row.enabled ? 'Switch off' : 'Switch on'} the connection ${row.label || row.id}`}
          onChange={(next) => onToggle(row, next)}
        />
      </div>

      {action && (
        <p className="pb-2 text-xs text-amber-300">
          <span className="font-mono">{action.action}</span> — {action.why}
        </p>
      )}
      {row.blockers.length > 0 && (
        <ul className="pb-2 space-y-1">
          {row.blockers.map((blocker) => (
            <li key={blocker.code} className="flex flex-wrap items-baseline gap-2 text-xs">
              <Badge tone="delete">{blocker.code}</Badge>
              <span className="text-muted-foreground">{blocker.why}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2 pb-3">
        <Button
          icon="plus"
          variant="primary"
          disabled={busy || row.blockers.length > 0}
          title={row.blockers.length > 0 ? 'Clear the blockers above first' : undefined}
          onClick={() => onAuthorize(row)}
        >
          Authorize
        </Button>
        <Button icon="pulse" disabled={busy} onClick={() => onTest(row)}>
          Test connection
        </Button>
        <Button
          icon="refresh"
          disabled={busy || !row.credential?.has_refresh_token}
          title={row.credential?.has_refresh_token ? undefined : 'No refresh token sealed for this org'}
          onClick={() => onRefresh(row)}
        >
          Refresh now
        </Button>
        <Button
          icon="chevron"
          variant="ghost"
          aria-expanded={expanded}
          onClick={() => onExpand(row.id)}
        >
          {expanded ? 'Hide detail' : 'Show detail'}
        </Button>
        <Button
          icon="trash"
          variant="danger"
          disabled={busy}
          onClick={() => onDisconnect(row)}
          aria-label={`Disconnect ${row.label || row.id}`}
        >
          Disconnect
        </Button>
      </div>

      {expanded && (
        <div className="mb-3 space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <p className="tracking-wide text-muted-foreground uppercase">Expires at</p>
              <p className="font-mono text-foreground">{absoluteTime(row.expires_at)}</p>
            </div>
            <div>
              <p className="tracking-wide text-muted-foreground uppercase">Refresh due</p>
              <p className="font-mono text-foreground">
                {absoluteTime(row.refresh_due_at)} ({countdown(row.refresh_in)})
              </p>
            </div>
            <div>
              <p className="tracking-wide text-muted-foreground uppercase">Last probe</p>
              <p className="font-mono text-foreground">{relativeTime(row.last_checked_at)}</p>
            </div>
            <div>
              <p className="tracking-wide text-muted-foreground uppercase">Next poll</p>
              <p className="font-mono text-foreground">
                {relativeTime(row.next_check_at)} (every {countdown(row.health_interval_seconds)})
              </p>
            </div>
          </div>
          {row.health_detail && (
            <p className="text-muted-foreground">{row.health_detail}</p>
          )}
          {row.last_unauthorized_at && (
            <p className="text-amber-300">
              Last 401 {relativeTime(row.last_unauthorized_at)}. The token, its expiry and the poll
              interval were all left alone: a 401 is not a refresh trigger.
            </p>
          )}
          {row.last_refresh_error && (
            <p className="text-amber-300">
              The last refresh was refused {relativeTime(row.refresh_refused_at)}: {row.last_refresh_error}
            </p>
          )}
          <div>
            <p className="tracking-wide text-muted-foreground uppercase">
              The sealed credential, as far as it may be said
            </p>
            <p className="mt-1 text-muted-foreground">
              {row.credential?.sealed
                ? `Sealed under org ${row.credential.org_key}, readable here: ${row.credential.readable_here ? 'yes' : 'no'}. Fields inside: ${row.credential.fields.join(', ')}.`
                : 'Nothing sealed for this connection yet.'}
            </p>
          </div>
          <JsonView
            value={{
              vendor: row.vendor,
              client_id: row.client_id,
              has_client_secret: row.has_client_secret,
              redirect_uri: row.redirect_uri,
              scopes: row.scopes,
              org_id: row.org_id,
              credential_org_key: row.credential_org_key,
              environment: row.environment,
              policy: row.policy,
              api_version: row.api_version,
              tenant: row.tenant,
            }}
          />
        </div>
      )}
    </li>
  )
}

export default function CrmConnections() {
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [watching, setWatching] = useState(null)
  const [codes, setCodes] = useState({})
  const [statusFilter, setStatusFilter] = useState('all')
  const [form, setForm] = useState({
    vendor: 'hubspot',
    label: '',
    tenant: 'default',
    client_id: '',
    client_secret: '',
    redirect_uri: '',
    scopes: '',
    org_id: '',
    environment_url: '',
  })

  const rooms = useAsync(() => apiRequest('/records/room?limit=200&order_by=name'), [])
  const vocabulary = useAsync(() => crmApi.vocabulary(), [])
  const inferences = useAsync(() => crmApi.inferences(), [])
  const connectors = useAsync(() => crmApi.connectors(), [])
  const summary = useAsync(() => crmApi.summary(), [])
  const connections = useAsync(
    () => crmApi.listConnections({ room_id: roomId || undefined, limit: 200 }),
    [roomId]
  )
  const readiness = useAsync(
    () => (roomId ? crmApi.readiness(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const grants = useAsync(
    () => crmApi.listGrants({ state: 'pending', limit: 50 }),
    []
  )
  const events = useAsync(
    () =>
      watching
        ? crmApi.tokenEvents(watching, { limit: 60 })
        : Promise.resolve({ events: [], summary: {}, count: 0 }),
    [watching]
  )

  const refreshAll = useCallback(() => {
    connections.refetch()
    summary.refetch()
    grants.refetch()
    readiness.refetch()
  }, [connections, summary, grants, readiness])

  const report = useCallback((tone, message) => setFlash({ tone, message }), [])

  const run = useCallback(
    async (action, success) => {
      setBusy(true)
      setFlash(null)
      try {
        const result = await action()
        report('good', typeof success === 'function' ? success(result) : success)
        refreshAll()
        return result
      } catch (error) {
        // 428 is "this installation is not set up to answer that yet" and 400 is
        // "the request asks for something this layer will not do". Both are things
        // the person can act on, so both read as a warning rather than an error.
        report(error?.status === 428 || error?.status === 400 ? 'warn' : 'info', String(error?.message || error))
        return null
      } finally {
        setBusy(false)
      }
    },
    [refreshAll, report]
  )

  const authorize = useCallback(
    (row) =>
      run(async () => {
        const grant = await crmApi.authorizeUrl(row.id)
        // The vendor's consent screen is a different origin, so the tab is opened
        // with noopener and the `state` is kept here for the callback.
        if (typeof window !== 'undefined') window.open(grant.authorize_url, '_blank', 'noopener')
        setCodes((current) => ({ ...current, [grant.grant_id]: { connection: row.id, code: '' } }))
        setWatching(row.id)
        return {
          message: `Sent to ${row.vendor_label}. When it redirects back, paste the code below to finish the exchange.`,
          grant,
        }
      }),
    [run]
  )

  const test = useCallback(
    (row) =>
      run(
        () => crmApi.test(row.id),
        (result) =>
          result.ok
            ? `${row.label || row.id}: the vendor answered ${result.vendor_status}${
                result.refreshed_before_probe ? ' (after refreshing on the stored TTL)' : ''
              }.`
            : `${row.label || row.id}: ${result.detail}`
      ),
    [run]
  )

  const refreshToken = useCallback(
    (row) =>
      run(
        () => crmApi.refresh(row.id),
        (result) => `${row.label || row.id}: refreshed on ${result.trigger}, expires ${absoluteTime(result.expires_at)}.`
      ),
    [run]
  )

  const toggle = useCallback(
    (row, enabled) =>
      run(
        () => crmApi.updateConnection(row.id, { enabled }),
        `${row.label || row.id} is now ${enabled ? 'on' : 'off'}.`
      ),
    [run]
  )

  const disconnect = useCallback(
    (row) =>
      run(
        () => crmApi.disconnect(row.id),
        `${row.label || row.id} disconnected. Its token events and audit trail stay.`
      ),
    [run]
  )

  const sweep = useCallback(
    (force) =>
      run(
        () => crmApi.healthCheck(roomId, force),
        (result) =>
          `Polled ${result.counts.checked} connection(s): ${result.counts.ok} ok, ${result.counts.unauthorized} rejected by the vendor, ${result.counts.error} failed, ${result.counts.skipped} not due.`
      ),
    [roomId, run]
  )

  const roomList = useMemo(() => rooms.data?.records || [], [rooms.data])
  const rows = useMemo(
    () =>
      (connections.data?.connections || []).filter(
        (row) => statusFilter === 'all' || row.status === statusFilter
      ),
    [connections.data, statusFilter]
  )
  const connectorEntries = connectors.data?.connectors || {}
  const counts = summary.data || {}
  const states = vocabulary.data?.states || {}
  const pendingGrants = grants.data?.grants || []
  const eventRows = events.data?.events || []
  const hints = connectors.data?.connectors?.[form.vendor]?.info

  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (rooms.loading) return <Spinner label="Loading CRM connections" />
  if (connections.error) return <ErrorNote error={connections.error} onRetry={connections.refetch} />

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-xl font-semibold text-foreground">CRM connections</h1>
          <Badge tone="update">WF-034</Badge>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Authorize a Salesforce, HubSpot or Dynamics org, exchange the code for a bearer token, and
          seal the refresh token in a vault keyed by the org id. Refresh happens on the TTL the token
          came with, and a 401 is recorded as a health fact rather than treated as a reason to fetch
          a new token.
        </p>
        <div className="max-w-md">
          <Field
            label="Room"
            id="wf034-room"
            hint="Readiness and the health sweep are room-scoped; the connection list is not."
          >
            <select
              id="wf034-room"
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
              className={inputClass}
            >
              <option value="">All connections</option>
              {roomList.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {flash && <Note tone={flash.tone}>{flash.message}</Note>}
        {connections.data?.vault_key_warning && (
          <Note tone="warn">{connections.data.vault_key_warning}</Note>
        )}
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Connections"
          value={rows.length}
          hint={`${Object.keys(connectorEntries).length} vendor(s) registered`}
          path="plug"
        />
        <StatTile
          label="Authorized"
          value={counts.statuses?.authorized ?? 0}
          hint="holding a token that is not past its TTL"
          path="link"
        />
        <StatTile
          label="Needs a person"
          value={counts.needs_action ?? 0}
          hint="a 401 is a consent screen, not a refresh"
          path="consent"
        />
        <StatTile
          label="Blocked"
          value={counts.blocked ?? 0}
          hint="missing something the authorize URL needs"
          path="pulse"
        />
      </section>

      {SECTIONS.map((section) => {
        const heading = (
          <h2 className="font-mono text-sm font-semibold text-foreground">{section.label}</h2>
        )
        const body = {
          vendors: (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                The researched user flow offers exactly these three. Each card says which of its facts
                are quoted from the vendor documentation and which are this build&apos;s inference,
                because the research read HubSpot&apos;s authorize URL and could not read
                Salesforce&apos;s flow pages or the Dataverse auth page at all.
              </p>
              <div className="grid gap-4 lg:grid-cols-3">
                {Object.entries(connectorEntries).map(([name, entry]) => (
                  <VendorCard key={name} entry={entry} />
                ))}
              </div>
              {connectors.error && <ErrorNote error={connectors.error} onRetry={connectors.refetch} />}
            </div>
          ),
          connections: (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <div className="max-w-xs">
                  <Field label="Show" id="wf034-status" hint="Filters the rows below.">
                    <select
                      id="wf034-status"
                      value={statusFilter}
                      onChange={(event) => setStatusFilter(event.target.value)}
                      className={inputClass}
                    >
                      <option value="all">Every status</option>
                      {(states.statuses || []).map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Button
                  icon="pulse"
                  disabled={busy || !roomId}
                  title={roomId ? undefined : 'Pick a room: the sweep is room-scoped'}
                  onClick={() => sweep(false)}
                >
                  Poll what is due
                </Button>
                <Button icon="refresh" disabled={busy || !roomId} onClick={() => sweep(true)}>
                  Poll everything now
                </Button>
              </div>
              {!roomId && (
                <Note tone="info">
                  Polling is driven by the room&apos;s own scheduler and nothing pushes at us, so the
                  sweep needs a room. Pick one above, or call the same route from your scheduler.
                </Note>
              )}
              {rows.length === 0 ? (
                <EmptyState
                  title="No connections yet"
                  description="Register one below: the client id and secret come from the vendor's own app screen, and everything else the consent screen asks for."
                />
              ) : (
                <ul>
                  {rows.map((row) => (
                    <ConnectionRow
                      key={row.id}
                      row={row}
                      busy={busy}
                      expanded={expanded === row.id}
                      onExpand={(id) => setExpanded((current) => (current === id ? null : id))}
                      onTest={test}
                      onRefresh={refreshToken}
                      onAuthorize={authorize}
                      onToggle={toggle}
                      onDisconnect={disconnect}
                    />
                  ))}
                </ul>
              )}

              <form
                className="grid gap-3 border-t border-border-subtle/20 pt-4 sm:grid-cols-2"
                onSubmit={(event) => {
                  event.preventDefault()
                  run(
                    () =>
                      crmApi.createConnection({
                        vendor: form.vendor,
                        label: form.label,
                        tenant: form.tenant,
                        room_id: roomId || null,
                        client_id: form.client_id,
                        client_secret: form.client_secret,
                        redirect_uri: form.redirect_uri,
                        scopes: form.scopes,
                        org_id: form.org_id,
                        environment_url: form.environment_url,
                      }),
                    `${form.vendor} connection registered. Authorize it to get a token.`
                  )
                  setForm({
                    vendor: form.vendor,
                    label: '',
                    tenant: form.tenant,
                    client_id: '',
                    client_secret: '',
                    redirect_uri: '',
                    scopes: '',
                    org_id: '',
                    environment_url: '',
                  })
                }}
              >
                <Field label="Vendor" id="wf034-vendor" hint="The researched flow offers these three.">
                  <select
                    id="wf034-vendor"
                    value={form.vendor}
                    onChange={(event) => setForm({ ...form, vendor: event.target.value })}
                    className={inputClass}
                  >
                    {(vocabulary.data?.vendors || []).map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Label" id="wf034-label" hint="Free text. Shown on the row.">
                  <input
                    id="wf034-label"
                    value={form.label}
                    onChange={(event) => setForm({ ...form, label: event.target.value })}
                    placeholder="Northwind Traders"
                    className={inputClass}
                  />
                </Field>
                <Field label="Client ID" id="wf034-client-id" hint="From the vendor's app screen.">
                  <input
                    id="wf034-client-id"
                    required
                    value={form.client_id}
                    onChange={(event) => setForm({ ...form, client_id: event.target.value })}
                    className={inputClass}
                  />
                </Field>
                <Field
                  label="Client secret"
                  id="wf034-client-secret"
                  hint="Sealed in the vault. Never returned by a read."
                >
                  <input
                    id="wf034-client-secret"
                    type="password"
                    value={form.client_secret}
                    onChange={(event) => setForm({ ...form, client_secret: event.target.value })}
                    className={inputClass}
                  />
                </Field>
                <Field
                  label="Redirect URI"
                  id="wf034-redirect"
                  hint="The vendor redirects here with a code. Must match the vendor's app screen."
                >
                  <input
                    id="wf034-redirect"
                    required
                    value={form.redirect_uri}
                    onChange={(event) => setForm({ ...form, redirect_uri: event.target.value })}
                    className={inputClass}
                  />
                </Field>
                <Field
                  label="Scopes"
                  id="wf034-scopes"
                  hint={
                    hints?.scopes?.suggested?.length
                      ? `Space separated. Suggested: ${hints.scopes.suggested.join(' ')}`
                      : 'Space separated. The consent screen grants what is asked for.'
                  }
                >
                  <input
                    id="wf034-scopes"
                    required
                    value={form.scopes}
                    onChange={(event) => setForm({ ...form, scopes: event.target.value })}
                    className={inputClass}
                  />
                </Field>
                <Field
                  label="Org / account"
                  id="wf034-org"
                  hint="The vault is keyed by this, so the credential has nowhere to live without it."
                >
                  <input
                    id="wf034-org"
                    value={form.org_id}
                    onChange={(event) => setForm({ ...form, org_id: event.target.value })}
                    className={inputClass}
                  />
                </Field>
                <Field
                  label="Environment host"
                  id="wf034-env"
                  hint="Only for a vendor whose resource URL is templated on the org."
                >
                  <input
                    id="wf034-env"
                    value={form.environment_url}
                    onChange={(event) => setForm({ ...form, environment_url: event.target.value })}
                    placeholder={hints?.api_base_url || ''}
                    className={inputClass}
                  />
                </Field>
                <div className="sm:col-span-2">
                  <Button type="submit" icon="plus" variant="primary" disabled={busy}>
                    Register connection
                  </Button>
                </div>
              </form>
            </div>
          ),
          readiness: (
            <div className="space-y-3">
              {!roomId ? (
                <EmptyState
                  title="Pick a room"
                  description="Readiness is room-scoped: it reports what each connection is missing before it can finish the researched flow."
                />
              ) : readiness.loading ? (
                <Spinner label="Reading this room's readiness" />
              ) : readiness.error ? (
                <ErrorNote error={readiness.error} onRetry={readiness.refetch} />
              ) : (
                <>
                  <p className="text-sm text-muted-foreground">
                    {readiness.data?.ready
                      ? 'Every connection serving this room can be authorized and used.'
                      : `${readiness.data?.blocked} of ${readiness.data?.count} cannot finish the flow yet.`}
                  </p>
                  <ul className="space-y-2">
                    {(readiness.data?.connections || []).map((entry) => (
                      <li key={entry.connection_id} className="rounded-lg border border-border-subtle/25 p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[13px] text-foreground">{entry.label}</span>
                          <Badge tone={STATUS_TONE[entry.status] || 'neutral'}>{entry.status}</Badge>
                          <Badge tone={HEALTH_TONE[entry.health] || 'neutral'}>{entry.health}</Badge>
                        </div>
                        {entry.blockers.length === 0 && entry.advisory.length > 0 && (
                          <div className="mt-2">
                            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                              Not checked, and not checkable from here
                            </p>
                            <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                              {entry.advisory.map((line) => (
                                <li key={line}>{line}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {entry.blockers.map((blocker) => (
                          <p key={blocker.code} className="mt-2 text-xs text-amber-300">
                            <span className="font-mono">{blocker.code}</span> — {blocker.why}
                          </p>
                        ))}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ),
          pending: (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                An authorization is live for ten minutes and a code can only be exchanged once. A
                deployment whose redirect lands on another host pastes the code here with the state
                this row carries.
              </p>
              {pendingGrants.length === 0 ? (
                <EmptyState
                  title="No authorization in flight"
                  description="Press Authorize on a connection to open the vendor's consent screen."
                />
              ) : (
                <ul className="space-y-3">
                  {pendingGrants.map((grant) => (
                    <li key={grant.id} className="rounded-lg border border-border-subtle/25 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="update">{grant.vendor}</Badge>
                        <span className="font-mono text-xs text-foreground">{grant.id}</span>
                        <span className="text-xs text-muted-foreground">
                          expires in {countdown(grant.seconds_remaining)}
                        </span>
                      </div>
                      <p className="mt-1 font-mono text-xs break-all text-muted-foreground">
                        {grant.redirect_uri}?code=…&state=…
                      </p>
                      <form
                        className="mt-2 flex flex-wrap items-end gap-2"
                        onSubmit={(event) => {
                          event.preventEventDefault()
                          const code = (codes[grant.id] || {}).code || ''
                          run(
                            () =>
                              crmApi.callback(grant.connection_id, {
                                code,
                                state: grant.state_nonce,
                              }),
                            'Code exchanged and the credential sealed in the vault.'
                          )
                        }}
                      >
                        <div className="min-w-64 flex-1">
                          <Field label="Code from the redirect" id={`wf034-code-${grant.id}`}>
                            <input
                              id={`wf034-code-${grant.id}`}
                              required
                              value={(codes[grant.id] || {}).code || ''}
                              onChange={(event) =>
                                setCodes((current) => ({
                                  ...current,
                                  [grant.id]: { ...(current[grant.id] || {}), code: event.target.value },
                                }))
                              }
                              className={inputClass}
                            />
                          </Field>
                        </div>
                        <Button type="submit" icon="link" variant="primary" disabled={busy}>
                          Exchange the code
                        </Button>
                        <Button
                          icon="trash"
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            run(
                              () => crmApi.cancelGrant(grant.id),
                              'That authorization is abandoned; press Authorize to start another.'
                            )
                          }
                        >
                          Abandon
                        </Button>
                      </form>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ),
          events: (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Which trigger refreshed a token, what the vendor answered, and that a 401 changed
                nothing but the health. A record rather than memory, because the reason a second
                attempt happened is the first attempt.
              </p>
              <div className="max-w-md">
                <Field label="Connection" id="wf034-watch" hint="Whose token lifecycle to show.">
                  <select
                    id="wf034-watch"
                    value={watching || ''}
                    onChange={(event) => setWatching(event.target.value || null)}
                    className={inputClass}
                  >
                    <option value="">Pick a connection</option>
                    {(connections.data?.connections || []).map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.label || row.id}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {!watching ? (
                <EmptyState
                  title="No connection selected"
                  description="Pick one above to read its token lifecycle."
                />
              ) : eventRows.length === 0 ? (
                <EmptyState
                  title="Nothing recorded yet"
                  description="Authorize the connection, or test it, and the attempts land here."
                />
              ) : (
                <ol className="space-y-2">
                  {eventRows.map((event) => (
                    <li key={event.id} className="rounded-lg border border-border-subtle/25 p-3 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={event.kind === 'refresh_refused' ? 'restore' : 'neutral'}>
                          {event.kind}
                        </Badge>
                        <span className="font-mono text-foreground">{relativeTime(event.at)}</span>
                        {event.outcome && (
                          <Badge tone={HEALTH_TONE[event.outcome] || 'neutral'}>{event.outcome}</Badge>
                        )}
                        {event.trigger && <span className="text-muted-foreground">trigger {event.trigger}</span>}
                      </div>
                      <p className="mt-1 text-muted-foreground">{event.detail}</p>
                      {event.request_url && (
                        <p className="mt-1 font-mono break-all text-muted-foreground">
                          {event.method} {event.request_url}
                        </p>
                      )}
                      {event.unauthorized_is_not_a_refresh_trigger && (
                        <p className="mt-1 text-amber-300">
                          The Authorization header was <span className="font-mono">{event.authorization_header}</span>.
                          A 401 changed nothing but the health.
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          ),
          contract: (
            <div className="space-y-4">
              <div>
                <h3 className="font-mono text-xs font-semibold text-foreground">The researched flow</h3>
                <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
                  {(vocabulary.data?.flow || []).map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              </div>
              <div>
                <h3 className="font-mono text-xs font-semibold text-foreground">
                  The sentence this is built around
                </h3>
                <blockquote className="mt-2 border-l-2 border-accent/50 pl-3 text-sm text-foreground/90">
                  {vocabulary.data?.sourced_quotes?.['401_is_not_a_refresh_signal']?.quote}
                </blockquote>
              </div>
              <div>
                <h3 className="font-mono text-xs font-semibold text-foreground">Quoted evidence</h3>
                <ul className="mt-2 space-y-2">
                  {Object.entries(vocabulary.data?.sourced_quotes || {}).map(([id, entry]) => (
                    <li key={id} className="rounded-lg border border-border-subtle/25 p-3 text-xs">
                      <p className="font-mono text-foreground">{id}</p>
                      <p className="mt-1 text-muted-foreground">{entry.quote}</p>
                      <p className="mt-1 text-muted-foreground">
                        <span className="font-mono">{entry.where}</span> — {entry.means}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="font-mono text-xs font-semibold text-foreground">
                  What the research could not read
                </h3>
                <ul className="mt-2 space-y-2">
                  {(vocabulary.data?.gaps || []).map((gap) => (
                    <li key={gap.id} className="rounded-lg border border-amber-500/30 p-3 text-xs">
                      <p className="font-mono text-amber-300">{gap.id}</p>
                      <p className="mt-1 text-muted-foreground">{gap.gap}</p>
                      <p className="mt-1 text-muted-foreground">So: {gap.effect}</p>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="font-mono text-xs font-semibold text-foreground">
                  What this build decided for itself
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {inferences.data?.note} {inferences.data?.constrained_by_a_quote} of{' '}
                  {inferences.data?.count} are constrained by a quote; the rest are named here so a
                  reviewer can disagree with one.
                </p>
                <ul className="mt-2 space-y-2">
                  {Object.entries(inferences.data?.inferences || {}).map(([id, entry]) => (
                    <li key={id} className="rounded-lg border border-border-subtle/25 p-3 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-foreground">{id}</span>
                        {entry.sourced_against ? (
                          <Badge tone="insert">against {entry.sourced_against}</Badge>
                        ) : (
                          <Badge tone="restore">research is silent</Badge>
                        )}
                      </div>
                      <p className="mt-1 text-foreground/90">{entry.decision}</p>
                      <p className="mt-1 text-muted-foreground">{entry.why}</p>
                      <p className="mt-1 text-muted-foreground">
                        <span className="font-mono">change it:</span> {entry.change_how}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="font-mono text-xs font-semibold text-foreground">
                  Adjacent surfaces this does not build
                </h3>
                <ul className="mt-2 space-y-2">
                  {Object.entries(vocabulary.data?.not_implemented || {}).map(([id, entry]) => (
                    <li key={id} className="rounded-lg border border-border-subtle/25 p-3 text-xs">
                      <span className="font-mono text-foreground">{id}</span>
                      <p className="mt-1 text-muted-foreground">researched: {entry.researched}</p>
                      <p className="mt-1 text-muted-foreground">this build: {entry.this_build}</p>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ),
        }
        return (
          <Card key={section.id}>
            <div className="mb-3 flex items-center gap-2">
              <span className="text-accent" aria-hidden="true">
                <Glyph name={section.icon} size={16} />
              </span>
              {heading}
            </div>
            {section.id === 'events' && events.error && (
              <ErrorNote error={events.error} onRetry={events.refetch} />
            )}
            {section.id === 'contract' && vocabulary.error && (
              <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
            )}
            {body[section.id]}
          </Card>
        )
      })}

      <footer className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon name="audit" size={14} />
        <span>
          Every write on this page is audited with the route that served it. No route in this
          feature reads the credential vault, and nothing rendered here is a credential: the
          client secret and the token pair are sealed, and a read reports only whether a sealed
          row exists and which field names it holds.
        </span>
      </footer>
    </div>
  )
}
