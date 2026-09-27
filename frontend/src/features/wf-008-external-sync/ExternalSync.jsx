import { useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { relativeTime } from '@/lib/api'

import { describeError, externalApi, formatBytes, isRateLimited } from './api'
import { Checkbox, Glyph, Notice } from './primitives'

/**
 * External content library (WF-008).
 *
 * The flow this page drives is the researched one: a caller must have a
 * connection for the source, then adds a file by its source id into a target
 * folder, and `autoSync` decides whether the library item follows the source
 * from then on or is a one-time snapshot.
 *
 * Two things the page is careful about:
 *
 * * It never claims a link is current when the store cannot know. "In sync"
 *   means "matches the version it last applied"; drift shows up in the re-sync
 *   report, and the copy says so rather than implying a live check happened.
 * * Failures are shown with their remedy. The API answers every failure with a
 *   remediation and a correlation id, and the server composes both into the
 *   message this client receives, so the sentence on screen is the whole answer
 *   rather than a generic "something went wrong".
 */

const STATE_TONES = { in_sync: 'insert', orphaned: 'delete', not_external: 'neutral' }
const LINKAGE_TONES = { linked: 'update', snapshot: 'restore' }

function ConnectionPanel({ sources, connections, onConnected, error, onRetry }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ name: '', account: '' })
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const connected = (sources || []).filter((source) => source.connected)

  async function connect(event) {
    event.preventDefault()
    setSaving(true)
    setSaveError(null)
    try {
      // A connection is an ordinary schema-flexible record, so this goes through
      // the core generic route. No dedicated endpoint, no migration.
      await externalApi.createConnection(
        {
          source: connected[0]?.name || sources[0]?.name,
          name: form.name,
          account: form.account,
          status: 'connected',
        },
        { actor: form.account || 'ui' },
      )
      setForm({ name: '', account: '' })
      setOpen(false)
      onConnected()
    } catch (caught) {
      setSaveError(caught)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Source connections</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            A file can only be added from a source this deployment has a connection for. The
            research records one such source today; more can be registered.
          </p>
        </div>
        <Button icon={open ? 'close' : 'plus'} onClick={() => setOpen((value) => !value)}>
          {open ? 'Cancel' : 'Add connection'}
        </Button>
      </div>

      {error && (
        <div className="mt-4">
          <ErrorNote error={error} onRetry={onRetry} />
        </div>
      )}

      {!error && (
        <ul className="mt-4 space-y-2">
          {(sources || []).map((source) => (
            <li
              key={source.name}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-subtle/25 px-3 py-2.5"
            >
              <div className="flex min-w-0 items-center gap-3">
                <span className="rounded-lg bg-muted p-2 text-accent">
                  <Glyph name="cloud" size={18} />
                </span>
                <div className="min-w-0">
                  <p className="truncate font-mono text-sm text-foreground">{source.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {source.connections} connection{source.connections === 1 ? '' : 's'} configured
                  </p>
                </div>
              </div>
              <Badge tone={source.connected ? 'insert' : 'delete'}>
                {source.connected ? 'connected' : 'not connected'}
              </Badge>
            </li>
          ))}
        </ul>
      )}

      {connected.length === 0 && !error && (
        <div className="mt-4">
          <Notice tone="warning" title="No connection configured" icon="alert">
            Adding a file fails with <span className="font-mono">ExternalConnectionNotFound</span> until
            one exists. Connect an account to continue.
          </Notice>
        </div>
      )}

      {open && (
        <form onSubmit={connect} className="mt-4 space-y-4 border-t border-border-subtle/25 pt-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Connection name" id="connection-name" hint="Shown to whoever reads the audit log.">
              <input
                id="connection-name"
                className={inputClass}
                required
                value={form.name}
                placeholder="Dana's Google Drive"
                onChange={(event) => setForm({ ...form, name: event.target.value })}
              />
            </Field>
            <Field label="Account" id="connection-account" hint="The cloud account this stands for.">
              <input
                id="connection-account"
                className={inputClass}
                value={form.account}
                placeholder="dana@northwind.example"
                onChange={(event) => setForm({ ...form, account: event.target.value })}
              />
            </Field>
          </div>
          {saveError && (
            <Notice tone="danger" title="Could not save the connection" icon="alert">
              {describeError(saveError)}
            </Notice>
          )}
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? 'Saving…' : 'Save connection'}
          </Button>
        </form>
      )}

      {(connections || []).length > 0 && (
        <p className="mt-4 text-xs text-muted-foreground">
          {(connections || [])
            .map((entry) => `${entry.external_system_connection_name} (${entry.record.data.account || 'no account'})`)
            .join(' · ')}
        </p>
      )}
    </Card>
  )
}

