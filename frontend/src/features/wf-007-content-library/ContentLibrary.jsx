/**
 * The content library: ingest a document or deck (WF-007).
 *
 * The interesting part of this screen is what it does *not* ask the backend to
 * know. There is no fixed form for a document: the metadata part sent with the
 * upload is assembled from whatever the person typed, including free-form
 * property rows, so a team can require its own ingest metadata without a
 * migration or a redeploy.
 *
 * The researched behaviours are surfaced rather than hidden, because each one
 * changes what the user should expect to see:
 *
 * * A collision is de-collided to `Deck (1).pptx` in the target folder only,
 *   and the applied name is reported back on the stored record.
 * * `rollbackOnError` decides whether a failed binary leaves a retryable draft
 *   in the folder. It defaults on, which is the documented advice for
 *   unattended ingestion.
 * * The thumbnail is not there when the upload returns. Rendering lags the
 *   upload, so each card offers to fetch it and the pending state is shown
 *   honestly instead of a broken image.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import { relativeTime } from '@/lib/api'
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

import { libraryApi } from './api'
import { byteSize } from './format'
import { ICONS } from './icons'
import { Note, Toggle } from './primitives'

const EMPTY_FORM = {
  name: '',
  description: '',
  parentFolderId: 'root',
  externalId: '',
  expiresAt: '',
  language: '',
  ownerId: '',
  resolveNameCollision: true,
  rollbackOnError: true,
}

const TONES = {
  Draft: 'insert',
  Published: 'update',
}

/** The download link is a raw anchor, so it carries the focus ring itself. */
const LINK_CLASS =
  'inline-flex min-h-11 items-center gap-2 rounded-lg bg-muted px-4 text-sm text-foreground ' +
  'transition-colors duration-200 hover:bg-border-subtle ' +
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent'

