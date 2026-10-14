/**
 * WF-137: version control, brand governance and post-sale room continuity.
 *
 * Every publish is snapshotted into an immutable version a seller can preview,
 * restore or branch. The brand token set is applied to each room so an off-brand
 * room is a named finding. At closed-won the room is repurposed, not rebuilt,
 * into a phased onboarding portal with its own retention policy.
 *
 * The page reads its states, triggers and decisions from the server's
 * `/vocabulary` and `/inferences` routes rather than repeating them, so the panel
 * cannot tell a seller a value the rules do not accept.
 *
 * Things this page deliberately does not do, each named from the research and
 * listed in the panel below: a live push of refreshed content into every live
 * room (the refresh is a read-through), an LMS embed, a content library or DAM
 * connection, a CRM close event, and messaging control.
 */

import { useState } from 'react'

import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  governanceApi,
  listRooms,
  shortTimestamp,
  wholeInRange,
} from './api'
import {
  FindingList,
  InlineNote,
  PhaseList,
  RetentionWindow,
  StateBadge,
  VersionRow,
} from './primitives'

/** Everything scoped to one room, loaded together so the panels cannot disagree. */
function useRoom(roomId) {
  const brand = useAsync(
    () => (roomId ? governanceApi.roomBrand(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const history = useAsync(
    () => (roomId ? governanceApi.history(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const onboarding = useAsync(
    () => (roomId ? governanceApi.onboarding(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const retention = useAsync(
    () => (roomId ? governanceApi.roomRetention(roomId) : Promise.resolve(null)),
    [roomId],
  )
  return { brand, history, onboarding, retention }
}

function GovernancePage() {
  const [roomId, setRoomId] = useState('')
  const [note, setNote] = useState(null)
  const [problem, setProblem] = useState(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(null)

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => governanceApi.vocabulary(), [])
  const summary = useAsync(() => governanceApi.summary(), [])
  const brandBoard = useAsync(() => governanceApi.brand(), [])
  const retentionBoard = useAsync(() => governanceApi.retentionBoard(), [])
  const room = useRoom(roomId)

  function refetchAll() {
    summary.refetch()
    brandBoard.refetch()
    retentionBoard.refetch()
    room.brand.refetch()
    room.history.refetch()
    room.onboarding.refetch()
    room.retention.refetch()
  }

  async function act(fn, success) {
    setBusy(true)
    setProblem(null)
    setNote(null)
    try {
      const result = await fn()
      setNote(typeof success === 'function' ? success(result) : success)
      refetchAll()
      return result
    } catch (error) {
      setProblem(error)
      return null
    } finally {
      setBusy(false)
    }
  }

  const loading = rooms.loading || vocabulary.loading || summary.loading
  const error = rooms.error || vocabulary.error || summary.error
  if (loading && !summary.data) return <Spinner label="Loading governance" />
  if (error) return <ErrorNote error={error} onRetry={refetchAll} />

  const counts = summary.data || {}
  const board = brandBoard.data
  const retentionDue = retentionBoard.data?.due ?? 0
  const notBuilt = vocabulary.data?.not_implemented || []
  const brandSet = board?.brand_set

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">Governance and version control</h1>
        <p className="text-sm text-muted-foreground">
          Every publish is snapshotted into an immutable version you can preview, restore or
          branch. The brand token set is applied to each room, and at closed-won the room is
          repurposed, not rebuilt, into a phased onboarding portal.
        </p>
      </header>

      {note && <InlineNote tone="success">{note}</InlineNote>}
      {problem && <ErrorNote error={problem} />}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Governed rooms"
          value={counts.rooms_tracked ?? 0}
          icon="rooms"
          hint="Rooms with a governance write"
        />
        <StatCard label="Snapshots" value={counts.snapshots ?? 0} icon="database" hint="Immutable versions" />
        <StatCard
          label="Brand set"
          value={`v${counts.brand_set_version ?? 1}`}
          icon="schema"
          hint={`${board?.non_compliant ?? 0} off brand, ${board?.stale ?? 0} stale`}
        />
        <StatCard
          label="Retention due"
          value={retentionDue}
          icon="audit"
          hint={`${counts.retention_policies ?? 0} policy row(s)`}
        />
      </div>

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Brand and messaging control</h2>
        <p className="text-xs text-muted-foreground">
          The approved token set is what every room is measured against. A room that carries an
          unapproved value is a named finding below, and applying the brand writes the approved
          tokens onto it.
        </p>
        {brandSet && (
          <div className="flex flex-wrap gap-2">
            {Object.entries(brandSet.tokens || {}).map(([token, value]) => (
              <span key={token} className="flex items-center gap-2 rounded-sm border border-border-subtle px-3 py-1.5">
                <span className="text-xs text-muted-foreground">{token}</span>
                <span className="font-mono text-xs text-foreground">{value}</span>
              </span>
            ))}
          </div>
        )}
        {(board?.rooms || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No room has a governance row yet. Pick a room below and apply the brand.
          </p>
        ) : (
          <ul className="flex flex-col" aria-label="Brand compliance by room">
            {board.rooms.map((row) => (
              <li
                key={row.room_id}
                className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle py-2 last:border-b-0"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm text-foreground">
                    {row.room_name || row.room_id}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">{row.room_id}</span>
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  <StateBadge state={row.lifecycle_state} />
                  <span className="text-xs text-muted-foreground">
                    {row.compliant ? 'On brand' : `${row.findings.length} finding(s)`}
                  </span>
                  {row.stale && <span className="text-xs text-warning">Behind the current set</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">Rooms</h2>
        <Field label="Room" id="wf137-room" hint="A governed room: its history, brand and plan.">
          <select
            id="wf137-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setPreview(null)
              setNote(null)
              setProblem(null)
            }}
          >
            <option value="">Pick a room</option>
            {(rooms.data?.records || []).map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.data?.name || entry.id}
              </option>
            ))}
          </select>
        </Field>

        {!roomId ? (
          <EmptyState
            title="No room selected"
            description="Pick a room to see its brand findings, its version history, its onboarding plan and its retention policy."
          />
        ) : (
          <div className="flex flex-col gap-6">
            <RoomBrand
              brand={room.brand.data}
              busy={busy}
              onApply={() =>
                act(() => governanceApi.applyBrand(roomId), 'Applied the approved brand tokens.')
              }
            />

            <RoomHistory
              history={room.history.data}
              busy={busy}
              preview={preview}
              onPublish={() =>
                act(
                  () =>
                    governanceApi.snapshot(roomId, {
                      label: `Publish ${new Date().toISOString().slice(0, 10)}`,
                      content: { source: 'page' },
                    }),
                  (created) => `Published as version ${created.version}.`,
                )
              }
              onCheckpoint={() =>
                act(
                  () =>
                    governanceApi.snapshot(
                      roomId,
                      { label: 'Editing session', content: { source: 'page' } },
                      'session',
                    ),
                  (created) => `Checkpointed as version ${created.version}.`,
                )
              }
              onPreview={(version) =>
                act(
                  async () => {
                    const body = await governanceApi.preview(roomId, version)
                    setPreview(body)
                    return body
                  },
                  (body) =>
                    body.matches_head
                      ? `Version ${body.version} matches the head.`
                      : `Version ${body.version} differs in ${body.changes.length} field(s).`,
                )
              }
              onRestore={(version) =>
                act(
                  () => governanceApi.restore(roomId, version),
                  (created) => `Restored version ${version} as new version ${created.version}.`,
                )
              }
              onBranch={(version) =>
                act(
                  () => governanceApi.branch(roomId, version, {}),
                  (created) => `Branched version ${version} into a new room ${created.room_id}.`,
                )
              }
            />

            <RoomOnboarding
              plan={room.onboarding.data}
              busy={busy}
              onRepurpose={() =>
                act(
                  () => governanceApi.repurpose(roomId, {}),
                  'Repurposed the room into a phased onboarding portal.',
                )
              }
              onAdvance={() =>
                act(
                  () => governanceApi.advance(roomId),
                  (state) =>
                    state.lifecycle_state === 'onboarded'
                      ? 'Every phase is complete. The onboarding is done.'
                      : `Advanced to ${state.active_phase?.name || 'the next phase'}.`,
                )
              }
              onComplete={(phaseKey, milestoneKey) =>
                act(
                  () =>
                    governanceApi.completeMilestone(roomId, {
                      phase_key: phaseKey,
                      milestone_key: milestoneKey,
                    }),
                  `Closed milestone ${milestoneKey}.`,
                )
              }
            />

            <RoomRetention
              retention={room.retention.data}
              busy={busy}
              onSet={(payload) =>
                act(() => governanceApi.setRetention(roomId, payload), 'Saved the retention policy.')
              }
              onApply={() =>
                act(
                  () => governanceApi.applyRetention(roomId),
                  (result) =>
                    result.applied === 'deleted'
                      ? 'Applied the schedule: the governance rows were soft-deleted.'
                      : 'Applied the schedule: the room was archived.',
                )
              }
            />
          </div>
        )}
      </Card>

      <Card className="flex flex-col gap-2">
        <h2 className="text-base font-semibold text-foreground">Not built here</h2>
        <p className="text-xs text-muted-foreground">
          The research names these, and they belong to another workflow or to a deployment. They are
          listed so their absence reads as a decision rather than an oversight.
        </p>
        <ul className="list-inside list-disc text-xs text-muted-foreground">
          {notBuilt.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

function RoomBrand({ brand, busy, onApply }) {
  if (!brand) return <p className="text-sm text-muted-foreground">Loading the room's brand.</p>
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">Brand state</h3>
        <span className="flex flex-wrap items-center gap-2">
          <StateBadge state={brand.lifecycle_state} />
          <span className="text-xs text-muted-foreground">
            {brand.compliant ? 'On brand' : 'Off brand'}
            {brand.stale ? ' - behind the current set' : ''}
          </span>
          <Button variant="primary" icon="schema" disabled={busy} onClick={onApply}>
            Apply brand
          </Button>
        </span>
      </div>
      <FindingList findings={brand.findings} />
    </div>
  )
}

function RoomHistory({ history, busy, preview, onPublish, onCheckpoint, onPreview, onRestore, onBranch }) {
  if (!history) return <p className="text-sm text-muted-foreground">Loading the version history.</p>
  const versions = history.versions || []
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">Version history</h3>
        <span className="flex flex-wrap items-center gap-2">
          <Button variant="primary" icon="plus" disabled={busy} onClick={onPublish}>
            Publish a version
          </Button>
          <Button icon="plus" disabled={busy} onClick={onCheckpoint}>
            Checkpoint a session
          </Button>
        </span>
      </div>
      {versions.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No version yet. Every publish is snapshotted into an immutable version.
        </p>
      ) : (
        <ul className="flex flex-col" aria-label="Version history">
          {versions.map((version) => (
            <VersionRow
              key={version.id || version.version}
              version={version}
              head={history.head_version}
              busy={busy}
              onPreview={onPreview}
              onRestore={onRestore}
              onBranch={onBranch}
            />
          ))}
        </ul>
      )}
      {preview && (
        <div className="flex flex-col gap-2 rounded-sm border border-border-subtle bg-muted p-3">
          <p className="text-xs text-foreground">
            Version {preview.version} against the head (v{preview.head_version}).
          </p>
          {preview.changes.length === 0 ? (
            <p className="text-xs text-muted-foreground">No field differs.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {preview.changes.map((change) => (
                <li key={change.field} className="font-mono text-xs text-muted-foreground">
                  {change.field}: {JSON.stringify(change.from)} to {JSON.stringify(change.to)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function RoomOnboarding({ plan, busy, onRepurpose, onAdvance, onComplete }) {
  if (!plan) return <p className="text-sm text-muted-foreground">Loading the onboarding plan.</p>
  const canRepurpose = plan.lifecycle_state === 'sales'
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">Onboarding and handoff</h3>
        <span className="flex flex-wrap items-center gap-2">
          <StateBadge state={plan.lifecycle_state} />
          <Button variant="primary" icon="restore" disabled={busy || !canRepurpose} onClick={onRepurpose}>
            Repurpose at closed-won
          </Button>
          <Button icon="chevron" disabled={busy || canRepurpose} onClick={onAdvance}>
            Advance the phase
          </Button>
        </span>
      </div>
      <p className="text-xs text-muted-foreground">
        {plan.progress
          ? `${plan.progress.phases_complete} of ${plan.progress.phases} phase(s) complete, ${plan.progress.milestones_complete} of ${plan.progress.milestones} milestone(s) closed.`
          : 'No phases yet.'}
      </p>
      <PhaseList plan={plan} onComplete={onComplete} busy={busy} />
    </div>
  )
}

function RoomRetention({ retention, busy, onSet, onApply }) {
  const [archiveDays, setArchiveDays] = useState('180')
  const [deleteDays, setDeleteDays] = useState('365')
  const [legalBasis, setLegalBasis] = useState('')

  const archiveValue = wholeInRange(archiveDays, 1, 3650)
  const deleteValue = wholeInRange(deleteDays, 1, 3650)
  const refused = []
  if (archiveValue === null) refused.push('The archive window must be a whole number of days.')
  if (deleteValue === null) refused.push('The delete window must be a whole number of days.')

  if (!retention) return <p className="text-sm text-muted-foreground">Loading the retention policy.</p>

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-foreground">Data lifecycle</h3>
        <span className="flex flex-wrap items-center gap-2">
          <StateBadge state={retention.state} kind="retention" />
          <Button icon="restore" disabled={busy || !retention.due} onClick={onApply}>
            Apply the schedule
          </Button>
        </span>
      </div>
      <RetentionWindow retention={retention} />
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Archive after (days)" id="wf137-archive" hint="Days after the lifecycle ends.">
          <input
            id="wf137-archive"
            className={inputClass}
            value={archiveDays}
            onChange={(event) => setArchiveDays(event.target.value)}
          />
        </Field>
        <Field label="Delete after (days)" id="wf137-delete" hint="Days after the archive.">
          <input
            id="wf137-delete"
            className={inputClass}
            value={deleteDays}
            onChange={(event) => setDeleteDays(event.target.value)}
          />
        </Field>
        <Field label="Legal basis" id="wf137-basis" hint="Why the room is retained.">
          <input
            id="wf137-basis"
            className={inputClass}
            value={legalBasis}
            onChange={(event) => setLegalBasis(event.target.value)}
          />
        </Field>
      </div>
      {refused.length > 0 && <InlineNote tone="warning">{refused.join(' ')}</InlineNote>}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          icon="database"
          disabled={busy || refused.length > 0}
          onClick={() =>
            onSet({
              archive_after_days: archiveValue,
              delete_after_days: deleteValue,
              legal_basis: legalBasis.trim() || undefined,
            })
          }
        >
          Save the policy
        </Button>
        {legalBasis && <span className="text-xs text-muted-foreground">Basis: {legalBasis}</span>}
      </div>
      <p className="text-xs text-muted-foreground">
        A policy is ordinary JSON on the record. Any window inside the server's bound is accepted
        with no migration and no coordination with another team.
      </p>
    </div>
  )
}

export default {
  id: 'wf-137-governance',
  label: 'Governance',
  icon: 'schema',
  order: 1370,
  Component: GovernancePage,
}