function AddForm({ sources, rooms, onAdded, actor }) {
  const [roomId, setRoomId] = useState('')
  const [form, setForm] = useState({ externalContentId: '', parentFolderId: 'root', autoSync: true })
  const [metadata, setMetadata] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const roomOptions = rooms || []
  const targetRoom = roomId || roomOptions[0]?.id || ''

  // Folders belong to a room, so the picker follows the room selection rather
  // than offering folders the item could not legally be placed in.
  const folders = useAsync(
    () => (targetRoom ? externalApi.folders(targetRoom) : Promise.resolve({ folders: [] })),
    [targetRoom],
  )
  const folderOptions = folders.data?.folders || []

  async function submit(event) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    setResult(null)

    let extra
    if (metadata.trim()) {
      try {
        extra = JSON.parse(metadata)
      } catch {
        setError(new Error('Extra fields must be valid JSON.'))
        setSaving(false)
        return
      }
    }

    try {
      const body = await externalApi.addExternal(
        {
          externalSource: sources[0]?.name,
          externalContentId: form.externalContentId.trim(),
          roomId: targetRoom,
          parentFolderId: form.parentFolderId || 'root',
          autoSync: form.autoSync,
          ...(extra ? { metadata: extra } : {}),
        },
        { actor },
      )
      setResult(body)
      setForm({ ...form, externalContentId: '' })
      setMetadata('')
      onAdded()
    } catch (caught) {
      setError(caught)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
        Add a file from {sources[0]?.name}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The library item is linked to the source file, not a copy of it. The file stays in{' '}
        {sources[0]?.name}; this store keeps the pointer, the metadata, and the version that was
        applied.
      </p>

      {roomOptions.length === 0 ? (
        <div className="mt-4">
          <Notice tone="warning" title="No sales rooms yet" icon="alert">
            A file is added under a room, which is this project's teamsite. Create one on the{' '}
            <a href="#/rooms" className="text-accent underline underline-offset-2">
              sales rooms
            </a>{' '}
            page first.
          </Notice>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-4 space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Room" id="external-room" hint="The teamsite the item is placed under.">
              <select
                id="external-room"
                className={inputClass}
                value={targetRoom}
                onChange={(event) => {
                  setRoomId(event.target.value)
                  // A folder from the previous room is not a legal target here.
                  setForm({ ...form, parentFolderId: 'root' })
                }}
              >
                {roomOptions.map((room) => (
                  <option key={room.id} value={room.id}>
                    {room.data.name || room.id}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Target folder"
              id="external-folder"
              hint="root is the top level of the room library."
            >
              <select
                id="external-folder"
                className={inputClass}
                value={form.parentFolderId}
                onChange={(event) => setForm({ ...form, parentFolderId: event.target.value })}
              >
                {folderOptions.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {folder.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field
            label="Source file id"
            id="external-file-id"
            hint="The id the source uses for the file, e.g. 1XK_AinrjCzyylNGaH6oX9MUfY5-G-Y54."
          >
            <input
              id="external-file-id"
              className={inputClass}
              required
              value={form.externalContentId}
              placeholder="1XK_..."
              onChange={(event) => setForm({ ...form, externalContentId: event.target.value })}
            />
          </Field>

          <Checkbox
            id="external-auto-sync"
            checked={form.autoSync}
            onChange={(checked) => setForm({ ...form, autoSync: checked })}
            label="Keep this item in step with the source"
            hint="On: the item re-syncs whenever the file changes. Off: a one-time snapshot, which is also the default when the field is omitted."
          />

          <Field
            label="Extra fields (optional JSON)"
            id="external-metadata"
            hint="Stored on the item as given and queryable straight away. No migration needed."
          >
            <textarea
              id="external-metadata"
              className={`${inputClass} min-h-20 font-mono text-[13px]`}
              value={metadata}
              placeholder='{"review_owner": "sam"}'
              onChange={(event) => setMetadata(event.target.value)}
            />
          </Field>

          {error && (
            <Notice
              tone={isRateLimited(error) ? 'warning' : 'danger'}
              icon="alert"
              title={isRateLimited(error) ? 'Too many requests' : 'Could not add the file'}
            >
              <p>{describeError(error)}</p>
            </Notice>
          )}

          {result && (
            <Notice
              tone={result.created ? 'success' : 'info'}
              icon="link"
              title={
                result.created
                  ? result.status.linkage === 'linked'
                    ? 'Linked. This item now follows the source file.'
                    : 'Added as a one-time snapshot.'
                  : 'Already linked. Nothing changed.'
              }
            >
              <p>
                contentId <span className="font-mono text-foreground">{result.contentId}</span>
              </p>
              <p className="mt-1">
                {result.status.linkage === 'linked'
                  ? 'A re-sync pass will pick up any change to the source file.'
                  : 'It will not follow the source file. Add it again with auto sync on if that changes.'}
              </p>
            </Notice>
          )}

          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? 'Adding…' : 'Add to library'}
          </Button>
        </form>
      )}
    </Card>
  )
}

function ItemCard({ item, status }) {
  const [open, setOpen] = useState(false)
  const source = item.data.source || {}

  return (
    <Card className="card-hover h-full">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-mono text-base font-semibold text-foreground">
            {item.data.title || source.external_content_id}
          </h3>
          <p className="mt-0.5 truncate font-mono text-sm text-muted-foreground">
            {source.external_content_id}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-1.5">
          <Badge tone={LINKAGE_TONES[source.linkage] || 'neutral'}>
            {source.linkage === 'linked' ? 'live link' : 'snapshot'}
          </Badge>
          <Badge tone={STATE_TONES[status?.state] || 'neutral'}>{status?.state || 'unknown'}</Badge>
        </div>
      </div>

      <p className="mt-3 text-sm text-muted-foreground">{status?.reason}</p>

      <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted-foreground">Source version</dt>
          <dd className="font-mono text-foreground">{source.source_version || '—'}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Size</dt>
          <dd className="font-mono text-foreground">{formatBytes(item.data.size_bytes)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Folder</dt>
          <dd className="truncate font-mono text-foreground">
            {source.parent_folder_name || source.parent_folder_id || '—'}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Last synced</dt>
          <dd className="font-mono text-foreground">{relativeTime(status?.last_synced_at)}</dd>
        </div>
      </dl>

      {source.external_system_connection_name && (
        <p className="mt-3 text-xs text-muted-foreground">via {source.external_system_connection_name}</p>
      )}

      <div className="mt-4">
        <Button onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? 'Hide payload' : 'View payload'}
        </Button>
      </div>

      {open && (
        <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Stored payload (schema-flexible)
          </p>
          <JsonView value={item.data} />
        </div>
      )}
    </Card>
  )
}

function ResyncReport({ report, onDismiss }) {
  if (!report) return null
  const { counts } = report

  return (
    <Notice
      tone={counts.failed > 0 ? 'warning' : 'success'}
      icon="refresh"
      title={`Checked ${report.checked} linked item${report.checked === 1 ? '' : 's'}`}
      onDismiss={onDismiss}
    >
      <ul className="space-y-1">
        <li>
          {counts.updated} re-synced, {counts.in_sync} already current, {counts.skipped} snapshot
          {counts.skipped === 1 ? '' : 's'} skipped
          {counts.failed > 0 ? `, ${counts.failed} could not be checked` : ''}.
        </li>
        {report.updated.map((entry) => (
          <li key={entry.content_id} className="font-mono text-xs">
            {entry.content_id}: {entry.from_version} → {entry.to_version}
          </li>
        ))}
        {report.failed.map((entry) => (
          <li key={entry.content_id} className="text-xs text-destructive">
            {entry.content_id}: {entry.remediation}
          </li>
        ))}
      </ul>
    </Notice>
  )
}

export default function ExternalSync() {
  const [actor] = useState('ui')
  const [report, setReport] = useState(null)
  const [resyncError, setResyncError] = useState(null)
  const [resyncing, setResyncing] = useState(false)
  const [scopeRoom, setScopeRoom] = useState('')

  const sources = useAsync(() => externalApi.sources(), [])
  const connections = useAsync(() => externalApi.connections(), [])
  const rooms = useAsync(() => externalApi.rooms(), [])
  const items = useAsync(
    () => externalApi.listExternal({ limit: 200, roomId: scopeRoom }),
    [scopeRoom],
  )

  const roomList = rooms.data?.records || []

  function refetchAll() {
    items.refetch()
    sources.refetch()
    connections.refetch()
  }

  async function resync() {
    setResyncing(true)
    setResyncError(null)
    try {
      // Named as an actor, because a pass driven from the UI is still an
      // automation and the audit log should not blame a person for it.
      setReport(await externalApi.resync(scopeRoom ? { roomId: scopeRoom } : {}, { actor: 'sync-worker' }))
      items.refetch()
    } catch (error) {
      setReport(null)
      setResyncError(error)
    } finally {
      setResyncing(false)
    }
  }

  const entries = useMemo(
    () => (items.data?.items || []).map((item, index) => ({ item, status: items.data.statuses[index] })),
    [items.data],
  )

  const connected = (sources.data?.sources || []).some((source) => source.connected)

  if (items.loading && sources.loading) return <Spinner label="Loading the content library" />

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">External content</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Link a file that lives in someone else's cloud into a room's library, and optionally keep
            it in step with the source.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon="refresh" onClick={refetchAll} disabled={items.loading}>
            Refresh
          </Button>
          <Button icon="refresh" variant="primary" onClick={resync} disabled={resyncing || !connected}>
            {resyncing ? 'Re-syncing…' : 'Run re-sync'}
          </Button>
        </div>
      </header>

      {report && <ResyncReport report={report} onDismiss={() => setReport(null)} />}

      {resyncError && (
        <Notice tone="danger" icon="alert" title="The re-sync pass could not run" onDismiss={() => setResyncError(null)}>
          <p>{describeError(resyncError)}</p>
        </Notice>
      )}

      <ConnectionPanel
        sources={sources.data?.sources || []}
        connections={connections.data?.connections || []}
        error={sources.error}
        onRetry={sources.refetch}
        onConnected={() => {
          sources.refetch()
          connections.refetch()
        }}
      />

      {connected && (
        <AddForm sources={sources.data?.sources || []} rooms={roomList} actor={actor} onAdded={refetchAll} />
      )}

      {items.error && <ErrorNote error={items.error} onRetry={items.refetch} />}

      {!items.error && (
        <section aria-label="Linked library items" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">
                Linked items <span className="text-muted-foreground">({entries.length})</span>
              </h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                State here is what the store last applied. A source that has moved since then only
                shows up when a re-sync pass runs.
              </p>
            </div>
            {roomList.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <label htmlFor="external-scope" className="text-xs font-medium text-muted-foreground">
                  Room
                </label>
                <select
                  id="external-scope"
                  className={inputClass}
                  value={scopeRoom}
                  onChange={(event) => setScopeRoom(event.target.value)}
                >
                  <option value="">All rooms</option>
                  {roomList.map((room) => (
                    <option key={room.id} value={room.id}>
                      {room.data.name || room.id}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {entries.length === 0 ? (
            <EmptyState
              title="No externally sourced content yet"
              description="Add a file from a connected source above. It will appear here, and in the audit log the moment it lands."
            />
          ) : (
            <ul className="grid gap-4 md:grid-cols-2">
              {entries.map(({ item, status }) => (
                <li key={item.id}>
                  <ItemCard item={item} status={status} />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