function formatFromName(name) {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

export default function ContentLibrary() {
  const rooms = useAsync(() => libraryApi.rooms(), [])
  const [roomId, setRoomId] = useState('')

  // Default to the first room once the list arrives, so the screen is useful
  // immediately rather than showing an empty picker.
  useEffect(() => {
    const first = rooms.data?.records?.[0]
    if (first && !roomId) setRoomId(first.id)
  }, [rooms.data, roomId])

  const documents = useAsync(
    () => (roomId ? libraryApi.documents(roomId, { limit: 100 }) : Promise.resolve(null)),
    [roomId]
  )
  const folders = useAsync(
    () => (roomId ? libraryApi.folders(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const usage = useAsync(
    () => (roomId ? libraryApi.usage(roomId) : Promise.resolve(null)),
    [roomId]
  )

  const [form, setForm] = useState(EMPTY_FORM)
  const [file, setFile] = useState(null)
  const [properties, setProperties] = useState([{ key: '', value: '' }])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [detail, setDetail] = useState(null)
  const fileInput = useRef(null)

  const folderOptions = useMemo(
    () => (folders.data?.folders || []).slice().sort((a, b) => a.data.path.localeCompare(b.data.path)),
    [folders.data]
  )

  function reset() {
    setForm(EMPTY_FORM)
    setFile(null)
    setProperties([{ key: '', value: '' }])
    if (fileInput.current) fileInput.current.value = ''
  }

  function pickFile(event) {
    const chosen = event.target.files?.[0] || null
    setFile(chosen)
    setError(null)
    // Prefill the name from the filename: the API requires it, and making a
    // person retype what they just selected is pure friction.
    if (chosen) setForm((prev) => ({ ...prev, name: chosen.name }))
  }

  async function ingest(event) {
    event.preventDefault()
    if (!file || !roomId) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const properties_ = Object.fromEntries(
        properties.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.value])
      )
      const metadata = {
        name: form.name.trim() || file.name,
        format: formatFromName(form.name.trim() || file.name),
        parentFolderId: form.parentFolderId,
      }
      for (const key of ['description', 'externalId', 'expiresAt', 'language', 'ownerId']) {
        if (form[key].trim()) metadata[key] = form[key].trim()
      }
      if (Object.keys(properties_).length) metadata.properties = properties_

      const created = await libraryApi.ingest(roomId, {
        file,
        metadata,
        resolveNameCollision: form.resolveNameCollision,
        rollbackOnError: form.rollbackOnError,
      })

      setNotice(created)
      setDetail(created.id)
      reset()
      documents.refetch()
      usage.refetch()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  function refreshAll() {
    documents.refetch()
    usage.refetch()
  }

  const rows = documents.data?.documents || []

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">Content library</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Ingest a document or deck. Binaries are stored on disk; the metadata part is
            schema-flexible JSON and every change is audited.
          </p>
        </div>
        <Button icon="refresh" onClick={refreshAll} aria-label="Reload the library">
          Refresh
        </Button>
      </header>

      {rooms.loading && <Spinner label="Loading rooms" />}
      {rooms.error && <ErrorNote error={rooms.error} onRetry={rooms.refetch} />}

      {!rooms.loading && (rooms.data?.records || []).length === 0 && (
        <EmptyState
          title="No sales rooms yet"
          description="A room is the container a library belongs to. Create one first, then come back and ingest into it."
        />
      )}

      {(rooms.data?.records || []).length > 0 && (
        <>
          <Field label="Room" id="library-room" hint="The library is scoped to one room.">
            <select
              id="library-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              {(rooms.data.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data.name || room.id}
                </option>
              ))}
            </select>
          </Field>

          {usage.data && (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {/*
                No `icon` on these: the shared StatCard threads a name from the
                shared PATHS map and offers no way to pass a path, and none of
                those names (documents, folders, a clock) means anything here.
                The label carries the meaning and an icon would be decoration
                that risks being wrong. Adding an `iconPath` to StatCard is a
                one-line platform change, raised in the port report.
              */}
              <StatCard label="Documents" value={usage.data.documents} />
              <StatCard label="Folders" value={usage.data.folders} />
              <StatCard label="Stored size" value={byteSize(usage.data.bytes)} />
              <StatCard
                label="Awaiting thumbnail"
                value={usage.data.pending_thumbnails}
                hint={usage.data.failed ? `${usage.data.failed} failed ingest` : undefined}
              />
            </div>
          )}

          {notice && <IngestReceipt record={notice} onDismiss={() => setNotice(null)} />}

          <Card>
            <form onSubmit={ingest} className="space-y-4">
              <div className="flex items-center gap-2">
                <span className="text-accent">
                  <Icon path={ICONS.upload} />
                </span>
                <h2 className="font-mono text-base font-semibold">Ingest a document or deck</h2>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <Field
                  label="File"
                  id="library-file"
                  hint="Sent as the binary part. Nothing is uploaded until you choose a file."
                >
                  <input
                    ref={fileInput}
                    id="library-file"
                    type="file"
                    className={`${inputClass} py-2 file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:text-foreground`}
                    onChange={pickFile}
                    required
                  />
                </Field>

                <Field
                  label="Name"
                  id="library-name"
                  hint={
                    file
                      ? 'Required. Prefilled from the file name.'
                      : 'Required. Choose a file to prefill it.'
                  }
                >
                  <input
                    id="library-name"
                    className={inputClass}
                    value={form.name}
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                    required
                  />
                </Field>
              </div>

              <div className="grid gap-4 lg:grid-cols-3">
                <Field
                  label="Target folder"
                  id="library-folder"
                  hint="root is the top of the room. Materialized paths are built from this."
                >
                  <select
                    id="library-folder"
                    className={inputClass}
                    value={form.parentFolderId}
                    onChange={(event) => setForm({ ...form, parentFolderId: event.target.value })}
                  >
                    <option value="root">root (top of the room)</option>
                    {folderOptions.map((folder) => (
                      <option key={folder.id} value={folder.id}>
                        {folder.data.path}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field label="External id" id="library-external" hint="Correlate with a CRM deal or any other record.">
                  <input
                    id="library-external"
                    className={inputClass}
                    placeholder="ext-12345"
                    value={form.externalId}
                    onChange={(event) => setForm({ ...form, externalId: event.target.value })}
                  />
                </Field>

                <Field label="Expires at" id="library-expires" hint="Sticky: treated as expired after this date.">
                  <input
                    id="library-expires"
                    type="date"
                    className={inputClass}
                    min={today()}
                    value={form.expiresAt}
                    onChange={(event) => setForm({ ...form, expiresAt: event.target.value })}
                  />
                </Field>
              </div>

              <div className="grid gap-4 lg:grid-cols-3">
                <Field label="Description" id="library-description">
                  <input
                    id="library-description"
                    className={inputClass}
                    value={form.description}
                    onChange={(event) => setForm({ ...form, description: event.target.value })}
                  />
                </Field>
                <Field label="Owner id" id="library-owner">
                  <input
                    id="library-owner"
                    className={inputClass}
                    value={form.ownerId}
                    onChange={(event) => setForm({ ...form, ownerId: event.target.value })}
                  />
                </Field>
                <Field label="Language" id="library-language" hint="BCP-47 tag, used for search and personalisation.">
                  <input
                    id="library-language"
                    className={inputClass}
                    placeholder="en-GB"
                    value={form.language}
                    onChange={(event) => setForm({ ...form, language: event.target.value })}
                  />
                </Field>
              </div>

              <fieldset className="rounded-lg border border-border-subtle/25 p-4">
                <legend className="px-1 text-xs font-medium text-muted-foreground">
                  Custom properties
                </legend>
                <p className="mb-3 text-xs text-muted-foreground">
                  Any key and value. These are stored and indexed as-is, so a team can require its own
                  metadata without a migration.
                </p>
                <div className="space-y-2">
                  {properties.map((row, index) => (
                    <div key={index} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                      <input
                        className={inputClass}
                        placeholder="audience"
                        aria-label={`Property ${index + 1} name`}
                        value={row.key}
                        onChange={(event) =>
                          setProperties(
                            properties.map((item, i) => (i === index ? { ...item, key: event.target.value } : item))
                          )
                        }
                      />
                      <input
                        className={inputClass}
                        placeholder="enterprise"
                        aria-label={`Property ${index + 1} value`}
                        value={row.value}
                        onChange={(event) =>
                          setProperties(
                            properties.map((item, i) => (i === index ? { ...item, value: event.target.value } : item))
                          )
                        }
                      />
                      <Button
                        variant="ghost"
                        icon="close"
                        aria-label={`Remove property ${index + 1}`}
                        onClick={() =>
                          setProperties(
                            properties.length > 1
                              ? properties.filter((_, i) => i !== index)
                              : [{ key: '', value: '' }]
                          )
                        }
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                </div>
                <div className="mt-3">
                  <Button icon="plus" onClick={() => setProperties([...properties, { key: '', value: '' }])}>
                    Add property
                  </Button>
                </div>
              </fieldset>

              <div className="grid gap-4 sm:grid-cols-2">
                <Toggle
                  label="Resolve name collisions"
                  hint="Appends (1), (2) ... until the name is free in the target folder. Off means a conflict is an error."
                  checked={form.resolveNameCollision}
                  onChange={(value) => setForm({ ...form, resolveNameCollision: value })}
                />
                <Toggle
                  label="Roll back on error"
                  hint="Removes the draft if the binary cannot be stored, so the folder keeps no orphaned entry."
                  checked={form.rollbackOnError}
                  onChange={(value) => setForm({ ...form, rollbackOnError: value })}
                />
              </div>

              {error && <ErrorNote error={error} />}

              <Note>
                The upload returns before the thumbnail exists: rendering lags the upload by design,
                so a new document arrives as a Draft at version 0.1 with its thumbnail still pending.
                Use <em>Load thumbnail</em> on the card once you want it.
              </Note>

              <div className="flex flex-wrap gap-2">
                {/*
                  The glyph goes in as a child rather than through Button's
                  `icon` prop: that prop takes a name from the shared PATHS map,
                  and "upload" is not in it. Button renders children after its
                  icon slot, so the result is the same icon-plus-label control
                  the design system asks for, with the 44px target intact.
                */}
                <Button type="submit" variant="primary" disabled={busy || !file}>
                  <Icon path={ICONS.upload} />
                  {busy ? 'Ingesting…' : 'Ingest document'}
                </Button>
                <Button onClick={reset} disabled={busy}>
                  Clear
                </Button>
              </div>
            </form>
          </Card>
        </>
      )}

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-mono text-lg font-semibold">Library contents</h2>
          {folders.data && folders.data.count > 0 && (
            <p className="font-mono text-xs text-muted-foreground">
              {folders.data.folders.map((folder) => folder.data.path).join('  ')}
            </p>
          )}
        </div>

        {documents.loading && <Spinner label="Loading documents" />}
        {documents.error && <ErrorNote error={documents.error} onRetry={documents.refetch} />}

        {!documents.loading && !documents.error && rows.length === 0 && (
          <EmptyState
            title="The library is empty"
            description="Ingest a document or a deck above. The record lands as a Draft at version 0.1 with the thumbnail still pending."
          />
        )}

        <ul className="grid gap-4 lg:grid-cols-2">
          {rows.map((record) => (
            <li key={record.id}>
              <DocumentCard
                record={record}
                expanded={detail === record.id}
                onToggle={() => setDetail(detail === record.id ? null : record.id)}
                onChanged={refreshAll}
              />
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

/** What came back from an ingest, with the two things that surprise people. */
function IngestReceipt({ record, onDismiss }) {
  const renamed = record.data.requestedName
  return (
    <div
      role="status"
      className="rounded-lg border border-accent/30 bg-accent/10 p-4 text-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono font-semibold text-foreground">
            Stored &ldquo;{record.data.name}&rdquo;
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {record.data.libraryMaterializedPath} &middot; {record.data.status} &middot; v
            {record.data.version} &middot; {byteSize(record.data.size)}
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">id {record.id}</p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            versionId {record.data.versionId}
          </p>
          {renamed && (
            <p className="mt-2 text-xs text-foreground">
              The name was taken in that folder, so it was stored as &ldquo;{record.data.name}&rdquo;
              rather than &ldquo;{renamed}&rdquo;.
            </p>
          )}
        </div>
        <Button icon="close" variant="ghost" onClick={onDismiss} aria-label="Dismiss the receipt">
          Dismiss
        </Button>
      </div>
    </div>
  )
}

function DocumentCard({ record, expanded, onToggle, onChanged }) {
  const { data } = record
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [thumbnailNonce, setThumbnailNonce] = useState(0)
  const versionInput = useRef(null)

  // The thumbnail endpoint renders on demand and answers 200 once it exists,
  // so a preview that fails to load is the honest "still pending" signal.
  const thumbnailReady = data.thumbnailStatus === 'ready'

  async function loadThumbnail() {
    setBusy(true)
    setError(null)
    try {
      await libraryApi.deriveThumbnail(record.id)
      setThumbnailNonce((value) => value + 1)
      onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function addVersion(event) {
    event.preventDefault()
    const chosen = versionInput.current?.files?.[0]
    if (!chosen) return
    setBusy(true)
    setError(null)
    try {
      await libraryApi.addVersion(record.id, chosen)
      versionInput.current.value = ''
      onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      await libraryApi.remove(record.id)
      onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="card-hover h-full">
      <div className="flex gap-4">
        <div className="w-28 shrink-0">
          {thumbnailReady ? (
            <img
              key={thumbnailNonce}
              src={libraryApi.thumbnailUrl(record.id)}
              alt={`Preview of ${data.name}`}
              width={112}
              height={70}
              className="h-18 w-28 rounded-md border border-border-subtle/40 object-cover"
            />
          ) : (
            <div className="flex h-18 w-28 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border-subtle/50 text-center">
              <span className="text-muted-foreground">
                <Icon path={ICONS.image} size={18} />
              </span>
              <span className="px-1 text-[10px] leading-tight text-muted-foreground">
                thumbnail pending
              </span>
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="truncate font-mono text-base font-semibold text-foreground">
                {data.name}
              </h3>
              <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                {data.libraryMaterializedPath}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <Badge tone={TONES[data.status] || 'neutral'}>{data.status || 'unset'}</Badge>
              <Badge>v{data.version}</Badge>
            </div>
          </div>

          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">Format</dt>
              <dd className="font-mono text-foreground">
                {String(data.format || '—').toUpperCase()}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Size</dt>
              <dd className="font-mono text-foreground">{byteSize(data.size)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Ingested</dt>
              <dd className="font-mono text-foreground">{relativeTime(data.ingestedAt)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">External id</dt>
              <dd className="truncate font-mono text-foreground">{data.externalId || '—'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Expires</dt>
              <dd className="font-mono text-foreground">{data.expiresAt || '—'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Versions</dt>
              <dd className="font-mono text-foreground">{(data.versions || []).length}</dd>
            </div>
          </dl>

          {data.ingestState === 'failed' && (
            <p className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
              Ingest failed and the draft was kept: {data.ingestError}
            </p>
          )}

          {error && (
            <p role="alert" className="mt-3 text-xs text-destructive">
              {String(error.message || error)}
            </p>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={onToggle}>{expanded ? 'Hide payload' : 'View payload'}</Button>
        {!thumbnailReady && (
          <Button onClick={loadThumbnail} disabled={busy}>
            <Icon path={ICONS.image} />
            Load thumbnail
          </Button>
        )}
        <a href={libraryApi.contentUrl(record.id)} className={LINK_CLASS}>
          <Icon path={ICONS.download} />
          Download
        </a>
        <Button variant="danger" icon="trash" onClick={remove} disabled={busy}>
          Remove
        </Button>
      </div>

      <form onSubmit={addVersion} className="mt-3 flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <Field
            label="Add a new version"
            id={`version-${record.id}`}
            hint="Appends a version. The stored binary of the current version is kept."
          >
            <input
              ref={versionInput}
              id={`version-${record.id}`}
              type="file"
              className={`${inputClass} py-2 file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:text-foreground`}
            />
          </Field>
        </div>
        <Button type="submit" disabled={busy}>
          <Icon path={ICONS.layers} />
          Add version
        </Button>
      </form>

      {expanded && (
        <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Stored payload (schema-flexible)
          </p>
          <JsonView value={data} />
          {(data.versions || []).length > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Version history
              </p>
              <ul className="space-y-1">
                {data.versions.map((version) => (
                  <li key={version.versionId} className="flex flex-wrap items-center gap-2 text-xs">
                    <a
                      href={libraryApi.contentUrl(record.id, version.versionId)}
                      className="font-mono text-accent underline underline-offset-2"
                    >
                      v{version.version}
                    </a>
                    <span className="text-muted-foreground">{byteSize(version.bytes)}</span>
                    <span className="font-mono text-muted-foreground/70">
                      {version.versionId}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
