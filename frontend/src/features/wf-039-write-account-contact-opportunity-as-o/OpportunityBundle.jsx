/**
 * Opportunity bundle: write account + contact + opportunity as one atomic
 * transaction (WF-039).
 *
 * The page follows the researched flow rather than a feature tour, because the
 * flow is what a rep does and the order matters:
 *
 *   1. **Sync → Create opportunity bundle** for a buyer whose CRM account may not
 *      exist yet. So the page starts from a room and a declared bundle, and the
 *      bundle is a list of records each naming the one before it it depends on.
 *   2. "The connector builds a single request containing Account → Contact →
 *      Opportunity subrequests **in dependency order**." So the order is the
 *      headline: the preview's first job is to show which subrequest runs first,
 *      numbered, with what each one depends on.
 *   3. "The connector sets the rollback policy (strict / partial) for that
 *      request." Two researched settings, a policy and the `collateSubrequests`
 *      ordering flag, both overridable per request - so both are offered as
 *      controls on the preview, and the *exact request* re-renders as they move.
 *   4. "The CRM executes subrequests in order, capturing each created record id."
 *      That is the run log, and the records the CRM holds.
 *   5. "Later subrequests reference earlier ones by id, so the Opportunity is
 *      created against the *just-created* Account." So a committed step expands to
 *      show the id the parent link actually resolved to, which is the difference
 *      between a fresh account and a stale one.
 *   6. "On any failure the whole bundle rolls back (strict mode) and the room
 *      shows a single actionable error." So the run shows **one** error, and every
 *      per-subrequest outcome beneath it.
 *
 * Two things this page is careful to show rather than hide:
 *
 *   - **A rollback is not the same as nothing happening.** A strict run's rows
 *     are gone from the CRM and the run still records their ids, so "the Account
 *     was created and then the whole bundle was rolled back" is a thing a rep can
 *     read rather than something they are told.
 *   - **HubSpot is not atomic.** The research cites a contacts batch create and
 *     an association `PUT` and documents no transaction that spans them, so a
 *     strict run on that dialect is a *compensation*. The preview says so, the
 *     run carries `compensated`, and a refused compensating delete is reported
 *     instead of being swallowed.
 */

import { useCallback, useMemo, useState } from 'react'
import { relativeTime } from '@/lib/api'
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
import { bundleApi, listRooms } from './api'
import Glyph, { ATOMIC_ICON, BUNDLE_ICON, ORDER_ICON, ROLLBACK_ICON } from './icons'
import { Notice, StatTile, StepRow, Toggle } from './primitives'

/** A short label for a dialect id, so the picker reads as a choice not a code. */
const DIALECT_LABEL = {
  salesforce_composite: 'Salesforce Composite',
  salesforce_sobject_tree: 'Salesforce sObject Tree',
  dataverse_batch: 'Dataverse changeset',
  hubspot_associations: 'HubSpot associations',
}

const POLICY_LABEL = {
  strict: 'Strict — roll the whole bundle back',
  partial: 'Partial — keep whatever is independent',
}

