/**
 * Seller activity feed: DSR events into the seller's own feed (WF-026).
 *
 * The page is the four researched steps, in the order a seller would do them,
 * plus the two things a person has to read afterwards:
 *
 *   1. the app, with the S2S token the write is authorized with. The token is
 *      never returned by a read, so the row says `has_token` and a masked hint.
 *   2. the configured custom events: the app-scoped `name`, the `template` the
 *      Outreach portal renders the card from, and the optional `body` we send.
 *   3. the room's prospect links, then Preview and Publish - the researched write.
 *   4. what the seller's feed would show, as a chronological clickable trail.
 *
 * Plus the two logs a rep actually reads. The delivery log distinguishes delivered
 * from failed and says which rows will not fix themselves, because the researched
 * platform retries nothing in either direction. The signal log is intent coming
 * back out of Outreach, with its `beforeUpdate` block kept verbatim.
 *
 * Two things this page deliberately makes loud, because a feature that does not
 * fall through is a bug someone hits in production:
 *
 *   - **Blockers.** If a room cannot write anything, the page says exactly which
 *     of the five is missing, rather than showing an empty feed.
 *   - **Unmapped buyer actions.** Buyer actions in the room's event stream that no
 *     enabled custom event claims, counted per action name. "Nobody configured
 *     comments yet" and "comments are configured and broken" read very differently.
 *
 * The pickers are rendered from the vocabulary endpoint rather than from a list
 * compiled into this file, and every form field is free text, so a team that
 * configures a new custom event ships a record rather than a change to this page.
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
import { feedApi } from './api'
import Glyph, { ICON_PATHS } from './icons'
import { Note, StatTile, Toggle } from './primitives'

const SECTIONS = [
  { id: 'setup', label: 'Apps and custom events', icon: 'template' },
  { id: 'prospects', label: 'Who the feed belongs to', icon: 'prospect' },
  { id: 'publish', label: 'Write to the feed', icon: 'outbound' },
  { id: 'cards', label: 'What the seller sees', icon: 'card' },
  { id: 'deliveries', label: 'Outbound log', icon: 'schema' },
  { id: 'signals', label: 'Intent coming back', icon: 'activity' },
  { id: 'contract', label: 'The researched contract', icon: 'audit' },
]

const STATUS_TONE = { delivered: 'insert', failed: 'delete', skipped: 'update' }

/** One configured custom event, with its card preview and its on/off toggle. */
function EventTypeRow({ entry, onToggle, onDelete, busy }) {
  const [open, setOpen] = useState(false)
  const locales = Object.entries(entry.localizations || {})

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <div className="flex flex-wrap items-center gap-3 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] text-foreground">{entry.name}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            <span className="font-mono">{entry.actions.join(', ')}</span>
            {entry.documents.length > 0 && ` · only ${entry.documents.join(', ')}`}
            {entry.body ? ' · with a body' : ' · template only'}
            {locales.length > 0 && ` · ${locales.length} localised`}
          </p>
        </div>
        <Badge tone={entry.configured_in_portal ? 'insert' : 'update'}>
          {entry.configured_in_portal ? 'in portal' : 'not in portal'}
        </Badge>
        <Toggle
          checked={entry.enabled}
          disabled={busy}
          label={`${entry.enabled ? 'Disable' : 'Enable'} the custom event ${entry.name}`}
          onChange={(next) => onToggle(entry, next)}
        />
        <Button
          icon="trash"
          variant="danger"
          disabled={busy}
          onClick={() => onDelete(entry)}
          aria-label={`Delete the custom event ${entry.name}`}
        >
          Delete
        </Button>
      </div>

      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-2 py-1 text-left text-xs text-muted-foreground
          transition-colors duration-150 hover:text-foreground
          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
          focus-visible:ring-offset-background"
      >
        <Glyph name="chevron" size={14} />
        <span>{open ? 'Hide' : 'Show'} the card this produces</span>
      </button>

      {open && (
        <div className="mb-3 space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              Template, as the rep sees it
            </p>
            <p className="mt-1 font-mono text-foreground">{entry.template}</p>
            {locales.map(([locale, text]) => (
              <p key={locale} className="mt-1 font-mono text-foreground/80">
                <span className="text-muted-foreground">{locale}</span> {text}
              </p>
            ))}
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              Body we send with it
            </p>
            <p className="mt-1 font-mono text-foreground">
              {entry.body || <span className="text-muted-foreground">none</span>}
            </p>
          </div>
          <p className="text-muted-foreground">{entry.card_preview.note}</p>
          <JsonView value={{ app: entry.app_identifier, name: entry.name, ...entry.payload_preview }} />
        </div>
      )}
    </li>
  )
}

