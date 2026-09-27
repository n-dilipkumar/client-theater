/**
 * CRM provisioning: install the engagement object and its fields (WF-036).
 *
 * One screen for the researched flow, in the order a person actually runs it.
 * Pick a connection and a manifest, read the diff, then install. After that the
 * page is about two questions a deploy cannot answer on its own: is the object
 * complete, and is the sync key usable.
 *
 * Three things are deliberately on the page rather than in a comment:
 *
 *   - **The diff**, before anything is installed. The research lists "a dry-run
 *     diff view" among the surfaces in play, and the five actions it can report
 *     are the workflow's whole character: `create`, `unchanged`, `conflict`,
 *     `unmappable`, `left_in_place`. There is no sixth, because there is no code
 *     path that would update or drop a field.
 *   - **The requests each run sent**, spelled the way the researched APIs spell
 *     them. A reviewer can see what a deploy would have done without reading the
 *     code that decided it.
 *   - **The inference register.** The research is explicit about what it does not
 *     carry - the Salesforce half, and the widths the 900-byte key limit is
 *     measured in - and those are decisions, so they are named here and can be
 *     disagreed with by name.
 *
 * The manifest body is arbitrary JSON and the pickers are rendered from the
 * vocabulary endpoint, so a team adding a field ships a manifest version and
 * nothing on this page changes.
 */

import { useMemo, useState } from 'react'
import { absoluteTime, apiRequest, relativeTime } from '@/lib/api'
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
import { provisioningApi } from './api'
import Glyph from './icons'
import { DiffBadge, FindingRow, Note, StatTile } from './primitives'

const SECTIONS = [
  { id: 'diff', label: 'Diff and install', icon: 'diff' },
  { id: 'objects', label: 'Objects', icon: 'package' },
  { id: 'keys', label: 'Sync keys', icon: 'key' },
  { id: 'runs', label: 'Run log', icon: 'install' },
  { id: 'manifests', label: 'Manifests', icon: 'schema' },
  { id: 'inferences', label: 'What this infers', icon: 'warning' },
]

/** A connection picker, with the reason an unsupported one cannot be installed. */
function ConnectionPicker({ connections, value, onChange }) {
  return (
    <Field label="Connection" id="provision-connection">
      <select
        id="provision-connection"
        className={inputClass}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose a connection…</option>
        {connections.map((connection) => (
          <option key={connection.id} value={connection.id}>
            {connection.name} — {connection.vendor}
            {connection.supported ? '' : ' (unsupported by this research)'}
          </option>
        ))}
      </select>
    </Field>
  )
}

/** One manifest version picker, grouped by manifest id. */
function ManifestPicker({ manifests, value, onChange }) {
  const groups = useMemo(() => {
    const byId = new Map()
    for (const row of manifests) {
      const list = byId.get(row.manifest_id) || []
      list.push(row)
      byId.set(row.manifest_id, list)
    }
    return [...byId.entries()]
  }, [manifests])

  return (
    <Field label="Manifest" id="provision-manifest">
      <select
        id="provision-manifest"
        className={inputClass}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose a manifest…</option>
        {groups.map(([manifestId, versions]) =>
          versions.map((row) => (
            <option
              key={`${row.manifest_id}@${row.version}`}
              value={`${row.manifest_id}@${row.version}`}
            >
              {manifestId} {row.version}
            </option>
          )),
        )}
      </select>
    </Field>
  )
}

function splitSelection(value) {
  if (!value) return null
  const index = value.lastIndexOf('@')
  if (index < 0) return null
  return { manifestId: value.slice(0, index), version: value.slice(index + 1) }
}

/** One property row in the diff, with the reason for its verdict. */
function PropertyRow({ row }) {
  return (
    <li className="flex flex-wrap items-baseline gap-2 border-b border-border-subtle/15 py-2 text-xs last:border-0">
      <DiffBadge action={row.action} />
      <span className="min-w-0 flex-1 font-mono text-[13px] text-foreground">{row.name}</span>
      <span className="font-mono text-muted-foreground">{row.type}</span>
      {row.existing && (
        <span className="font-mono text-[11px] text-muted-foreground/80">
          CRM: {row.existing.type}
          {row.existing.field_type ? `/${row.existing.field_type}` : ''} · {row.existing.label}
        </span>
      )}
      <span className="w-full text-muted-foreground">{row.reason}</span>
    </li>
  )
}

