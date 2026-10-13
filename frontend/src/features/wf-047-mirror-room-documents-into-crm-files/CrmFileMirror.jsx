import { useCallback, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'
import {
  attachSummary,
  byteSize,
  crmFileApi,
  folderNote,
  outcomeNote,
  requestSummary,
  stateTone,
  vendorNote,
} from './api'
import { Callout, DataTable, Fact, Glyph, PushChip, Section } from './primitives'

/**
 * The CRM file mirror (WF-047).
 *
 * The researched flow, in the order the page renders it: a rep uploads a deck or
 * a proposal into the room; the rep toggles **Push to CRM** on the asset, or the
 * connection mirrors every room document; the room uploads the binary to the CRM
 * files endpoint with `multipart/form-data` and writes the returned file id onto
 * the CRM record that owns it; the room stores `crm_file_id` on the asset row, so
 * re-uploads are skipped and revisions create a new file; and the rep sees the
 * attachment from the record's Files panel or note timeline.
 *
 * The page shows each step's evidence rather than only the result, because the
 * three things a reviewer needs to check are the exact request that would go over
 * the wire, which attach mode wrote the id, and whether a confirmation came from a
 * vendor or from the local simulation.
 *
 * A room with no document mirrored yet is an empty state with a reason, not an
 * error, and a connection on a vendor whose file API the research could not source
 * is shown with that gap named rather than hidden.
 */

const ROOM_STORAGE_KEY = 'wf-047:last-room'

function lastRoom() {
  try {
    return window.localStorage.getItem(ROOM_STORAGE_KEY) || ''
  } catch {
    // Private browsing, a locked-down profile, or a server render. An empty
    // picker is the right answer in all three, so nothing is invented here.
    return ''
  }
}

function rememberRoom(roomId) {
  try {
    if (roomId) window.localStorage.setItem(ROOM_STORAGE_KEY, roomId)
  } catch {
    // A browser that will not store the choice still renders; the picker simply
    // starts empty next time.
  }
}

function ConnectionPanel({ connection, onDelete }) {
  const attach =
    connection.attach_mode === 'file_property'
      ? `file property ${connection.file_property}`
      : 'a note on the record'
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Connection
          </p>
          <p className="mt-2 font-display text-base font-semibold text-foreground">
            {connection.name || connection.system}
          </p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{connection.id}</p>
        </div>
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Glyph name="file" size={20} />
        </span>
      </div>
      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Fact term="System" mono>
          {connection.system}
        </Fact>
        <Fact term="Owns the attachment" mono>
          {connection.owner_object} {connection.crm_record_id || '(no record yet)'}
        </Fact>
        <Fact term="Written as">{attach}</Fact>
        <Fact term="Mirrors every document">{connection.mirror_all ? 'yes' : 'no'}</Fact>
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">{folderNote(connection)}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="danger" icon="trash" onClick={() => onDelete(connection)}>
          Remove connection
        </Button>
      </div>
    </Card>
  )
}

function VendorPanel({ vendors }) {
  return (
    <Section
      title="Vendor file capability"
      hint="The research documents one vendor's Files API and states that the other two could not be sourced. Each row below carries that gap verbatim, so a tenant on either of them is told why rather than shown a silent absence."
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {vendors.map((vendor) => (
          <Card key={vendor.system}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-mono text-sm font-semibold text-foreground">{vendor.system}</p>
                <p className="mt-1 text-xs text-muted-foreground">{vendorNote(vendor)}</p>
              </div>
              <span className="rounded-sm bg-accent-soft p-2 text-accent">
                <Glyph name={vendor.status === 'sourced' ? 'folder' : 'close'} size={18} />
              </span>
            </div>
            {vendor.status === 'sourced' ? (
              <dl className="mt-4 grid grid-cols-1 gap-3">
                <Fact term="Upload" mono>
                  {vendor.upload_path}
                </Fact>
                <Fact term="Required field" mono>
                  {vendor.required_fields.join(', ')}
                </Fact>
                <Fact term="Optional fields" mono>
                  {vendor.optional_fields.join(', ')}
                </Fact>
              </dl>
            ) : (
              <p className="mt-4 text-xs text-muted-foreground">
                {vendor.gap}
              </p>
            )}
          </Card>
        ))}
      </div>
    </Section>
  )
}

