import { useMemo, useState } from 'react'

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
import { absoluteTime, relativeTime } from '@/lib/api'

import { libraryApi } from './api'
import { ICONS } from './icons'
import { useRoomSelection } from './useRoomSelection'

/**
 * The room's Documents view (WF-003).
 *
 * Three things here are load-bearing, and each maps to a rule in the research
 * rather than to taste:
 *
 * 1. The view lists only the room's documents folder, so a room's other assets
 *    never leak into it.
 * 2. The *New* button is not rendered unless the caller's role permits an
 *    upload, and the server refuses it either way. Hiding a control is a
 *    courtesy; refusing the request is the gate.
 * 3. Each row shows a thumbnail, who last modified it, and its workflow
 *    status. An empty thumbnail is a real state, not a broken image: rendering
 *    is asynchronous and the url is empty until it finishes.
 *
 * Ported from `frontend/src/pages/Documents.jsx` on
 * `feature/WF-003-...`. The page is unchanged in behaviour; it differs in that
 * it owns its API wrapper, its icon paths, and its own room selection, because
 * the three places it used to reach into are shared files a feature must not
 * edit.
 */

const ROLE_HINT = 'Acting as'

export default function Documents() {
  const [role, setRole] = useState('room_collaborator')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ name: '', title: '', description: '', expires_at: '' })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [expanded, setExpanded] = useState(null)

  const who = useMemo(() => ({ actor: 'dana', role }), [role])

  const rooms = useAsync(() => libraryApi.rooms(), [])
  const workflow = useAsync(() => libraryApi.workflow(), [])

  const roomRecords = rooms.data?.records || []
  const [roomId, selectRoom] = useRoomSelection(roomRecords[0]?.id)

  const library = useAsync(
    () =>
      roomId
        ? libraryApi.listDocuments(roomId, { ...who, search, status: statusFilter })
        : Promise.resolve(null),
    [roomId, role, search, statusFilter],
  )
  const galleries = useAsync(
    () => (roomId ? libraryApi.listGalleryBlocks(roomId) : Promise.resolve(null)),
    [roomId],
  )

  const capabilities = library.data?.capabilities
  const documents = library.data?.documents || []
  const canUpload = Boolean(capabilities?.can_upload)
  const archived = library.data?.room_status === 'archived'

  async function addDocument(event) {
    event.preventDefault()
    setSaving(true)
    setFormError(null)
    try {
      const payload = { name: form.name }
      if (form.title.trim()) payload.title = form.title.trim()
      if (form.description.trim()) payload.description = form.description.trim()
      // Omitted rather than sent empty: the server rejects a non-future date,
      // and an empty string is not a date.
      if (form.expires_at) payload.expires_at = form.expires_at
      await libraryApi.addDocument(roomId, payload, who)
      setForm({ name: '', title: '', description: '', expires_at: '' })
      setCreating(false)
      library.refetch()
    } catch (error) {
      setFormError(error)
    } finally {
      setSaving(false)
    }
  }

  async function publish(documentId) {
    setActionError(null)
    try {
      await libraryApi.setDocumentStatus(roomId, documentId, 'published', who)
      library.refetch()
    } catch (error) {
      setActionError(error)
    }
  }

  async function remove(documentId) {
    setActionError(null)
    try {
      await libraryApi.deleteDocument(roomId, documentId, who)
      library.refetch()
    } catch (error) {
      setActionError(error)
    }
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">Documents</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            The files in this room's documents folder. Every change is audited.
          </p>
        </div>
        {canUpload && (
          <Button
            icon="plus"
            variant={creating ? 'ghost' : 'primary'}
            onClick={() => setCreating((value) => !value)}
          >
            {creating ? 'Cancel' : 'New'}
          </Button>
        )}
      </header>

      {roomRecords.length === 0 ? (
        <EmptyState
          title="No rooms yet"
          description="A document library belongs to a room. Create a room first, then come back here."
        />
      ) : (
        <>
          <Card>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Room" id="documents-room">
                <select
                  id="documents-room"
                  className={inputClass}
                  value={roomId || ''}
                  onChange={(event) => selectRoom(event.target.value)}
                >
                  {roomRecords.map((room) => (
                    <option key={room.id} value={room.id}>
                      {room.data.name || room.id}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label={ROLE_HINT}
                id="documents-role"
                hint="The role decides whether the New button appears at all."
              >
                <select
                  id="documents-role"
                  className={inputClass}
                  value={role}
                  onChange={(event) => setRole(event.target.value)}
                >
                  {(workflow.data?.roles || []).map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <p className="mt-4 text-xs text-muted-foreground">
              {capabilities
                ? `${capabilities.role_label} · ${
                    canUpload
                      ? 'can add documents to this room'
                      : archived
                        ? 'uploads are closed while the room is archived'
                        : 'cannot add documents to this room'
                  }`
                : ' '}
            </p>
          </Card>

          {archived && (
            <div
              role="status"
              className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm"
            >
              <p className="flex items-center gap-2 font-medium text-amber-300">
                <Icon path={ICONS.archive} size={16} />
                This digital sales room is archived.
              </p>
              <p className="mt-1 text-muted-foreground">
                Uploading documents is closed. Only instance administrators see the New button.
              </p>
            </div>
          )}

          {creating && canUpload && (
            <Card>
              <form onSubmit={addDocument} className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="File name" id="document-name" hint="Include the extension, e.g. proposal.pdf">
                    <input
                      id="document-name"
                      className={inputClass}
                      required
                      value={form.name}
                      onChange={(event) => setForm({ ...form, name: event.target.value })}
                    />
                  </Field>
                  <Field label="Title" id="document-title" hint="Shown to the buyer.">
                    <input
                      id="document-title"
                      className={inputClass}
                      value={form.title}
                      onChange={(event) => setForm({ ...form, title: event.target.value })}
                    />
                  </Field>
                </div>
                <Field label="Description" id="document-description">
                  <input
                    id="document-description"
                    className={inputClass}
                    value={form.description}
                    onChange={(event) => setForm({ ...form, description: event.target.value })}
                  />
                </Field>
                <Field
                  label="Expires"
                  id="document-expires"
                  hint="Optional. After this date the file is treated as expired. Must be in the future."
                >
                  <input
                    id="document-expires"
                    type="date"
                    className={inputClass}
                    value={form.expires_at}
                    onChange={(event) => setForm({ ...form, expires_at: event.target.value })}
                  />
                </Field>
                {formError && <ErrorNote error={formError} />}
                <Button type="submit" variant="primary" disabled={saving}>
                  {saving ? 'Adding…' : 'Add document'}
                </Button>
              </form>
            </Card>
          )}

          <Card>
            <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
              <Field label="Search" id="documents-search" hint="Matches name, title and description.">
                <input
                  id="documents-search"
                  className={inputClass}
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </Field>
              <Field label="Workflow status" id="documents-status">
                <select
                  id="documents-status"
                  className={inputClass}
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value)}
                >
                  <option value="">All statuses</option>
                  {(workflow.data?.known || []).map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </Card>

          {actionError && <ErrorNote error={actionError} />}

          {library.loading && <Spinner label="Loading documents" />}
          {library.error && <ErrorNote error={library.error} onRetry={library.refetch} />}

          {!library.loading && !library.error && documents.length === 0 && (
            <EmptyState
              title="There are no documents or media files in this folder."
              description={
                search || statusFilter
                  ? 'No document matches the current filters.'
                  : canUpload
                    ? 'Use New to add the first file to this room.'
                    : 'Nothing has been shared here yet.'
              }
            />
          )}

          {!library.loading && !library.error && documents.length > 0 && (
            <ul className="space-y-3">
              {documents.map((document) => {
                const meta = document.library
                const permissions = document.permissions
                return (
                  <li key={document.id}>
                    <Card>
                      <div className="flex flex-wrap items-start gap-4">
                        <Thumbnail meta={meta} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <h2 className="truncate font-mono text-base font-semibold">
                              {meta.title}
                            </h2>
                            <Badge tone={statusTone(meta.status)}>{meta.status}</Badge>
                            {meta.expired && <Badge tone="restore">expired</Badge>}
                          </div>
                          <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                            {meta.name} · {meta.format}
                          </p>
                          <dl className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
                            <div>
                              <dt className="text-muted-foreground">Last modified by</dt>
                              <dd className="font-mono text-foreground">
                                {meta.last_modified_by || 'unknown'}
                              </dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Modified</dt>
                              <dd
                                className="font-mono text-foreground"
                                title={absoluteTime(meta.last_modified_at)}
                              >
                                {relativeTime(meta.last_modified_at)}
                              </dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Uploaded by</dt>
                              <dd className="font-mono text-foreground">
                                {meta.uploaded_by || 'unknown'}
                              </dd>
                            </div>
                          </dl>
                          {meta.description && (
                            <p className="mt-3 text-sm text-muted-foreground">
                              {meta.description}
                            </p>
                          )}
                          {meta.expires_at && (
                            <p className="mt-2 text-xs text-muted-foreground">
                              Expires {absoluteTime(meta.expires_at)}
                            </p>
                          )}

                          <div className="mt-4 flex flex-wrap gap-2">
                            {permissions.can_manage_status && meta.status === 'draft' && (
                              <Button icon="chevron" onClick={() => publish(document.id)}>
                                Publish
                              </Button>
                            )}
                            {permissions.can_delete && (
                              <Button
                                icon="trash"
                                variant="danger"
                                onClick={() => remove(document.id)}
                              >
                                Delete
                              </Button>
                            )}
                            <Button
                              onClick={() =>
                                setExpanded(expanded === document.id ? null : document.id)
                              }
                            >
                              {expanded === document.id ? 'Hide payload' : 'View payload'}
                            </Button>
                          </div>

                          {expanded === document.id && (
                            <div className="mt-4 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                                Stored payload (schema-flexible)
                              </p>
                              <JsonView value={document.data} />
                            </div>
                          )}
                        </div>
                      </div>
                    </Card>
                  </li>
                )
              })}
            </ul>
          )}

          <GalleryBlocks
            roomId={roomId}
            role={role}
            actor="dana"
            documents={documents}
            galleries={galleries}
          />
        </>
      )}
    </div>
  )
}

/**
 * The thumbnail cell. Rendering is asynchronous, so "no image yet" gets an
 * explicit label and the file type rather than a broken-image glyph.
 */
function Thumbnail({ meta }) {
  if (meta.thumbnail?.state === 'ready' && meta.thumbnail.url) {
    return (
      <img
        src={meta.thumbnail.url}
        alt={`Preview of ${meta.title}`}
        className="h-16 w-16 shrink-0 rounded-lg border border-border-subtle/30 object-cover"
      />
    )
  }
  return (
    <div className="flex h-16 w-16 shrink-0 flex-col items-center justify-center gap-1 rounded-lg border border-border-subtle/30 bg-muted text-muted-foreground">
      <span className="font-mono text-[10px] uppercase">{meta.format}</span>
      <span className="text-[10px]">rendering</span>
    </div>
  )
}

/** Buyer-facing preview of the Document Gallery Block. */
function GalleryBlocks({ roomId, role, actor, documents, galleries }) {
  const [error, setError] = useState(null)
  const blocks = galleries.data?.blocks || []
  const slots = galleries.data?.slots || 4
  const choices = documents.map((document) => document.id)
  // Editing a room page needs the same role as managing documents; a Viewer
  // gets the rendered block and no controls.
  const editable = role !== 'viewer'

  async function save(slotDocumentIds) {
    setError(null)
    try {
      const first = blocks[0]
      await libraryApi.saveGalleryBlock(
        roomId,
        { documents: slotDocumentIds },
        { actor, role },
        first?.id || null,
      )
      galleries.refetch()
    } catch (saveError) {
      setError(saveError)
    }
  }

  if (galleries.loading) return <Spinner label="Loading gallery blocks" />
  if (galleries.error) return <ErrorNote error={galleries.error} onRetry={galleries.refetch} />

  const firstBlock = blocks[0]

  return (
    <Card>
      <div className="flex items-center gap-2">
        <span className="rounded-lg bg-muted p-2 text-accent">
          <Icon path={ICONS.gallery} size={18} />
        </span>
        <div>
          <h2 className="font-mono text-base font-semibold">Document Gallery Block</h2>
          <p className="text-xs text-muted-foreground">
            {slots} fixed document selectors. To show more, add another block.
          </p>
        </div>
      </div>

      {error && <div className="mt-3"><ErrorNote error={error} /></div>}

      {blocks.length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">
          No gallery block on this room yet.
        </p>
      ) : (
        <ul className="mt-4 grid gap-3 sm:grid-cols-2">
          {firstBlock.documents.map((document) => (
            <li key={document.id} className="rounded-lg border border-border-subtle/25 p-3">
              <p className="truncate font-mono text-sm font-medium">{document.title}</p>
              <p className="truncate text-xs text-muted-foreground">
                {document.name} · {document.format}
              </p>
              <a
                href={document.thumbnail_url || '#'}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-flex min-h-11 items-center gap-1 text-sm text-accent hover:underline"
              >
                Open
                <span className="sr-only">{document.title} in a new tab</span>
                <Icon path={ICONS.external} size={14} />
              </a>
            </li>
          ))}
        </ul>
      )}

      {editable && (
        <div className="mt-4 border-t border-border-subtle/25 pt-4">
          <p className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Document 1–{slots}
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {Array.from({ length: slots }, (_, index) => {
              const current = firstBlock?.documents?.[index]?.id || ''
              return (
                <select
                  key={index}
                  aria-label={`Document ${index + 1}`}
                  className={inputClass}
                  value={current}
                  onChange={(event) => {
                    const next = Array.from(
                      { length: slots },
                      (_, position) => firstBlock?.documents?.[position]?.id || '',
                    )
                    next[index] = event.target.value
                    save(next.filter(Boolean))
                  }}
                >
                  <option value="">Empty</option>
                  {choices.map((id) => {
                    const found = documents.find((document) => document.id === id)
                    return (
                      <option key={id} value={id}>
                        {found?.library.title || id}
                      </option>
                    )
                  })}
                </select>
              )
            })}
          </div>
        </div>
      )}
    </Card>
  )
}

function statusTone(status) {
  if (status === 'published') return 'insert'
  if (status === 'in_review') return 'update'
  if (status === 'draft') return 'neutral'
  return 'restore'
}