/** One configured app. The S2S token is never rendered: a read does not return it. */
function AppRow({ app, onToggle, onDelete, busy }) {
  const [open, setOpen] = useState(false)

  return (
    <li className="border-b border-border-subtle/15 last:border-0 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] text-foreground">{app.app_identifier}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            token {app.has_token ? app.token_hint : 'not set'} · secret{' '}
            {app.has_webhook_secret ? 'set' : 'not set'} · base {app.room_base_url || 'not set'}
            {app.environment ? ` · ${app.environment}` : ''}
          </p>
        </div>
        <Badge tone={app.has_token ? 'insert' : 'delete'}>
          {app.has_token ? 'ready' : 'no token'}
        </Badge>
        <Toggle
          checked={app.enabled}
          disabled={busy}
          label={`${app.enabled ? 'Disable' : 'Enable'} the app ${app.app_identifier}`}
          onChange={(next) => onToggle(app, next)}
        />
        <Button
          icon="trash"
          variant="danger"
          disabled={busy}
          onClick={() => onDelete(app)}
          aria-label={`Delete the app ${app.app_identifier}`}
        >
          Delete
        </Button>
      </div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="mt-1 flex min-h-11 items-center gap-2 text-left text-xs text-muted-foreground
          transition-colors duration-150 hover:text-foreground
          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
          focus-visible:ring-offset-background"
      >
        <Glyph name="chevron" size={14} />
        <span>Credentials and field map</span>
      </button>
      {open && (
        <div className="mt-1 space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <p className="text-muted-foreground">
            The S2S token is the <code className="font-mono">Authorization</code> header on the
            write, so it is never returned by a read and the recorded headers on a delivery row have
            it redacted.
          </p>
          <JsonView value={app} />
        </div>
      )}
    </li>
  )
}