function InferencePanel({ inferences }) {
  return (
    <Section
      title="Decisions this workflow rests on"
      hint="The research names the two-method connector interface without narrowing it, says revisions create a new file without saying how a revision is detected, gives the reconciliation rule without a cadence, recommends a folder without requiring one, and refuses to source two of the three vendors. Those edges are listed here rather than left for a reader to reconstruct from a diff."
    >
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {inferences.map((entry) => (
          <Card key={entry.id}>
            <p className="font-mono text-[13px] font-semibold text-foreground">{entry.id}</p>
            <p className="mt-1 text-[13px] text-foreground">{entry.topic}</p>
            <p className="mt-2 text-xs text-muted-foreground">{entry.why}</p>
            <p className="mt-2 font-mono text-[11px] text-muted-foreground">
              Change it in {entry.change_it}
            </p>
          </Card>
        ))}
      </div>
    </Section>
  )
}

function DocumentsPanel({ documents, onMirror, onToggle, busy }) {
  return (
    <Section
      title="Room documents"
      hint="The researched product surface: a Push to CRM status chip per file. Every chip carries its state, its tone and the state's own sentence, so the status never depends on colour alone."
    >
      <DataTable
        columns={[
          {
            key: 'filename',
            header: 'Document',
            render: (row) => (
              <div className="min-w-0">
                <p className="text-[13px] text-foreground">{row.filename}</p>
                <p className="font-mono text-[11px] text-muted-foreground">
                  {row.mime_type} - {byteSize(row.byte_size)}
                </p>
              </div>
            ),
          },
          {
            key: 'state',
            header: 'Push to CRM',
            render: (row) => (
              <PushChip state={row.state} note={row.state_note} tone={stateTone(row.state)} />
            ),
          },
          {
            key: 'crm_file_id',
            header: 'crm_file_id',
            render: (row) =>
              row.crm_file_id ? (
                <span className="font-mono text-[11px] text-foreground">{row.crm_file_id}</span>
              ) : (
                <span className="font-mono text-[11px] text-muted-foreground">none</span>
              ),
          },
          {
            key: 'actions',
            header: 'Actions',
            render: (row) => (
              <div className="flex flex-wrap gap-2">
                <Button
                  icon="refresh"
                  disabled={busy}
                  onClick={() => onMirror(row)}
                >
                  Push now
                </Button>
                <Button
                  icon={row.push_to_crm ? 'close' : 'plus'}
                  disabled={busy}
                  onClick={() => onToggle(row, !row.push_to_crm)}
                >
                  {row.push_to_crm ? 'Turn push off' : 'Turn push on'}
                </Button>
              </div>
            ),
          },
        ]}
        rows={documents.assets}
        rowKey={(row) => row.id}
        empty="This room holds no documents that are mirrored into a CRM."
      />
    </Section>
  )
}

function FilesPanel({ files }) {
  return (
    <Section
      title="CRM files panel"
      hint="The second researched surface, reached through the room. Each row is reached through the vendor-agnostic crm_file_id the room stored, which is what lets the sync state survive a connector swap."
    >
      <DataTable
        columns={[
          {
            key: 'filename',
            header: 'File',
            render: (row) => (
              <div className="min-w-0">
                <p className="text-[13px] text-foreground">{row.filename}</p>
                <p className="font-mono text-[11px] text-muted-foreground">
                  {row.crm_file_id}
                </p>
              </div>
            ),
          },
          { key: 'system', header: 'System', render: (row) => <span className="font-mono text-[13px]">{row.system}</span> },
          {
            key: 'owner',
            header: 'Attached to',
            render: (row) => (
              <span className="font-mono text-[13px]">
                {row.owner_object} {row.crm_record_id}
              </span>
            ),
          },
          { key: 'attach_mode', header: 'Written as', render: (row) => <span className="text-[13px]">{row.attach_mode}</span> },
          {
            key: 'simulated',
            header: 'Confirmed by',
            render: (row) => (
              <Badge tone={row.simulated ? 'restore' : 'insert'}>
                {row.simulated ? 'local simulation' : 'the CRM'}
              </Badge>
            ),
          },
        ]}
        rows={files.files}
        rowKey={(row) => row.crm_file_id}
        empty="This room has pushed no file to a CRM yet."
      />
    </Section>
  )
}