export default function OpportunityBundle() {
  const [roomId, setRoomId] = useState('')
  const [bundleId, setBundleId] = useState('')
  const [policy, setPolicy] = useState('')
  const [collate, setCollate] = useState(null)
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState(null)
  const [showInferences, setShowInferences] = useState(false)

  const rooms = useAsync(() => listRooms({ limit: 100 }), [])
  const vocabulary = useAsync(() => bundleApi.vocabulary(), [])
  const inferences = useAsync(() => bundleApi.inferences(), [])

  const bundles = useAsync(
    () => (roomId ? bundleApi.listBundles(roomId) : Promise.resolve(null)),
    [roomId]
  )
  const runs = useAsync(() => (roomId ? bundleApi.listRuns(roomId) : Promise.resolve(null)), [roomId])
  const targets = useAsync(
    () => (roomId ? bundleApi.listTargets(roomId) : Promise.resolve(null)),
    [roomId]
  )

  // The settings actually in force for the next commit: the request's own choice
  // if made, otherwise whatever the preview says is effective. Both live in one
  // place so the control and the request cannot disagree.
  const effective = bundles.data?.bundles?.find((entry) => entry.id === bundleId)?.effective

  const overrides = useMemo(() => {
    const payload = {}
    if (policy) payload.policy = policy
    if (collate !== null) payload.collate_subrequests = collate
    return payload
  }, [policy, collate])

  const preview = useAsync(
    () => (roomId && bundleId ? bundleApi.preview(roomId, bundleId, overrides) : Promise.resolve(null)),
    [roomId, bundleId, policy, collate]
  )

  const reloadRuns = useCallback(() => {
    runs.refetch()
    targets.refetch()
  }, [runs, targets])

  async function commit() {
    setBusy(true)
    setFailure(null)
    setRun(null)
    try {
      const result = await bundleApi.commit(roomId, bundleId, overrides)
      setRun(result)
      reloadRuns()
    } catch (error) {
      setFailure(error)
    } finally {
      setBusy(false)
    }
  }

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading the opportunity bundle" />
  }
  if (rooms.error) {
    return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />
  }
  if (vocabulary.error) {
    return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  }

  const roomOptions = rooms.data?.records || []
  const bundleOptions = bundles.data?.bundles || []
  const selected = bundleOptions.find((entry) => entry.id === bundleId)

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground">
          <Glyph name="bundle" size={20} />
          Sync → Create opportunity bundle
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Account, contact and opportunity committed as <em>one</em> request. The bundle below is the
          dependency graph; this page shows the subrequest order before anything is sent, and the
          rollback policy and the ordering flag as the two researched settings they are.
        </p>
      </header>

      {/* -- pick a room and a bundle ------------------------------------- */}
      <Card className="grid gap-4 md:grid-cols-2">
        <Field label="Room" id="wf039-room" hint="The bundle belongs to one room.">
          <select
            id="wf039-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setRoomId(event.target.value)
              setBundleId('')
              setRun(null)
              setPolicy('')
              setCollate(null)
            }}
          >
            <option value="">Choose a room…</option>
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Bundle"
          id="wf039-bundle"
          hint={
            bundleOptions.length
              ? 'The declared subrequests, in dependency order.'
              : 'No bundles declared for this room yet.'
          }
        >
          <select
            id="wf039-bundle"
            className={inputClass}
            value={bundleId}
            disabled={!roomId || !bundleOptions.length}
            onChange={(event) => {
              setBundleId(event.target.value)
              setRun(null)
              setPolicy('')
              setCollate(null)
            }}
          >
            <option value="">Choose a bundle…</option>
            {bundleOptions.map((bundle) => (
              <option key={bundle.id} value={bundle.id}>
                {bundle.name} — {bundle.record_count} subrequest
                {bundle.record_count === 1 ? '' : 's'}
              </option>
            ))}
          </select>
        </Field>
      </Card>

      {!roomId && (
        <EmptyState
          title="Choose a room to begin"
          description="The researched flow starts from a buyer whose CRM account may not exist yet, so everything here is scoped to one room."
        />
      )}

      {roomId && !bundleId && (
        <EmptyState
          title="This room has no bundles declared"
          description="A bundle is an ordered list of records, each naming the one before it it depends on. Declare one to see the request it renders."
        />
      )}

      {/* -- the two researched settings, and the blockers ---------------- */}
      {selected && (
        <Card className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-foreground">The request</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {DIALECT_LABEL[preview.data?.plan?.dialect || effective?.dialect] ||
                  effective?.dialect}
                {' · '}
                {bundleOptions.find((entry) => entry.id === bundleId)?.types?.join(' → ')}
              </p>
            </div>
            <Badge tone="neutral">
              {preview.data?.plan?.subrequests ?? selected.record_count} subrequests, one request
            </Badge>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Field
              label="Rollback policy (allOrNone)"
              id="wf039-policy"
              hint={
                (vocabulary.data?.policies || []).find((entry) => entry.id === (policy || effective?.policy))
                  ?.meaning
              }
            >
              <select
                id="wf039-policy"
                className={inputClass}
                value={policy || effective?.policy || ''}
                onChange={(event) => setPolicy(event.target.value)}
              >
                {(vocabulary.data?.policies || []).map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {POLICY_LABEL[entry.id] || entry.id}
                  </option>
                ))}
              </select>
            </Field>

            <Field
              label="collateSubrequests"
              id="wf039-collate"
              hint="The researched trade: group subrequests of the same type for speed, or force the declared order."
            >
              <div className="flex min-h-11 items-center gap-3">
                <Toggle
                  checked={
                    collate === null
                      ? effective?.collate_subrequests !== false
                      : collate
                  }
                  label="collateSubrequests"
                  onChange={(next) => setCollate(next)}
                />
                <span className="text-xs text-muted-foreground">
                  {((collate === null ? effective?.collate_subrequests !== false : collate)
                    ? 'Grouping on — an implicit dependency may not be ordered.'
                    : 'Grouping off — subrequests run in the order below.'
                  ) || ''}
                </span>
              </div>
            </Field>
          </div>

          {(preview.data?.blockers || []).map((blocker) => (
            <Notice key={blocker.code} tone="warn" title={blocker.code.replace(/_/g, ' ')}>
              {blocker.message}
            </Notice>
          ))}
          {(preview.data?.warnings || []).map((warning) => (
            <Notice
              key={warning.code}
              tone="warn"
              title={warning.code.replace(/_/g, ' ')}
              action={
                warning.code === 'implicit_dependency_unordered' ? (
                  <Button onClick={() => setCollate(false)}>Set collateSubrequests to false</Button>
                ) : null
              }
            >
              {warning.message}
              {warning.steps ? ` (affects ${warning.steps})` : ''}
            </Notice>
          ))}
        </Card>
      )}

      {/* -- the researched bundle preview: the subrequest order ------------ */}
      {selected && preview.loading && <Spinner label="Building the request" />}

      {selected && preview.error && (
        <ErrorNote error={preview.error} onRetry={preview.refetch} />
      )}

      {selected && preview.data && (
        <>
          <Card className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Glyph name="order" size={16} />
                Subrequest order
              </h2>
              <span className="font-mono text-xs text-muted-foreground">
                {preview.data.request.method} {preview.data.request.path}
                {preview.data.request.is_sequence
                  ? ` · ${preview.data.request.request_count} requests, not one transaction`
                  : ''}
              </span>
            </div>
            <ol className="divide-y divide-border-subtle/15">
              {(preview.data.plan.steps || []).map((step, index) => (
                <StepRow
                  key={step.reference_id}
                  step={{ ...step, depends_on: [...step.explicit_dependencies, ...step.implicit_dependencies] }}
                  index={index}
                />
              ))}
            </ol>
            <p className="text-xs text-muted-foreground">
              {preview.data.request.is_sequence
                ? preview.data.request.atomicity.basis
                : `Atomicity: ${preview.data.request.atomicity.quote}`}
            </p>
          </Card>

          <Card className="space-y-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <Glyph name="wire" size={16} />
              Exactly what would go on the wire
            </h2>
            {preview.data.request.notes?.length > 0 && (
              <ul className="space-y-1">
                {preview.data.request.notes.map((note) => (
                  <li key={note} className="text-xs text-muted-foreground">
                    {note}
                  </li>
                ))}
              </ul>
            )}
            <pre className="max-h-80 overflow-auto rounded-lg border border-border-subtle/25 bg-background/40 p-3 font-mono text-[12px] text-foreground">
              {preview.data.request.raw_body || '(a sequence of requests — see the parts above)'}
            </pre>
          </Card>

          <Card className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-foreground">Commit</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                One explicit request. {preview.data.ready ? '' : 'Blocked — see above.'}
              </p>
            </div>
            <Button
              variant="primary"
              onClick={commit}
              disabled={busy || !preview.data.ready}
              icon={busy ? undefined : 'refresh'}
            >
              {busy ? 'Committing…' : 'Commit the bundle'}
            </Button>
          </Card>
        </>
      )}

      {failure && (
        <Notice tone="bad" title="The commit was refused">
          {String(failure.message || failure)}
        </Notice>
      )}

      {/* -- the run: one error, every outcome ----------------------------- */}
      {run && (
        <RunPanel
          run={run}
          targets={targets.data}
          onRetry={reloadRuns}
        />
      )}

      {/* -- the run log and what the CRM holds --------------------------- */}
      {roomId && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card className="space-y-2">
            <h2 className="text-sm font-semibold text-foreground">Runs</h2>
            {runs.loading && <Spinner label="Loading runs" />}
            {runs.error && <ErrorNote error={runs.error} onRetry={runs.refetch} />}
            {!runs.loading && (runs.data?.runs || []).length === 0 && (
              <p className="text-sm text-muted-foreground">No commits yet for this room.</p>
            )}
            <ul className="divide-y divide-border-subtle/15">
              {(runs.data?.runs || []).map((entry) => (
                <li key={entry.id} className="py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={entry.ok ? 'insert' : 'delete'}>
                      {entry.ok ? 'committed' : 'failed'}
                    </Badge>
                    <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                      {entry.bundle_name || entry.bundle_id}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {relativeTime(entry.created_at)}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">
                    {entry.dialect} · {entry.policy} ·{' '}
                    {Object.entries(entry.counts || {})
                      .filter(([, n]) => n)
                      .map(([key, n]) => `${n} ${key}`)
                      .join(', ') || 'nothing committed'}
                  </p>
                  {entry.actionable_error && (
                    <p className="mt-0.5 truncate text-xs text-destructive">
                      {entry.actionable_error.message}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="space-y-2">
            <h2 className="text-sm font-semibold text-foreground">
              Records the CRM holds for this room
            </h2>
            {targets.loading && <Spinner label="Loading CRM records" />}
            {targets.error && <ErrorNote error={targets.error} onRetry={targets.refetch} />}
            {!targets.loading && (targets.data?.targets || []).length === 0 && (
              <p className="text-sm text-muted-foreground">
                Nothing. A strict rollback leaves the CRM exactly as it found it.
              </p>
            )}
            <ul className="divide-y divide-border-subtle/15">
              {(targets.data?.targets || []).map((row) => (
                <li key={row.id} className="py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="neutral">{row.data?.object}</Badge>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
                      {row.data?.reference_id}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {relativeTime(row.created_at)}
                    </span>
                  </div>
                  {row.data?.linked_to && (
                    <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                      linked to {row.data.linked_to}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      {/* -- the sourced contract, and the judgements behind it ------------ */}
      <Card className="space-y-2">
        <button
          type="button"
          onClick={() => setShowInferences((value) => !value)}
          aria-expanded={showInferences}
          className="flex min-h-11 w-full items-center gap-2 text-left text-sm font-semibold text-foreground"
        >
          <Glyph name="inference" size={16} />
          What the research fixes, and what this build decided
        </button>
        {showInferences && inferences.loading && <Spinner label="Loading the inferences" />}
        {showInferences && inferences.data && (
          <ul className="space-y-2">
            {(inferences.data.inferences || []).map((entry) => (
              <li key={entry.id} className="rounded-lg border border-border-subtle/25 p-3">
                <p className="text-sm font-medium text-foreground">{entry.topic}</p>
                <p className="mt-1 text-xs text-muted-foreground">{entry.basis}</p>
                <p className="mt-1 text-xs text-muted-foreground">{entry.why}</p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  change it: {entry.change_it}
                </p>
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-muted-foreground">
                    What this build chose
                  </summary>
                  <JsonView value={entry.value} />
                </details>
              </li>
            ))}
            <li className="rounded-lg border border-border-subtle/25 p-3">
              <p className="text-sm font-medium text-foreground">The research's own gaps</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                {(inferences.data.sourced_gaps || []).map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </li>
          </ul>
        )}
      </Card>
    </div>
  )
}

/** One commit, in full: the single error, then every per-subrequest outcome. */
function RunPanel({ run, targets, onRetry }) {
  const outcomes = run.steps || []
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground">
          The run — {run.ok ? 'committed' : 'did not commit'}
        </h2>
        <span className="font-mono text-xs text-muted-foreground">
          {run.policy} · {run.dialect} · {run.atomic ? 'atomic' : 'a sequence, not a transaction'}
        </span>
      </div>

      {run.actionable_error ? (
        <Notice tone="bad" title="Actionable error">
          <p className="font-medium text-foreground">
            {run.actionable_error.reference_id} ({run.actionable_error.record_type})
          </p>
          <p className="mt-0.5">{run.actionable_error.message}</p>
          <p className="mt-1 text-xs">{run.actionable_error.detail}</p>
        </Notice>
      ) : (
        <Notice tone="good" title="Committed">
          Every subrequest in this bundle committed. Nothing to fix.
        </Notice>
      )}

      {run.compensated && (
        <Notice tone="warn" title="Compensated, not rolled back">
          This dialect has no documented transaction, so the strict policy was honoured by deleting
          what it created.{(run.compensation_failures || []).length > 0
            ? ` A delete was refused for ${run.compensation_failures.join(', ')}, so those rows are still there.`
            : ''}
        </Notice>
      )}

      <div className="grid gap-3 sm:grid-cols-4">
        <StatTile label="Committed" value={run.counts?.created ?? 0} glyph={ATOMIC_ICON} />
        <StatTile
          label="Rolled back"
          value={run.counts?.rolled_back ?? 0}
          hint="rows the CRM created, then undid"
          glyph={ROLLBACK_ICON}
        />
        <StatTile
          label="Skipped"
          value={run.counts?.skipped ?? 0}
          hint="their dependency did not succeed"
          glyph={BUNDLE_ICON}
        />
        <StatTile label="Failed" value={run.counts?.failed ?? 0} glyph={ORDER_ICON} />
      </div>

      <ol className="divide-y divide-border-subtle/15">
        {outcomes.map((step, index) => (
          <StepRow
            key={step.reference_id}
            step={{ ...step, depends_on: step.depends_on }}
            index={index}
            outcome={step.outcome}
            reason={step.reason}
            message={step.message}
            resolved={step.resolved}
          />
        ))}
      </ol>

      {targets && (
        <p className="text-xs text-muted-foreground">
          The CRM now holds {targets.count} record{targets.count === 1 ? '' : 's'} for this room.
          {run.counts?.rolled_back > 0 &&
            ' The rolled-back rows are gone from that list and recorded on this run.'}
        </p>
      )}

      <details>
        <summary className="cursor-pointer text-sm text-muted-foreground">
          The request that was sent
        </summary>
        <JsonView value={run.request || null} />
      </details>

      <Button onClick={onRetry}>Refresh runs and CRM records</Button>
    </Card>
  )
}