/** One delivery row, expanding to the payload and every attempt that produced it. */
function DeliveryRow({ row, onRetry, busy }) {
  const [open, setOpen] = useState(false)
  const failed = row.status === 'failed'

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <div className="flex flex-wrap items-center gap-3 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] text-foreground">
            {row.event_name}
            {row.prospect_id && (
              <span className="text-muted-foreground"> → {row.prospect_id}</span>
            )}
          </p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {row.source_action || '—'} · {relativeTime(row.occurred_at)}
            {row.http_status != null && ` · HTTP ${row.http_status}`}
            {` · ${row.attempts} attempt${row.attempts === 1 ? '' : 's'}`}
            {row.runs > 1 && ` · ${row.runs} runs`}
          </p>
          {row.status === 'skipped' && row.skip_detail && (
            <p className="mt-0.5 truncate text-xs text-amber-300">{row.skip_detail}</p>
          )}
        </div>
        <Badge tone={STATUS_TONE[row.status] || 'neutral'}>{row.status}</Badge>
        {row.needs_manual_update && <Badge tone="delete">needs a human</Badge>}
        <Button
          icon="refresh"
          disabled={busy}
          onClick={() => onRetry(row)}
          aria-label={`Send ${row.event_name} again`}
        >
          Send again
        </Button>
      </div>

      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-2 text-left text-xs text-muted-foreground
          transition-colors duration-150 hover:text-foreground
          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
          focus-visible:ring-offset-background"
      >
        <Glyph name="chevron" size={14} />
        <span>{open ? 'Hide' : 'Show'} the payload and every attempt</span>
      </button>

      {open && (
        <div className="mb-3 space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          {row.external_url && (
            <a
              href={row.external_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-11 items-center gap-2 font-mono text-accent underline
                underline-offset-4 focus-visible:ring-2 focus-visible:ring-accent
                focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <Glyph name="link" size={14} />
              <span>Open the room link the card carries</span>
            </a>
          )}
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              Every attempt, in order
            </p>
            {row.attempt_log.length === 0 ? (
              <p className="mt-1 text-muted-foreground">
                None. The event never reached the network.
              </p>
            ) : (
              <ul className="mt-1 space-y-1">
                {row.attempt_log.map((attempt) => (
                  <li key={attempt.attempt} className="flex flex-wrap items-baseline gap-2">
                    <span className="font-mono text-muted-foreground">#{attempt.attempt}</span>
                    <Badge tone={attempt.ok ? 'insert' : 'delete'}>
                      {attempt.status ?? 'no status'}
                    </Badge>
                    <span className="font-mono text-muted-foreground">
                      {attempt.duration_ms}ms{attempt.retryable ? ' · retryable' : ''}
                    </span>
                    {attempt.error && (
                      <span className="font-mono text-destructive">{attempt.error}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              The JSON:API event create that went out
            </p>
            <JsonView value={row.payload} />
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              Headers, with the token redacted
            </p>
            <JsonView value={row.request_headers} />
          </div>
          {failed && (
            <Note tone="warn">
              Nothing will retry this for us, so it waits for a person. Fix the prospect, then use
              Send again.
            </Note>
          )}
        </div>
      )}
    </li>
  )
}

/** One inbound signal, expanding to the delivery that carried it. */
function SignalRow({ signal }) {
  const [open, setOpen] = useState(false)

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <div className="flex flex-wrap items-center gap-3 py-3">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] text-foreground">
            {signal.resource}.{signal.type}
            {signal.mailing_id && <span className="text-muted-foreground"> · {signal.mailing_id}</span>}
          </p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {absoluteTime(signal.occurred_at)}
            {signal.prospect_id && ` · prospect ${signal.prospect_id}`}
            {` · payloadVersion ${signal.payload_version}`}
          </p>
          {signal.ignore_reason && (
            <p className="mt-0.5 truncate text-xs text-amber-300">{signal.ignore_reason}</p>
          )}
        </div>
        {signal.intent && <Badge tone="insert">intent</Badge>}
        <Badge tone={signal.status === 'ignored' ? 'update' : 'neutral'}>{signal.status}</Badge>
        <Badge tone={signal.link_status === 'linked' ? 'insert' : 'delete'}>{signal.link_status}</Badge>
      </div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-2 text-left text-xs text-muted-foreground
          transition-colors duration-150 hover:text-foreground
          focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
          focus-visible:ring-offset-background"
      >
        <Glyph name="chevron" size={14} />
        <span>
          {open ? 'Hide' : 'Show'} the delivery, including its beforeUpdate block
        </span>
      </button>
      {open && (
        <div className="mb-3 space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">beforeUpdate</p>
            {signal.before_update === null || signal.before_update === undefined ? (
              <p className="mt-1 text-muted-foreground">This delivery carried none.</p>
            ) : (
              <JsonView value={signal.before_update} />
            )}
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Attributes</p>
            <JsonView value={signal.attributes} />
          </div>
        </div>
      )}
    </li>
  )
}

/** One inferred behaviour: what it is, what the research says, and how to change it. */
function InferenceRow({ entry }) {
  const [open, setOpen] = useState(false)

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors
          duration-150 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-accent
          focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {entry.topic}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{entry.id}</span>
      </button>
      {open && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Why</p>
            <p className="mt-0.5 text-foreground/90">{entry.why}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What the research says
            </p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What this build chose
            </p>
            <JsonView value={entry.value} />
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              How to change it
            </p>
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

export default function SellerActivityFeed() {
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState(null)
  const [ledger, setLedger] = useState(null)
  const [deliveryFilter, setDeliveryFilter] = useState('all')
  const [appForm, setAppForm] = useState({
    app_identifier: '',
    token: '',
    webhook_secret: '',
    room_base_url: '',
    environment: '',
  })
  const [typeForm, setTypeForm] = useState({
    name: '',
    template: '{{prospect}} ',
    body: '',
    actions: '',
    documents: '',
  })
  const [linkForm, setLinkForm] = useState({ prospect_id: '', label: '', opportunity_id: '', account_id: '' })

  const rooms = useAsync(() => apiRequest('/records/room?limit=200&order_by=name'), [])
  const apps = useAsync(() => feedApi.listApps(), [])
  const types = useAsync(() => feedApi.listEventTypes(), [])
  const vocabulary = useAsync(() => feedApi.vocabulary(), [])
  const inferences = useAsync(() => feedApi.inferences(), [])
  const prospects = useAsync(
    () => (roomId ? feedApi.listProspects(roomId) : Promise.resolve({ prospects: [] })),
    [roomId]
  )
  const feed = useAsync(
    () => (roomId ? feedApi.roomFeed(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const deliveries = useAsync(
    () =>
      feedApi.listDeliveries({
        room_id: roomId || undefined,
        status: deliveryFilter === 'all' ? undefined : deliveryFilter,
        limit: 100,
      }),
    [roomId, deliveryFilter]
  )
  const signals = useAsync(() => feedApi.listSignals({ room_id: roomId || undefined, limit: 100 }), [roomId])

  const refreshAll = useCallback(() => {
    apps.refetch()
    types.refetch()
    prospects.refetch()
    feed.refetch()
    deliveries.refetch()
    signals.refetch()
  }, [apps, types, prospects, feed, deliveries, signals])

  const report = useCallback((tone, message) => {
    setFlash({ tone, message })
  }, [])

  const run = useCallback(
    async (action, success) => {
      setBusy(true)
      setFlash(null)
      try {
        await action()
        report('good', success)
        refreshAll()
      } catch (error) {
        report(error?.status === 428 ? 'warn' : 'info', String(error?.message || error))
      } finally {
        setBusy(false)
      }
    },
    [refreshAll, report]
  )

  const roomList = useMemo(() => rooms.data?.records || [], [rooms.data])
  const view = feed.data
  const counts = view?.counts || { delivered: 0, failed: 0, skipped: 0 }
  const deliveryRows = deliveries.data?.deliveries || []
  const signalRows = signals.data?.signals || []
  const appList = apps.data?.apps || []
  const typeList = types.data?.event_types || []
  const prospectList = prospects.data?.prospects || []
  const appIdentifiers = appList.map((app) => app.app_identifier)

  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (rooms.loading) return <Spinner label="Loading the seller activity feed" />

  return (
    <div className="space-y-6">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-xl font-semibold text-foreground">Seller activity feed</h1>
          <Badge tone="update">WF-026</Badge>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          DSR engagement events mapped onto configured Outreach custom events, POSTed to the
          prospect activity feed with a deep link back into the room. On the other side, the
          intent that comes back out through Outreach webhooks.
        </p>
        <div className="max-w-md">
          <Field
            label="Room"
            id="wf026-room"
            hint="Every write and every feed card is scoped to one room."
          >
            <select
              id="wf026-room"
              value={roomId}
              onChange={(event) => {
                setRoomId(event.target.value)
                setLedger(null)
              }}
              className={inputClass}
            >
              <option value="">All rooms — the log is unscoped</option>
              {roomList.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {flash && <Note tone={flash.tone}>{flash.message}</Note>}
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Apps"
          value={appList.length}
          hint={appList.filter((app) => app.has_token && app.enabled).length + ' ready to write'}
          path="template"
        />
        <StatTile
          label="Custom events"
          value={typeList.length}
          hint={typeList.filter((entry) => entry.enabled).length + ' enabled'}
          path="card"
        />
        <StatTile
          label="Delivered"
          value={counts.delivered}
          hint={roomId ? 'this room' : 'pick a room to scope'}
          path="outbound"
        />
        <StatTile
          label="Needs a human"
          value={counts.failed}
          hint="Outreach retries nothing in either direction"
          path="warning"
        />
      </section>

      {view && !view.ready && (
        <Card className="border-amber-500/40">
          <h2 className="font-mono text-sm font-semibold text-foreground">
            This room cannot write anything yet
          </h2>
          <ul className="mt-2 space-y-1">
            {view.blockers.map((blocker) => (
              <li key={`${blocker.code}-${blocker.detail}`} className="flex items-start gap-2 text-sm">
                <Badge tone="delete">{blocker.code}</Badge>
                <span className="text-muted-foreground">{blocker.detail}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">
            Nothing is being dropped: each of these events is recorded against the delivery log with
            its reason, and re-running Publish once the blocker is cleared sends exactly what was
            waiting.
          </p>
        </Card>
      )}

      {view && view.unmapped_actions.length > 0 && (
        <Card>
          <h2 className="font-mono text-sm font-semibold text-foreground">
            Buyer actions with no custom event
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            In this room&apos;s event stream, nobody has configured a custom event for these. They
            are counted rather than written to the log, so &ldquo;not configured yet&rdquo; does not
            read like a broken integration.
          </p>
          <ul className="mt-3 space-y-1">
            {view.unmapped_actions.map((row) => (
              <li key={row.action} className="flex items-center gap-3 text-sm">
                <span className="font-mono text-foreground">{row.action}</span>
                <Badge tone="update">{row.count} event{row.count === 1 ? '' : 's'}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {SECTIONS.map((section) => {
        const body = {
          setup: (
            <div className="space-y-4">
              <Card>
                <h2 className="font-mono text-sm font-semibold text-foreground">
                  1. The app the custom events are configured under
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  The researched step happens in the Outreach developer portal. What is recorded
                  here is the identity and the S2S token the write is authorized with, so a
                  configured event name can be checked against a real app.
                </p>
                {appList.length === 0 ? (
                  <EmptyState
                    title="No app registered"
                    description="Register one to go any further: an event name is app-scoped, so nothing can be configured without one."
                  />
                ) : (
                  <ul className="mt-3">
                    {appList.map((app) => (
                      <AppRow
                        key={app.id}
                        app={app}
                        busy={busy}
                        onToggle={(row, next) =>
                          run(
                            () => feedApi.updateApp(row.id, { enabled: next }),
                            `${row.app_identifier} is now ${next ? 'on' : 'off'}.`
                          )
                        }
                        onDelete={(row) =>
                          run(
                            () => feedApi.deleteApp(row.id),
                            `App ${row.app_identifier} removed. Its delivery log stays.`
                          )
                        }
                      />
                    ))}
                  </ul>
                )}
                <form
                  className="mt-4 grid gap-3 sm:grid-cols-2"
                  onSubmit={(event) => {
                    event.preventDefault()
                    run(
                      () =>
                        feedApi.registerApp({
                          ...appForm,
                          enabled: true,
                          field_map: undefined,
                        }),
                      `App ${appForm.app_identifier} registered.`
                    )
                    setAppForm({
                      app_identifier: '',
                      token: '',
                      webhook_secret: '',
                      room_base_url: '',
                      environment: '',
                    })
                  }}
                >
                  <Field label="App identifier" id="wf026-app-id" hint="The prefix of every event name.">
                    <input
                      id="wf026-app-id"
                      required
                      value={appForm.app_identifier}
                      onChange={(event) => setAppForm({ ...appForm, app_identifier: event.target.value })}
                      placeholder="dsr"
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Environment" id="wf026-app-env" hint="Free text. Shown on the row.">
                    <input
                      id="wf026-app-env"
                      value={appForm.environment}
                      onChange={(event) => setAppForm({ ...appForm, environment: event.target.value })}
                      placeholder="production"
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="S2S token"
                    id="wf026-app-token"
                    hint="Sent as Authorization: Bearer. Never returned by a read."
                  >
                    <input
                      id="wf026-app-token"
                      required
                      type="password"
                      autoComplete="off"
                      value={appForm.token}
                      onChange={(event) => setAppForm({ ...appForm, token: event.target.value })}
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="Webhook signing secret"
                    id="wf026-app-secret"
                    hint="Needed to verify an inbound delivery. Leave blank if you have no subscription."
                  >
                    <input
                      id="wf026-app-secret"
                      type="password"
                      autoComplete="off"
                      value={appForm.webhook_secret}
                      onChange={(event) => setAppForm({ ...appForm, webhook_secret: event.target.value })}
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="Room base URL"
                    id="wf026-app-base"
                    hint="The deep link is this plus the room id. Without it, no event can carry an externalUrl."
                  >
                    <input
                      id="wf026-app-base"
                      value={appForm.room_base_url}
                      onChange={(event) => setAppForm({ ...appForm, room_base_url: event.target.value })}
                      placeholder="https://rooms.example/r"
                      className={inputClass}
                    />
                  </Field>
                  <div className="flex items-end">
                    <Button type="submit" variant="primary" icon="plus" disabled={busy}>
                      Register app
                    </Button>
                  </div>
                </form>
              </Card>

              <Card>
                <h2 className="font-mono text-sm font-semibold text-foreground">
                  2. The custom events it claims
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  A name is app-scoped: <code className="font-mono">app:event</code>, and the app
                  half has to be one registered above, because Outreach matches it to the app whose
                  custom event was configured in the portal. The template is configured in that
                  portal and never sent; only the name, the deep link and the optional body go over
                  the wire.
                </p>
                {typeList.length === 0 ? (
                  <EmptyState
                    title="No custom event configured"
                    description="Without one, no DSR event qualifies and every buyer action is reported as unmapped."
                  />
                ) : (
                  <ul className="mt-3">
                    {typeList.map((entry) => (
                      <EventTypeRow
                        key={entry.id}
                        entry={entry}
                        busy={busy}
                        onToggle={(row, next) =>
                          run(
                            () => feedApi.updateEventType(row.id, { enabled: next }),
                            `${row.name} is now ${next ? 'on' : 'off'}.`
                          )
                        }
                        onDelete={(row) =>
                          run(
                            () => feedApi.deleteEventType(row.id),
                            `${row.name} removed. Its delivery log stays.`
                          )
                        }
                      />
                    ))}
                  </ul>
                )}
                <form
                  className="mt-4 grid gap-3 sm:grid-cols-2"
                  onSubmit={(event) => {
                    event.preventDefault()
                    run(
                      () =>
                        feedApi.createEventType({
                          name: typeForm.name,
                          template: typeForm.template,
                          body: typeForm.body,
                          actions: typeForm.actions,
                          documents: typeForm.documents,
                          enabled: true,
                        }),
                      `Custom event ${typeForm.name} configured.`
                    )
                    setTypeForm({ name: '', template: '{{prospect}} ', body: '', actions: '', documents: '' })
                  }}
                >
                  <Field
                    label="Event name"
                    id="wf026-type-name"
                    hint={
                      appIdentifiers.length > 0
                        ? `app:event — one of ${appIdentifiers.join(', ')}`
                        : 'app:event — register an app first'
                    }
                  >
                    <input
                      id="wf026-type-name"
                      required
                      value={typeForm.name}
                      onChange={(event) => setTypeForm({ ...typeForm, name: event.target.value })}
                      placeholder="dsr:room-viewed"
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="Buyer actions it claims"
                    id="wf026-type-actions"
                    hint="Comma separated, matched against the room's event stream. At least one."
                  >
                    <input
                      id="wf026-type-actions"
                      required
                      value={typeForm.actions}
                      onChange={(event) => setTypeForm({ ...typeForm, actions: event.target.value })}
                      placeholder="viewed, opened_link"
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="Card template"
                    id="wf026-type-template"
                    hint="Configured in the portal, never sent. {{prospect}} is the only placeholder Outreach replaces."
                  >
                    <input
                      id="wf026-type-template"
                      required
                      value={typeForm.template}
                      onChange={(event) => setTypeForm({ ...typeForm, template: event.target.value })}
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="Body we send"
                    id="wf026-type-body"
                    hint="Optional accompanying text. Leave blank and the card shows the template alone."
                  >
                    <input
                      id="wf026-type-body"
                      value={typeForm.body}
                      onChange={(event) => setTypeForm({ ...typeForm, body: event.target.value })}
                      placeholder="Opened the room"
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="Only these documents"
                    id="wf026-type-documents"
                    hint="Optional. Narrow the match to particular targets."
                  >
                    <input
                      id="wf026-type-documents"
                      value={typeForm.documents}
                      onChange={(event) => setTypeForm({ ...typeForm, documents: event.target.value })}
                      placeholder="Pricing One-Pager"
                      className={inputClass}
                    />
                  </Field>
                  <div className="flex items-end">
                    <Button type="submit" variant="primary" icon="plus" disabled={busy}>
                      Configure custom event
                    </Button>
                  </div>
                </form>
              </Card>
            </div>
          ),

          prospects: (
            <Card>
              <h2 className="font-mono text-sm font-semibold text-foreground">
                Who this room&apos;s feed belongs to
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Every event write carries a <code className="font-mono">prospect</code>{' '}
                relationship, so a room with no link can send nothing — which the log records with a
                reason rather than swallowing. A room may hold several links: one DSR event then
                produces one card per prospect.
              </p>
              {!roomId ? (
                <EmptyState title="Pick a room" description="Choose a room above to manage its links." />
              ) : prospectList.length === 0 ? (
                <EmptyState
                  title="This room is not linked"
                  description="Nothing can be written to the seller's feed until it names the prospect it belongs to."
                />
              ) : (
                <ul className="mt-3">
                  {prospectList.map((link) => (
                    <li
                      key={link.id}
                      className="flex flex-wrap items-center gap-3 border-b border-border-subtle/15 py-3 last:border-0"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-[13px] text-foreground">
                          <Glyph name="prospect" size={14} /> {link.prospect_id}
                        </p>
                        <p className="mt-0.5 truncate text-xs text-muted-foreground">
                          {link.label || 'no label'}
                          {link.opportunity_id && ` · opportunity ${link.opportunity_id}`}
                          {link.account_id && ` · account ${link.account_id}`}
                          {link.external_url ? ` · own link ${link.external_url}` : ''}
                        </p>
                      </div>
                      <Badge tone={link.active ? 'insert' : 'update'}>
                        {link.active ? 'active' : 'inactive'}
                      </Badge>
                      <Button
                        icon="trash"
                        variant="danger"
                        disabled={busy}
                        onClick={() =>
                          run(
                            () => feedApi.unlinkProspect(roomId, link.id),
                            `Unlinked ${link.prospect_id}.`
                          )
                        }
                        aria-label={`Unlink prospect ${link.prospect_id}`}
                      >
                        Unlink
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              {roomId && (
                <form
                  className="mt-4 grid gap-3 sm:grid-cols-2"
                  onSubmit={(event) => {
                    event.preventDefault()
                    run(
                      () => feedApi.linkProspect(roomId, linkForm),
                      `Linked ${linkForm.prospect_id} to this room.`
                    )
                    setLinkForm({ prospect_id: '', label: '', opportunity_id: '', account_id: '' })
                  }}
                >
                  <Field
                    label="Prospect id"
                    id="wf026-link-prospect"
                    hint="The id of the prospect object in the seller platform."
                  >
                    <input
                      id="wf026-link-prospect"
                      required
                      value={linkForm.prospect_id}
                      onChange={(event) => setLinkForm({ ...linkForm, prospect_id: event.target.value })}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Label" id="wf026-link-label" hint="Who this contact is. Shown on the row.">
                    <input
                      id="wf026-link-label"
                      value={linkForm.label}
                      onChange={(event) => setLinkForm({ ...linkForm, label: event.target.value })}
                      placeholder="Procurement lead"
                      className={inputClass}
                    />
                  </Field>
                  <Field
                    label="Opportunity id"
                    id="wf026-link-opp"
                    hint="The account/opportunity behind the prospect. Context only."
                  >
                    <input
                      id="wf026-link-opp"
                      value={linkForm.opportunity_id}
                      onChange={(event) => setLinkForm({ ...linkForm, opportunity_id: event.target.value })}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Account id" id="wf026-link-acct" hint="Context only.">
                    <input
                      id="wf026-link-acct"
                      value={linkForm.account_id}
                      onChange={(event) => setLinkForm({ ...linkForm, account_id: event.target.value })}
                      className={inputClass}
                    />
                  </Field>
                  <div className="flex items-end">
                    <Button type="submit" variant="primary" icon="plus" disabled={busy || !roomId}>
                      Link prospect
                    </Button>
                  </div>
                </form>
              )}
            </Card>
          ),

          publish: (
            <Card>
              <h2 className="font-mono text-sm font-semibold text-foreground">
                3. Write the qualifying events
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                A sweep over the room&apos;s event stream, one delivery per qualifying event, per
                configured custom event, per linked prospect. Safe to run repeatedly: anything
                already delivered is not sent again, and anything blocked is re-evaluated rather
                than remembered as blocked.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  icon="search"
                  disabled={!roomId || busy}
                  onClick={() =>
                    run(async () => {
                      setLedger(await feedApi.preview(roomId))
                    }, 'Preview only. Nothing was written.')
                  }
                >
                  Preview
                </Button>
                <Button
                  variant="primary"
                  icon="outbound"
                  disabled={!roomId || busy}
                  onClick={() =>
                    run(async () => {
                      setLedger(await feedApi.publish(roomId))
                    }, 'Publish finished. See the ledger below.')
                  }
                >
                  Publish to the seller feed
                </Button>
              </div>
              {!roomId && (
                <p className="mt-2 text-xs text-muted-foreground">Pick a room above first.</p>
              )}
              {ledger && (
                <div className="mt-4 space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge tone="insert">{ledger.counts.sent} sent</Badge>
                    <Badge tone="delete">{ledger.counts.failed} failed</Badge>
                    <Badge tone="update">{ledger.counts.skipped} skipped</Badge>
                    <Badge tone="neutral">{ledger.counts.duplicate} already sent</Badge>
                    <Badge tone="neutral">
                      {ledger.counts.unmapped_events} events, {ledger.counts.unmapped_actions} actions, unmapped
                    </Badge>
                  </div>
                  {ledger.dry_run && <Note tone="info">This was a preview. Nothing was written.</Note>}
                  {ledger.sent.length > 0 && (
                    <LedgerTable title="Sent" rows={ledger.sent} />
                  )}
                  {ledger.failed.length > 0 && <LedgerTable title="Failed" rows={ledger.failed} />}
                  {ledger.skipped.length > 0 && <LedgerTable title="Skipped" rows={ledger.skipped} />}
                  {ledger.duplicate.length > 0 && (
                    <LedgerTable title="Already delivered, not resent" rows={ledger.duplicate} />
                  )}
                </div>
              )}
            </Card>
          ),

          cards: (
            <Card>
              <h2 className="font-mono text-sm font-semibold text-foreground">
                4. What the seller would see
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                The chronological, clickable trail, assembled from the delivery log. The template half
                is rendered by Outreach from the portal configuration; what is shown here is the
                configured name and the body this build sends.
              </p>
              {!roomId ? (
                <EmptyState title="Pick a room" description="Cards are per room." />
              ) : !view || view.cards.length === 0 ? (
                <EmptyState
                  title="Nothing has reached the feed yet"
                  description="Publish, or fix a blocker above, and the cards appear here."
                />
              ) : (
                <ul className="mt-3">
                  {view.cards.map((card) => (
                    <li
                      key={card.delivery_id}
                      className="flex flex-wrap items-center gap-3 border-b border-border-subtle/15 py-3 last:border-0"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-[13px] text-foreground">
                          {card.event_name}
                          {card.body && <span className="text-muted-foreground"> · {card.body}</span>}
                        </p>
                        <p className="mt-0.5 truncate text-xs text-muted-foreground">
                          {card.person || 'anonymous'} {card.action || ''}
                          {card.target ? ` · ${card.target}` : ''} · {relativeTime(card.occurred_at)}
                        </p>
                        {card.superseded.length > 0 && (
                          <p className="mt-0.5 truncate text-xs text-muted-foreground">
                            Supersedes {card.superseded.length} earlier blocked row
                            {card.superseded.length === 1 ? '' : 's'}.
                          </p>
                        )}
                      </div>
                      <Badge tone={STATUS_TONE[card.status] || 'neutral'}>{card.status}</Badge>
                      {card.external_url && (
                        <a
                          href={card.externalUrl || card.external_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-sm
                            text-accent underline underline-offset-4 transition-colors duration-150
                            hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-accent
                            focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                        >
                          <Glyph name="link" size={14} />
                          <span>Open the room</span>
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          ),

          deliveries: (
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-mono text-sm font-semibold text-foreground">
                  Outbound log: every write, and which rows will not fix themselves
                </h2>
                <div className="flex items-center gap-2">
                  <label htmlFor="wf026-delivery-filter" className="text-xs text-muted-foreground">
                    Status
                  </label>
                  <select
                    id="wf026-delivery-filter"
                    value={deliveryFilter}
                    onChange={(event) => setDeliveryFilter(event.target.value)}
                    className={`${inputClass} min-w-40`}
                  >
                    <option value="all">All</option>
                    <option value="delivered">Delivered</option>
                    <option value="failed">Failed</option>
                    <option value="skipped">Skipped</option>
                  </select>
                </div>
              </div>
              {deliveries.error ? (
                <ErrorNote error={deliveries.error} onRetry={deliveries.refetch} />
              ) : deliveryRows.length === 0 ? (
                <EmptyState title="No deliveries yet" description="Publish a room to fill this." />
              ) : (
                <ul className="mt-3">
                  {deliveryRows.map((row) => (
                    <DeliveryRow
                      key={row.id}
                      row={row}
                      busy={busy}
                      onRetry={(target) =>
                        run(
                          () => feedApi.retryDelivery(target.id),
                          `Sent ${target.event_name} again. Earlier attempts were kept.`
                        )
                      }
                    />
                  ))}
                </ul>
              )}
            </Card>
          ),

          signals: (
            <Card>
              <h2 className="font-mono text-sm font-semibold text-foreground">
                Intent coming back out of Outreach
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                The researched <code className="font-mono">mailing</code> webhook resources carry
                opens, replies, deliveries and bounces. Outreach does not retry these deliveries, so
                anything readable is stored and acknowledged — including a resource outside the
                documented family, which is recorded as ignored rather than refused.
              </p>
              {signals.error ? (
                <ErrorNote error={signals.error} onRetry={signals.refetch} />
              ) : signalRows.length === 0 ? (
                <EmptyState
                  title="No signals recorded"
                  description="Point a payloadVersion 2 subscription at the receiving route, with its signing secret on the app."
                />
              ) : (
                <ul className="mt-3">
                  {signalRows.map((signal) => (
                    <SignalRow key={signal.id} signal={signal} />
                  ))}
                </ul>
              )}
            </Card>
          ),

          contract: (
            <div className="space-y-4">
              <Card>
                <h2 className="font-mono text-sm font-semibold text-foreground">
                  What the research fixes, and what this build infers
                </h2>
                {vocabulary.loading || inferences.loading ? (
                  <Spinner label="Loading the researched contract" />
                ) : vocabulary.error ? (
                  <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
                ) : (
                  <div className="mt-3 space-y-4 text-sm">
                    <div>
                      <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
                        The write
                      </p>
                      <p className="mt-1 font-mono text-foreground">
                        {vocabulary.data.write.method} {vocabulary.data.write.endpoint}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {vocabulary.data.write.sourced_quote}
                      </p>
                      <JsonView value={vocabulary.data.write.example_body} />
                    </div>
                    <div>
                      <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
                        The webhook subscription to create
                      </p>
                      <JsonView value={vocabulary.data.subscription} />
                    </div>
                    <div>
                      <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
                        Related surfaces this build did not implement
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {vocabulary.data.adjacent_surfaces.note}
                      </p>
                      {[
                        ...vocabulary.data.adjacent_surfaces.outreach,
                        ...vocabulary.data.adjacent_surfaces.salesloft,
                      ].map((entry) => (
                        <div key={entry.name || entry.endpoint} className="mt-2 rounded-lg border border-border-subtle/25 p-3 text-xs">
                          <p className="font-mono text-foreground">{entry.name || entry.endpoint}</p>
                          <p className="mt-1 text-muted-foreground">{entry.description}</p>
                          <p className="mt-1 text-foreground/80">{entry.why_not}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </Card>

              <Card>
                <h2 className="font-mono text-sm font-semibold text-foreground">
                  Every judgement call this workflow rests on
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  The research is specific about the wire contract and silent about almost everything
                  around it. Each entry below is named so it can be argued with by name, says what
                  the research does and does not say, and says how to change it.
                </p>
                {inferences.data && (
                  <ul className="mt-3">
                    {inferences.data.inferences.map((entry) => (
                      <InferenceRow key={entry.id} entry={entry} />
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          ),
        }[section.id]

        return (
          <section key={section.id} className="space-y-2">
            <h2 className="flex items-center gap-2 font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              <Icon name="schema" size={14} path={ICON_PATHS[section.icon]} />
              {section.label}
            </h2>
            {body}
          </section>
        )
      })}
    </div>
  )
}

/** The rows of one publish ledger bucket, so sent / failed / skipped read alike. */
function LedgerTable({ title, rows }) {
  return (
    <div>
      <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">{title}</p>
      <ul className="mt-1 space-y-1">
        {rows.map((row) => (
          <li key={`${row.event_key}-${row.prospect_id || row.reason || ''}`} className="flex flex-wrap items-baseline gap-2 text-xs">
            <span className="font-mono text-foreground">{row.event_name}</span>
            {row.prospect_id && <span className="font-mono text-muted-foreground">→ {row.prospect_id}</span>}
            {row.source_action && <span className="text-muted-foreground">({row.source_action})</span>}
            {row.http_status != null && <Badge tone="neutral">HTTP {row.http_status}</Badge>}
            {row.attempts > 1 && <Badge tone="update">{row.attempts} attempts</Badge>}
            {(row.reason || row.detail) && (
              <span className="text-amber-300">{row.reason ? `${row.reason}: ` : ''}{row.detail}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