function MirrorLogPanel({ mirrors }) {
  return (
    <Section
      title="Mirror log"
      hint="One row per attempt, carrying the exact multipart request the connector built and the vendor's answers. A mirror path nobody can account for is indistinguishable from one that never ran."
    >
      <DataTable
        columns={[
          {
            key: 'outcome',
            header: 'Outcome',
            render: (row) => (
              <div className="min-w-0">
                <Badge tone={row.outcome === 'mirrored' ? 'insert' : row.outcome === 'failed' ? 'delete' : 'restore'}>
                  {row.outcome}
                </Badge>
                <p className="mt-1 text-[13px] text-muted-foreground">{outcomeNote(row.outcome)}</p>
              </div>
            ),
          },
          {
            key: 'request',
            header: 'Request',
            render: (row) => (
              <div className="min-w-0">
                <p className="font-mono text-[11px] text-foreground">
                  {requestSummary(row.request) || 'nothing was sent'}
                </p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  {attachSummary(row.attachment) || (row.error_detail || row.error_code)}
                </p>
              </div>
            ),
          },
          {
            key: 'at',
            header: 'When',
            render: (row) => <span className="font-mono text-[11px] text-muted-foreground">{row.at}</span>,
          },
          {
            key: 'simulated',
            header: 'Confirmed by',
            render: (row) => (
              <Badge tone={row.simulated ? 'restore' : 'insert'}>
                {row.simulated ? 'local simulation' : 'the CRM'}
              </Badge>
            ),
          },
        ]}
        rows={mirrors.mirrors}
        rowKey={(row) => row.id}
        empty="This room has made no mirror attempt yet."
      />
    </Section>
  )
}

function ConnectionForm({ vocabulary, busy, onSave }) {
  const [system, setSystem] = useState('hubspot')
  const [recordId, setRecordId] = useState('')
  const [folder, setFolder] = useState('')
  const [attachMode, setAttachMode] = useState(vocabulary.default_attach_mode)
  const [propertyName, setPropertyName] = useState('')

  const sourced = vocabulary.file_capable_systems.includes(system)

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            New connection
          </p>
          <p className="mt-2 font-display text-base font-semibold text-foreground">
            Push this room's documents to a CRM
          </p>
        </div>
        <span className="rounded-sm bg-accent-soft p-2 text-accent">
          <Glyph name="folder" size={20} />
        </span>
      </div>

      <form
        className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          onSave({
            system,
            crm_record_id: recordId.trim(),
            folder_id: folder.trim(),
            attach_mode: attachMode,
            file_property: attachMode === 'file_property' ? propertyName.trim() : '',
          })
        }}
      >
        <Field label="System" id="wf047-system" hint={sourced ? 'Documented by the research.' : 'Refused by name on every mirror.'}>
          <select
            id="wf047-system"
            className={inputClass}
            value={system}
            onChange={(event) => setSystem(event.target.value)}
          >
            {vocabulary.systems.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="CRM record that owns the attachment"
          id="wf047-record"
          hint="The deal, contact or account id. Without one the mirror records no_owner_record."
        >
          <input
            id="wf047-record"
            className={inputClass}
            value={recordId}
            onChange={(event) => setRecordId(event.target.value)}
            placeholder="fabrikam-dl-9001"
          />
        </Field>

        <Field
          label="Folder id (optional)"
          id="wf047-folder"
          hint={vocabulary.folder_recommendation}
        >
          <input
            id="wf047-folder"
            className={inputClass}
            value={folder}
            onChange={(event) => setFolder(event.target.value)}
            placeholder="fabrikam-folder-3300"
          />
        </Field>

        <Field label="Written as" id="wf047-attach" hint={vocabulary.attach_mode_notes[attachMode]}>
          <select
            id="wf047-attach"
            className={inputClass}
            value={attachMode}
            onChange={(event) => setAttachMode(event.target.value)}
          >
            {vocabulary.attach_modes.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </Field>

        {attachMode === 'file_property' ? (
          <Field
            label="File property name"
            id="wf047-property"
            hint="The vendor documents that a file-type property stores a file ID."
          >
            <input
              id="wf047-property"
              className={inputClass}
              value={propertyName}
              onChange={(event) => setPropertyName(event.target.value)}
              placeholder="proposal_attachment"
            />
          </Field>
        ) : null}

        <div className="sm:col-span-2">
          <Button variant="primary" icon="plus" type="submit" disabled={busy}>
            Save connection
          </Button>
        </div>
      </form>

      {sourced ? null : (
        <div className="mt-4">
          <Callout tone="warning">{vocabulary.unsourced_systems[system]}</Callout>
        </div>
      )}
    </Card>
  )
}