/** The plan, rendered: object verdict, every property, and the key. */
function PlanCard({ plan, vocabulary }) {
  if (!plan) return null
  const keyStatuses = vocabulary?.key_statuses || []
  const keyTone = { Active: 'insert', Failed: 'delete', Pending: 'neutral', 'In Progress': 'update' }

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm font-semibold text-foreground">
          {plan.manifest_id} {plan.manifest_version}
        </h3>
        <Badge tone="neutral">{plan.vendor}</Badge>
        <DiffBadge action={plan.object.action === 'create' ? 'create' : 'unchanged'} />
        <span className="text-xs text-muted-foreground">
          {plan.object.name}
          {plan.object.crm_object_id ? ` → ${plan.object.crm_object_id}` : ''}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {Object.entries(plan.counts)
          .filter(([, count]) => count > 0)
          .map(([action, count]) => (
            <DiffBadge key={action} action={action} count={count} />
          ))}
      </div>

      <div>
        <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Properties
        </p>
        <ul>
          {plan.properties.map((row) => (
            <PropertyRow key={row.name} row={row} />
          ))}
        </ul>
      </div>

      {plan.left_in_place.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Left in place — nothing was dropped
          </p>
          <ul>
            {plan.left_in_place.map((row) => (
              <li key={row.name} className="flex flex-wrap items-baseline gap-2 py-1 text-xs">
                <DiffBadge action="left_in_place" />
                <span className="font-mono text-[13px] text-foreground">{row.name}</span>
                <span className="text-muted-foreground">{row.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="border-t border-border-subtle/25 pt-3 text-xs">
        <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Sync key
        </p>
        <p className="flex flex-wrap items-center gap-2">
          {keyStatuses.includes(plan.key.action) ? (
            <Badge tone={keyTone[plan.key.action]}>{plan.key.action}</Badge>
          ) : (
            <Badge tone="neutral">{plan.key.action}</Badge>
          )}
          <span className="font-mono text-foreground">
            {(plan.key.columns || []).join(', ') || 'none declared'}
          </span>
          <span className="text-muted-foreground">{plan.key.reason}</span>
        </p>
      </div>

      {plan.findings.length > 0 && (
        <div className="border-t border-border-subtle/25 pt-3">
          <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Findings
          </p>
          <ul>
            {plan.findings.map((finding, index) => (
              <FindingRow key={`${finding.code}-${index}`} finding={finding} />
            ))}
          </ul>
        </div>
      )}

      {!plan.complete && (
        <Note tone="warn">
          This install would skip at least one field. The object is left marked
          incomplete so a deploy job can assert on it.
        </Note>
      )}
    </Card>
  )
}

/** One installer run, with the exact requests it sent. */
function RunRow({ run, onOpen }) {
  const [open, setOpen] = useState(false)
  const counts = run.counts || {}

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full min-h-11 flex-wrap items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Badge tone={run.outcome === 'created' ? 'insert' : run.dry_run ? 'restore' : 'neutral'}>
          {run.dry_run ? 'dry run' : run.outcome}
        </Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {run.manifest_id} {run.manifest_version}
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          {counts.created ?? 0} created
        </span>
        {counts.conflicts > 0 && (
          <span className="font-mono text-xs text-sky-300">{counts.conflicts} conflict</span>
        )}
        {counts.skipped > 0 && (
          <span className="font-mono text-xs text-destructive">{counts.skipped} skipped</span>
        )}
        {counts.left_in_place > 0 && (
          <span className="font-mono text-xs text-amber-300">
            {counts.left_in_place} left in place
          </span>
        )}
        <span className="shrink-0 font-mono text-xs text-muted-foreground" title={absoluteTime(run.created_at)}>
          {relativeTime(run.created_at)}
        </span>
      </button>

      {open && (
        <div className="space-y-3 rounded-lg border border-border-subtle/25 bg-background/40 p-3">
          <dl className="grid gap-2 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">Connection</dt>
              <dd className="font-mono text-foreground">{run.connection_id}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">CRM object id</dt>
              <dd className="font-mono text-foreground">{run.crm_object_id || '—'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Key</dt>
              <dd className="font-mono text-foreground">
                {run.key?.action || '—'} {run.key?.status ? `· ${run.key.status}` : ''}
              </dd>
            </div>
          </dl>

          <div>
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Requests sent
            </p>
            {/* The researched paths, as this run would have sent them. A
                reviewer reads the deploy rather than the code that made it. */}
            <ul className="space-y-1">
              {(run.requests || []).map((request, index) => (
                <li key={`${request.method}-${request.path}-${index}`} className="text-xs">
                  <span className="font-mono text-muted-foreground">{request.method}</span>{' '}
                  <span className="font-mono text-foreground">{request.path}</span>
                  {request.body && (
                    <JsonView value={request.body} />
                  )}
                </li>
              ))}
            </ul>
          </div>

          {(run.skipped?.length > 0 || run.conflicts?.length > 0 || run.left_in_place?.length > 0) && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                What was not done
              </p>
              <ul className="space-y-1">
                {run.conflicts?.map((row) => (
                  <li key={`c-${row.property}`} className="flex gap-2 text-xs text-sky-300">
                    <Glyph name="warning" size={14} />
                    <span>
                      {row.property}: {row.reason}
                    </span>
                  </li>
                ))}
                {run.skipped?.map((row) => (
                  <li key={`s-${row.property}`} className="flex gap-2 text-xs text-destructive">
                    <Glyph name="close" size={14} />
                    <span>
                      {row.property}: {row.reason}
                    </span>
                  </li>
                ))}
                {run.left_in_place?.map((row) => (
                  <li key={`l-${row.name}`} className="flex gap-2 text-xs text-amber-300">
                    <Glyph name="link" size={14} />
                    <span>
                      {row.name}: {row.reason}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {run.findings?.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Findings
              </p>
              <ul>
                {run.findings.map((finding, index) => (
                  <FindingRow key={`${finding.code}-${index}`} finding={finding} />
                ))}
              </ul>
            </div>
          )}

          <Button onClick={() => onOpen(run.id)}>Open this run</Button>
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
        className="flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {entry.topic}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{entry.id}</span>
      </button>

      {open && (
        <div className="space-y-2 rounded-lg border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">
              What the research says
            </p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="font-medium tracking-wide text-muted-foreground uppercase">Why</p>
            <p className="mt-0.5 text-foreground/90">{entry.why}</p>
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

export default function CrmProvisioning() {
  const [section, setSection] = useState('diff')
  const [connectionId, setConnectionId] = useState('')
  const [manifestSelection, setManifestSelection] = useState('')
  const [roomId, setRoomId] = useState('')
  const [outcome, setOutcome] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [noticeError, setNoticeError] = useState(null)
  const [openRun, setOpenRun] = useState(null)

  const vocabulary = useAsync(() => provisioningApi.vocabulary(), [])
  const connections = useAsync(() => provisioningApi.listConnections(), [])
  const manifests = useAsync(() => provisioningApi.listManifests(), [])
  const objects = useAsync(() => provisioningApi.listObjects(), [])
  const keys = useAsync(() => provisioningApi.listKeys(), [])
  const runs = useAsync(
    () => provisioningApi.listInstallations({ limit: 60, outcome }),
    [outcome],
  )
  const inferences = useAsync(() => provisioningApi.inferences(), [])

  const selection = splitSelection(manifestSelection)
  const plan = useAsync(
    () =>
      connectionId && selection
        ? provisioningApi.diff({
            connection_id: connectionId,
            manifest_id: selection.manifestId,
            version: selection.version,
          })
        : Promise.resolve(null),
    [connectionId, manifestSelection],
  )

  const room = useAsync(
    () => (roomId ? provisioningApi.roomSummary(roomId) : Promise.resolve(null)),
    [roomId],
  )

  const connectionRows = connections.data?.connections || []
  const manifestRows = manifests.data?.manifests || []
  const objectRows = objects.data?.objects || []
  const keyRows = keys.data?.keys || []
  const runRows = runs.data?.installations || []
  const chosen = connectionRows.find((row) => row.id === connectionId)

  async function install(dryRun) {
    if (!connectionId || !selection) return
    setBusy(true)
    setNotice(null)
    setNoticeError(null)
    try {
      const result = await provisioningApi.install({
        connection_id: connectionId,
        manifest_id: selection.manifestId,
        version: selection.version,
        dry_run: dryRun,
      })
      const created = result.created
      setNotice(
        dryRun
          ? `Preview only. ${created} create request${created === 1 ? '' : 's'} would be sent, and none were.`
          : result.outcome === 'unchanged'
            ? 'Nothing to do. Every property was already present, so nothing was created.'
            : `Installed. Created ${created} object${created === 1 ? '' : 's'} and field${created === 1 ? '' : 's'}.` +
              (result.complete ? '' : ' One or more fields were skipped; the object is marked incomplete.'),
      )
      if (!dryRun) {
        plan.refetch()
        objects.refetch()
        keys.refetch()
        runs.refetch()
        if (roomId) room.refetch()
      }
    } catch (error) {
      setNoticeError(error)
    } finally {
      setBusy(false)
    }
  }

  async function driveKey(keyId, action) {
    setBusy(true)
    setNotice(null)
    setNoticeError(null)
    try {
      const result = action === 'poll' ? await provisioningApi.pollKey(keyId) : await provisioningApi.reactivateKey(keyId)
      setNotice(
        action === 'poll'
          ? `Key index is now ${result.status}.`
          : result.reactivated
            ? 'Key re-armed. Its index build starts again from Pending.'
            : `Key left as it was. Its index is already ${result.status}, which is not half-provisioned.`,
      )
      keys.refetch()
      if (roomId) room.refetch()
    } catch (error) {
      setNoticeError(error)
    } finally {
      setBusy(false)
    }
  }

  const stats = useMemo(
    () => [
      { label: 'Connections', value: connectionRows.length, path: 'building', hint: `${connectionRows.filter((c) => c.supported).length} installable here` },
      { label: 'CRM objects', value: objectRows.length, path: 'package', hint: `${objects.data?.incomplete ?? 0} incomplete` },
      { label: 'Keys active', value: keys.data?.summary?.Active ?? 0, path: 'key', hint: `${keys.data?.summary?.Failed ?? 0} failed, ${keys.data?.summary?.Pending ?? 0} building` },
      { label: 'Fields created', value: runs.data?.totals?.created ?? 0, path: 'check', hint: `${runs.data?.totals?.left_in_place ?? 0} left in place` },
    ],
    [connectionRows, objectRows, keys.data, runs.data],
  )

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">CRM provisioning</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Install the sales-room engagement object and its fields into a CRM. The installer reads
          the CRM&rsquo;s live schema, creates only what is missing, and never renames or drops a
          field. Re-running it is a no-op, so it can run on every deploy.
        </p>
      </header>

      {noticeError && <ErrorNote error={noticeError} />}
      {notice && <Note>{notice}</Note>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <StatTile key={stat.label} {...stat} />
        ))}
      </div>

      {/* Section switcher. A tablist so arrow-key semantics and the aria
          relationship are right, and so the choice is shareable by URL. */}
      <div role="tablist" aria-label="CRM provisioning sections" className="flex flex-wrap gap-2">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={section === item.id}
            aria-controls={`panel-${item.id}`}
            onClick={() => setSection(item.id)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-4 text-sm
              transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-accent ${
                section === item.id
                  ? 'bg-accent/15 font-medium text-accent'
                  : 'bg-muted text-muted-foreground hover:border-border-subtle hover:text-foreground'
              }`}
          >
            <Glyph name={item.icon} />
            {item.label}
          </button>
        ))}
      </div>

      {/* -- diff and install -- */}
      {section === 'diff' && (
        <div id="panel-diff" role="tabpanel" aria-labelledby="tab-diff" className="space-y-4">
          <Card>
            <div className="grid gap-4 sm:grid-cols-2">
              <ConnectionPicker
                connections={connectionRows}
                value={connectionId}
                onChange={setConnectionId}
              />
              <ManifestPicker
                manifests={manifestRows}
                value={manifestSelection}
                onChange={setManifestSelection}
              />
            </div>

            {chosen && !chosen.supported && (
              <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-300">
                <Glyph name="warning" size={16} />
                <span>
                  This connection&rsquo;s vendor is not one this workflow has evidence for.{' '}
                  {chosen.unsupported_reason}
                </span>
              </p>
            )}

            <div className="mt-4 flex flex-wrap items-end gap-3">
              <Button icon="diff" onClick={() => plan.refetch()} disabled={!connectionId || !selection}>
                Read the diff
              </Button>
              <Button
                icon="install"
                variant="primary"
                disabled={busy || !connectionId || !selection || (chosen && !chosen.supported)}
                onClick={() => install(false)}
              >
                {busy ? 'Working…' : 'Install integration package'}
              </Button>
              <Button
                variant="ghost"
                disabled={busy || !connectionId || !selection}
                onClick={() => install(true)}
              >
                Dry run
              </Button>
              <p className="text-xs text-muted-foreground">
                The same endpoint an admin presses and a CI job calls.
              </p>
            </div>
          </Card>

          {plan.loading && <Spinner label="Reading the CRM's live schema" />}
          {plan.error && <ErrorNote error={plan.error} onRetry={plan.refetch} />}

          {plan.data && (
            <PlanCard plan={plan.data} vocabulary={vocabulary.data} />
          )}

          {!plan.data && !plan.loading && !plan.error && (
            <EmptyState
              title="No diff read yet"
              description="Choose a connection and a manifest, then read the diff. It is a read: it cannot create anything."
            />
          )}
        </div>
      )}

      {/* -- objects -- */}
      {section === 'objects' && (
        <div id="panel-objects" role="tabpanel" aria-labelledby="tab-objects" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">Installed objects</h2>
              <p className="max-w-2xl text-sm text-muted-foreground">
                Each row is the mapping a later sync needs: the room&rsquo;s own object id, and the
                CRM&rsquo;s object id. Only fields this workflow created are listed; a field a
                tenant added in the vendor&rsquo;s own UI is found by the diff and is deliberately
                not claimed here.
              </p>
            </div>
            <Button icon="refresh" onClick={objects.refetch}>
              Refresh
            </Button>
          </div>

          {objects.loading && <Spinner label="Loading objects" />}
          {objects.error && <ErrorNote error={objects.error} onRetry={objects.refetch} />}

          {!objects.loading && !objects.error && objectRows.length === 0 && (
            <EmptyState
              title="Nothing installed yet"
              description="Run the installer from the diff tab and the mapping it records will appear here."
            />
          )}

          <ul className="grid gap-4 lg:grid-cols-2">
            {objectRows.map((row) => (
              <li key={row.id}>
                <Card className="card-hover h-full">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate font-mono text-base font-semibold text-foreground">
                        {row.crm_object_name}
                      </h3>
                      <p className="mt-0.5 text-sm text-muted-foreground">{row.object_label}</p>
                    </div>
                    {row.complete === false ? (
                      <Badge tone="delete">incomplete</Badge>
                    ) : (
                      <Badge tone="insert">complete</Badge>
                    )}
                  </div>

                  <dl className="mt-4 space-y-1.5 text-xs">
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">Room object id</dt>
                      <dd className="min-w-0 font-mono text-foreground">{row.room_object_id}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">CRM object id</dt>
                      <dd className="min-w-0 font-mono text-accent">{row.crm_object_id}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">Vendor</dt>
                      <dd className="min-w-0 font-mono text-foreground">{row.vendor}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">Manifest</dt>
                      <dd className="min-w-0 font-mono text-foreground">
                        {row.manifest_id} {row.manifest_version}
                      </dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">Fields</dt>
                      <dd className="min-w-0 font-mono text-foreground">
                        {row.declared_properties} declared
                        {row.skipped_properties?.length
                          ? `, ${row.skipped_properties.join(', ')} skipped`
                          : ''}
                      </dd>
                    </div>
                  </dl>
                </Card>
              </li>
            ))}
          </ul>

          <section aria-labelledby="room-reads-heading" className="space-y-3">
            <h2 id="room-reads-heading" className="font-mono text-lg font-semibold">
              What one room can write into
            </h2>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Provisioning is scoped to a connection, not to a room, so the room appears here. The
              researched result is that a room now has a first-class CRM-native object to write
              engagement rows into, and this is the read that answers it.
            </p>

            <Card>
              <div className="flex flex-wrap items-end gap-3">
                <div className="min-w-[16rem] flex-1">
                  <Field
                    label="Room id"
                    id="provision-room"
                    hint="The record id of a room. Provisioning itself is not room-scoped."
                  >
                    <input
                      id="provision-room"
                      className={inputClass}
                      value={roomId}
                      onChange={(event) => setRoomId(event.target.value)}
                      placeholder="room_…"
                    />
                  </Field>
                </div>
                <Button icon="search" onClick={() => room.refetch()} disabled={!roomId}>
                  Read this room
                </Button>
              </div>
            </Card>

            {room.data && (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatTile label="Connections" value={room.data.connections} path="building" />
                <StatTile label="CRM objects" value={room.data.objects} path="package" />
                <StatTile
                  label="Keys active"
                  value={room.data.keys_active}
                  hint={`${room.data.needs_repair} need a person`}
                  path="key"
                  tone={room.data.needs_repair > 0 ? 'warn' : 'good'}
                />
                <StatTile label="Installations" value={room.data.installations} path="install" />
              </div>
            )}

            {room.data?.unsupported_connections?.length > 0 && (
              <Note tone="warn">
                {room.data.unsupported_connections.join(', ')} — this workflow has no sourced
                provisioning behaviour for that vendor.
              </Note>
            )}
          </section>
        </div>
      )}

      {/* -- keys -- */}
      {section === 'keys' && (
        <div id="panel-keys" role="tabpanel" aria-labelledby="tab-keys" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">Sync keys</h2>
              <p className="max-w-2xl text-sm text-muted-foreground">
                The key the engagement rows are made unique by. The index builds in the background,
                so an install does not block on it, and a build that fails is repaired with the
                vendor&rsquo;s own reactivate call rather than by dropping the table.
              </p>
            </div>
            <Button icon="refresh" onClick={keys.refetch}>
              Refresh
            </Button>
          </div>

          {keys.loading && <Spinner label="Loading keys" />}
          {keys.error && <ErrorNote error={keys.error} onRetry={keys.refetch} />}

          {!keys.loading && !keys.error && keyRows.length === 0 && (
            <EmptyState
              title="No sync key requested"
              description="Dataverse creates an alternate key from a manifest that declares one. HubSpot has no sourced alternate-key call in this build, so a HubSpot manifest is installed without one and says so."
            />
          )}

          {keyRows.length > 0 && (
            <div className="overflow-x-auto">
              <Card>
                <table className="w-full min-w-[760px] text-left text-sm">
                  <caption className="sr-only">
                    Alternate keys, their background index state, and the repair action
                  </caption>
                  <thead>
                    <tr className="border-b border-border-subtle/30 text-xs tracking-wide text-muted-foreground uppercase">
                      <th scope="col" className="px-4 py-3 font-medium">Key</th>
                      <th scope="col" className="px-4 py-3 font-medium">Columns</th>
                      <th scope="col" className="px-4 py-3 font-medium">Index status</th>
                      <th scope="col" className="px-4 py-3 font-medium">Reactivated</th>
                      <th scope="col" className="px-4 py-3 font-medium">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {keyRows.map((key) => (
                      <tr key={key.id} className="border-b border-border-subtle/15 last:border-0">
                        <td className="px-4 py-3">
                          <span className="flex items-center gap-2 font-mono text-[13px] text-foreground">
                            <Glyph name="key" size={14} />
                            {key.key_id}
                          </span>
                          <span className="mt-0.5 block font-mono text-[11px] text-muted-foreground">
                            {key.async_job_id} · {key.polls ?? 0} look
                            {key.polls === 1 ? '' : 's'}
                          </span>
                        </td>
                        <td className="px-4 py-3 font-mono text-foreground">
                          {(key.columns || []).join(', ')}
                        </td>
                        <td className="px-4 py-3">
                          <Badge
                            tone={
                              key.status === 'Active'
                                ? 'insert'
                                : key.status === 'Failed'
                                  ? 'delete'
                                  : 'update'
                            }
                          >
                            {key.status}
                          </Badge>
                        </td>
                        <td className="px-4 py-3 font-mono text-muted-foreground">
                          {key.reactivated_count || 0}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              icon="clock"
                              disabled={busy || key.status === 'Active'}
                              onClick={() => driveKey(key.id, 'poll')}
                            >
                              Check build
                            </Button>
                            <Button
                              icon="refresh"
                              disabled={busy || key.status !== 'Failed'}
                              onClick={() => driveKey(key.id, 'reactivate')}
                            >
                              Reactivate
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            </div>
          )}
        </div>
      )}

      {/* -- run log -- */}
      {section === 'runs' && (
        <div id="panel-runs" role="tabpanel" aria-labelledby="tab-runs" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-mono text-lg font-semibold">Run log</h2>
              <p className="max-w-2xl text-sm text-muted-foreground">
                Every installer run, with the requests it sent. Expand a row to see the researched
                endpoints and bodies it used, and what it declined to do.
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Outcome" id="run-filter-outcome">
                <select
                  id="run-filter-outcome"
                  className={inputClass}
                  value={outcome}
                  onChange={(event) => setOutcome(event.target.value)}
                >
                  <option value="">All</option>
                  {(vocabulary.data?.outcomes || []).map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </Field>
              <Button icon="refresh" onClick={runs.refetch}>
                Refresh
              </Button>
            </div>
          </div>

          {runs.loading && <Spinner label="Loading runs" />}
          {runs.error && <ErrorNote error={runs.error} onRetry={runs.refetch} />}

          {!runs.loading && !runs.error && runRows.length === 0 && (
            <EmptyState
              title="No installer runs yet"
              description="Install from the diff tab, or run a dry run, and both land here."
            />
          )}

          {runRows.length > 0 && (
            <Card className="p-4">
              <ul>
                {runRows.map((run) => (
                  <RunRow key={run.id} run={run} onOpen={setOpenRun} />
                ))}
              </ul>
            </Card>
          )}

          {openRun && (
            <Card>
              <div className="flex items-center justify-between gap-3">
                <h3 className="font-mono text-sm font-semibold text-foreground">Run {openRun}</h3>
                <Button variant="ghost" onClick={() => setOpenRun(null)}>
                  Close
                </Button>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                The full plan this run acted on is in the run record above and in the generic
                records API under the <span className="font-mono">crm_installation</span> collection.
              </p>
            </Card>
          )}
        </div>
      )}

      {/* -- manifests -- */}
      {section === 'manifests' && (
        <div id="panel-manifests" role="tabpanel" aria-labelledby="tab-manifests" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">Manifests</h2>
            <p className="max-w-2xl text-sm text-muted-foreground">
              The descriptor is data: a deployment ships its own, and adding a CRM field is a new
              version rather than a code change. A version already published is never rewritten,
              because an install that ran against it must still be able to say which body it
              installed.
            </p>
          </div>

          {manifests.loading && <Spinner label="Loading manifests" />}
          {manifests.error && <ErrorNote error={manifests.error} onRetry={manifests.refetch} />}

          <ul className="grid gap-4 lg:grid-cols-2">
            {manifestRows.map((row) => (
              <li key={row.id}>
                <Card className="card-hover h-full">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate font-mono text-base font-semibold text-foreground">
                        {row.manifest_id} {row.version}
                      </h3>
                      <p className="mt-0.5 text-sm text-muted-foreground">{row.name}</p>
                    </div>
                    <Badge tone="neutral">{row.properties?.length ?? 0} fields</Badge>
                  </div>
                  {row.description && (
                    <p className="mt-2 text-sm text-muted-foreground">{row.description}</p>
                  )}
                  <dl className="mt-3 space-y-1.5 text-xs">
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">CRM object</dt>
                      <dd className="font-mono text-foreground">{row.object?.name}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">Room object id</dt>
                      <dd className="font-mono text-foreground">{row.room_object_id}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="shrink-0 text-muted-foreground">Sync key</dt>
                      <dd className="min-w-0 font-mono text-foreground">
                        {(row.sync_key?.columns || []).map((c) => c.name).join(', ') || 'none'}
                      </dd>
                    </div>
                  </dl>
                </Card>
              </li>
            ))}
          </ul>

          {vocabulary.data && (
            <Card className="space-y-2">
              <h3 className="font-mono text-sm font-semibold text-foreground">
                What each vendor will create
              </h3>
              <p className="text-xs text-muted-foreground">
                The bodies a property create will carry, from the vocabulary endpoint rather than
                from a list compiled into this page.
              </p>
              {vocabulary.data.vendors_detail.map((vendor) => (
                <div key={vendor.vendor} className="rounded-lg border border-border-subtle/25 p-3">
                  <p className="font-mono text-[13px] text-foreground">
                    {vendor.label}{' '}
                    <Badge tone={vendor.supports_alternate_key ? 'insert' : 'neutral'}>
                      {vendor.supports_alternate_key ? 'alternate key' : 'no alternate key'}
                    </Badge>
                  </p>
                  <ul className="mt-1.5 space-y-0.5 font-mono text-[11px] text-muted-foreground">
                    <li>read: {vendor.schema_read.join('  ')}</li>
                    <li>create object: {vendor.object_create}</li>
                    <li>create property: {vendor.property_create}</li>
                    {vendor.key_create && <li>create key: {vendor.key_create}</li>}
                    {vendor.key_reactivate && <li>reactivate key: {vendor.key_reactivate}</li>}
                    <li>required fields: {vendor.required_property_fields.join(', ')}</li>
                  </ul>
                </div>
              ))}
            </Card>
          )}
        </div>
      )}

      {/* -- what this infers -- */}
      {section === 'inferences' && (
        <div id="panel-inferences" role="tabpanel" aria-labelledby="tab-inferences" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">What this infers</h2>
            <p className="max-w-2xl text-sm text-muted-foreground">
              The endpoints, the required property fields, the two key limits and the four index
              statuses are all sourced. The rest is judgement: the parts of this workflow the
              research makes no claims about. Each is listed with the reason for the choice and how
              to change it.
            </p>
          </div>

          {inferences.loading && <Spinner label="Loading inferences" />}
          {inferences.error && <ErrorNote error={inferences.error} onRetry={inferences.refetch} />}

          {inferences.data && (
            <>
              <Card className="p-4">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">The research&rsquo;s own gap: </span>
                  &ldquo;{inferences.data.sourced_quotes.gap}&rdquo;
                </p>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">And the rule this build exists to satisfy: </span>
                  &ldquo;{inferences.data.sourced_quotes.idempotency}&rdquo;
                </p>
              </Card>

              <Card className="p-4">
                <ul>
                  {(inferences.data.inferences || []).map((entry) => (
                    <InferenceRow key={entry.id} entry={entry} />
                  ))}
                </ul>
              </Card>
            </>
          )}
        </div>
      )}
    </div>
  )
}