export default function CrmFileMirror() {
  const [roomId, setRoomId] = useState(lastRoom)
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  const rooms = useAsync(() => crmFileApi.rooms(), [])
  const vocabulary = useAsync(() => crmFileApi.vocabulary(), [])
  const vendors = useAsync(() => crmFileApi.vendors(), [])
  const inferences = useAsync(() => crmFileApi.inferences(), [])
  const active = selected || roomId
  const documents = useAsync(
    () => (active ? crmFileApi.documents(active) : Promise.resolve(null)),
    [active]
  )
  const files = useAsync(() => (active ? crmFileApi.files(active) : Promise.resolve(null)), [active])
  const mirrors = useAsync(() => (active ? crmFileApi.mirrors(active) : Promise.resolve(null)), [active])
  const connections = useAsync(
    () => (active ? crmFileApi.connections(active) : Promise.resolve(null)),
    [active]
  )
  const summary = useAsync(() => (active ? crmFileApi.summary(active) : Promise.resolve(null)), [active])

  const reload = useCallback(() => {
    documents.refetch()
    files.refetch()
    mirrors.refetch()
    connections.refetch()
    summary.refetch()
  }, [documents, files, mirrors, connections, summary])

  async function run(action) {
    setBusy(true)
    setNotice(null)
    try {
      const result = await action()
      setNotice(result)
      reload()
    } catch (error) {
      setNotice({ outcome: 'failed', error_detail: String(error?.message || error) })
    } finally {
      setBusy(false)
    }
  }

  /**
   * What the last action produced, in words.
   *
   * Three shapes reach here and all three carry their own text: a mirror answers
   * an outcome, a reconciliation answers not_due with a sentence, and the toggle
   * answers a note saying what did not happen. Rendering `outcome` alone would
   * print the word "undefined" beside the toggle, so each shape is read from the
   * field that carries its reason.
   */
  function noticeText(result) {
    if (!result) return ''
    if (result.outcome === 'not_due') {
      return `${vocab.reconcile_rule}. Nothing was pushed and no vendor was asked.`
    }
    if (result.note) return result.note
    if (result.error_detail) return result.error_detail
    if (result.mirrored !== undefined && result.candidates !== undefined) {
      return `Pushed ${result.mirrored} of ${result.candidates} candidate assets.`
    }
    return outcomeNote(result.outcome) || String(result.outcome || 'done')
  }

  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  if (rooms.loading || vocabulary.loading) return <Spinner label="Loading the CRM file mirror" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />

  const vocab = vocabulary.data
  const stats = summary.data

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">
            Mirror room documents into CRM files
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            A room document is uploaded to the CRM files endpoint as multipart/form-data, and the
            returned file id is written onto the CRM record that owns it. The room stores
            crm_file_id on the asset row, so re-uploads are skipped and revisions create a new file.
          </p>
        </div>
        <div className="w-full sm:w-72">
          <Field label="Room" id="wf047-room">
            <select
              id="wf047-room"
              className={inputClass}
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value)
                rememberRoom(event.target.value)
              }}
            >
              <option value="">Choose a room</option>
              {(rooms.data?.records || []).map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </header>

      {!active ? (
        <EmptyState
          title="Choose a room"
          description="The workflow is scoped to one room, because the CRM record that owns the attachment belongs to that room's deal, account or contact."
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Documents" value={stats?.assets ?? 0} hint="in this room" icon="audit" />
            <StatCard
              label="Mirrored"
              value={stats?.assets_by_state?.mirrored ?? 0}
              hint="the CRM holds these files"
              icon="database"
            />
            <StatCard
              label="Waiting"
              value={(stats?.assets_by_state?.pending ?? 0) + (stats?.assets_by_state?.stale ?? 0)}
              hint="no file id, or the bytes changed"
              icon="refresh"
            />
            <StatCard
              label="Mirror attempts"
              value={stats?.mirrors ?? 0}
              hint={`${stats?.simulated_mirrors ?? 0} answered by the local simulation`}
              icon="schema"
            />
          </div>

          {notice ? (
            <Callout tone={notice.outcome === 'failed' ? 'error' : 'info'}>
              <span className="font-mono text-[13px]">{notice.outcome || 'done'}</span>{' '}
              {noticeText(notice)}
            </Callout>
          ) : null}

          <Section
            title="Reconciliation"
            hint={`The rule is ${vocab.reconcile_rule}. Nothing in this workflow starts a thread: the pass is a route a scheduler calls, and a call inside the floor is answered not_due and asks the vendor for nothing.`}
            action={
              <Button
                icon="restore"
                disabled={busy}
                onClick={() => run(() => crmFileApi.reconcile(active, { force: true }))}
              >
                Run reconciliation now
              </Button>
            }
          >
            <Card>
              <div className="flex items-start gap-3">
                <span className="rounded-sm bg-accent-soft p-2 text-accent">
                  <Glyph name="reconcile" size={20} />
                </span>
                <div className="min-w-0">
                  <p className="text-sm text-foreground">
                    Floor between runs: <span className="font-mono">{vocab.default_poll_interval_seconds}s</span>
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    The research gives the rule and no cadence. This number is the same floor the
                    other polled vendor loops in this product derive, so one value governs all of
                    them. A connection may set its own.
                  </p>
                </div>
              </div>
            </Card>
          </Section>

          <Section
            title="Connections"
            hint="The vendor, the CRM record that owns the attachment, the attach mode and an optional folder. A second connection for the same room, system and owning record is refused, because two connections pushing one document into one record is the duplicate-file problem the idempotency rule exists to prevent."
          >
            {connections.error ? <ErrorNote error={connections.error} onRetry={connections.refetch} /> : null}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {(connections.data?.connections || []).map((connection) => (
                <ConnectionPanel
                  key={connection.id}
                  connection={connection}
                  onDelete={(target) =>
                    run(() => crmFileApi.deleteConnection(target.id, active))
                  }
                />
              ))}
            </div>
            <ConnectionForm
              vocabulary={vocab}
              busy={busy}
              onSave={(payload) => run(() => crmFileApi.saveConnection(payload, active))}
            />
          </Section>

          {documents.error ? <ErrorNote error={documents.error} onRetry={documents.refetch} /> : null}
          {documents.data ? (
            <DocumentsPanel
              documents={documents.data}
              busy={busy}
              onMirror={(row) => run(() => crmFileApi.mirror(active, row.id))}
              onToggle={(row, push) =>
                run(() => crmFileApi.patchAsset(active, row.id, { push_to_crm: push }))
              }
            />
          ) : null}

          {files.data ? <FilesPanel files={files.data} /> : null}
          {mirrors.data ? <MirrorLogPanel mirrors={mirrors.data} /> : null}

          <VendorPanel vendors={vendors.data?.vendors || []} />
          <InferencePanel inferences={inferences.data?.inferences || []} />

          <Section title="Raw API responses" hint="What the page rendered, verbatim.">
            <Card>
              <JsonView
                value={{
                  documents: documents.data,
                  files: files.data,
                  mirrors: mirrors.data,
                  summary: stats,
                }}
              />
            </Card>
          </Section>
        </>
      )}
    </div>
  )
}