import { useState } from 'react'

import {
  conflictText,
  countsByState,
  recordLabel,
  roomsByDeal,
  shortTimestamp,
  syncApi,
} from './api'
import { ConditionList, CreatedBadge, Notice, ObjectKindBadge, StateBadge } from './primitives'
import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  StatCard,
  Spinner,
  useAsync,
} from '@/components/ui'

/**
 * WF-125: the seller's page for a CRM-driven room lifecycle.
 *
 * What the page is shaped around
 * ------------------------------
 *
 * A mirrored CRM record is the room's source. So the page's first job is to
 * show what is mirrored, which rule selects a record, and what a room was made
 * from. Its second job is to show the write-back: every engagement event, and
 * one row per standard and custom object it lands on.
 *
 * Three things get deliberate treatment rather than a generic list row:
 *
 *   1. **The rule is data.** The page shows each rule's condition set, because
 *      the rule is a stored row evaluated against a stored payload, not a
 *      button a person pressed.
 *   2. **Custom objects are not a footnote.** Each write-back names its target
 *      object and whether it is standard or custom, and the summary counts the
 *      custom ones beside the standard ones.
 *   3. **A write-back is shown in every state.** Pending and failed lines are
 *      listed and can be retried, because a sync that only reported successes
 *      would hide the failure the research warns about.
 *
 * Loading, error and empty states are all present, as the design system
 * requires.
 */

async function loadBoard() {
  const [summary, vocabulary, rules, records, targets, rooms, events, writebacks] =
    await Promise.all([
      syncApi.summary(),
      syncApi.vocabulary(),
      syncApi.rules(),
      syncApi.records(),
      syncApi.targets(),
      syncApi.rooms(),
      syncApi.events(),
      syncApi.writebacks(),
    ])
  return {
    summary,
    vocabulary,
    rules: rules.rules || [],
    records: records.records || [],
    targets: targets.targets || [],
    rooms: rooms.rooms || [],
    events: events.events || [],
    writebacks: writebacks.writebacks || [],
  }
}

function StatRow({ summary }) {
  if (!summary) return null
  const failed = Number(summary.failed || 0)
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Rules"
        value={summary.rules}
        hint={`${summary.rules_enabled} enabled`}
      />
      <StatCard
        label="Mirrored records"
        value={summary.records}
        hint={`${summary.custom_objects} custom object(s)`}
      />
      <StatCard label="Generated rooms" value={summary.rooms} hint="Rule and seller picks" />
      <StatCard
        label="Write-backs"
        value={summary.writebacks}
        hint={failed ? `${failed} failed, ${summary.pending} pending` : 'All synced'}
        icon={failed ? 'audit' : undefined}
      />
    </div>
  )
}

export default function RoomSync() {
  const board = useAsync(() => loadBoard(), [])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)

  async function run(action) {
    setBusy(true)
    setProblem(null)
    try {
      await action()
      board.refetch()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  if (board.loading) return <Spinner label="Loading the CRM room sync" />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const data = board.data
  const counts = countsByState(data.writebacks)
  const roomsFor = roomsByDeal(data.rooms)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">
            CRM-driven room lifecycle
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Rooms generated from mirrored CRM records, and every engagement written back to the
            deal&apos;s standard and custom objects.
          </p>
        </div>
        <Button
          icon="refresh"
          variant="primary"
          disabled={busy}
          onClick={() => run(() => syncApi.evaluate())}
        >
          Apply enabled rules
        </Button>
      </div>

      {problem && (
        <Notice tone="destructive" title="That action was refused">
          {conflictText(problem.reason) || String(problem.message || problem)}
        </Notice>
      )}

      <StatRow summary={data.summary} />

      {data.summary && data.summary.writebacks > 0 && (
        <Notice
          tone={counts.failed ? 'warning' : counts.pending ? 'info' : 'success'}
          title={
            counts.failed
              ? `${counts.failed} write-back(s) failed`
              : counts.pending
                ? `${counts.pending} write-back(s) pending`
                : 'Every write-back synced'
          }
        >
          {data.summary.synced} synced, {counts.pending} pending, {counts.failed} failed across{' '}
          {data.targets.length} target object(s), {data.targets.filter((t) => t.object_kind === 'custom').length} of them custom.
        </Notice>
      )}

      {data.records.length === 0 ? (
        <EmptyState
          title="No mirrored CRM records"
          description="Ingest an opportunity or a custom object, then apply a rule to generate a room from it."
        />
      ) : (
        <>
          <section aria-labelledby="rules-heading" className="space-y-3">
            <h2 id="rules-heading" className="font-display text-lg font-semibold text-foreground">
              Room-creation rules
            </h2>
            {data.rules.length === 0 ? (
              <EmptyState title="No rules yet" description="A rule creates rooms when a CRM condition is met." />
            ) : (
              <ul className="space-y-3">
                {data.rules.map((rule) => (
                  <li key={rule.id}>
                    <Card>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-base font-semibold text-foreground">{rule.name}</p>
                          <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                            {rule.crm} / {rule.object}
                          </p>
                          <div className="mt-2">
                            <ConditionList conditions={rule.conditions} />
                          </div>
                        </div>
                        <span
                          className={`inline-flex items-center rounded-xs border px-2 py-0.5 font-mono text-xs font-medium ${
                            rule.enabled
                              ? 'border-success/30 bg-success/10 text-success'
                              : 'border-border-subtle bg-muted text-muted-foreground'
                          }`}
                        >
                          {rule.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                      </div>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="records-heading" className="space-y-3">
            <h2 id="records-heading" className="font-display text-lg font-semibold text-foreground">
              Mirrored CRM records
            </h2>
            <ul className="space-y-3">
              {data.records.map((record) => (
                <li key={record.id}>
                  <Card>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-foreground">{recordLabel(record)}</p>
                        <p className="mt-0.5 font-mono text-xs text-muted-foreground">
                          {record.crm} / {record.object} / {record.external_id}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <ObjectKindBadge kind={record.object_kind} />
                        {roomsFor[record.id] ? (
                          <CreatedBadge from={roomsFor[record.id].created_from} />
                        ) : (
                          <span className="text-xs text-muted-foreground">No room yet</span>
                        )}
                      </div>
                    </div>
                  </Card>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="writebacks-heading" className="space-y-3">
            <h2
              id="writebacks-heading"
              className="font-display text-lg font-semibold text-foreground"
            >
              Engagement write-back
            </h2>
            {data.writebacks.length === 0 ? (
              <EmptyState
                title="No engagement yet"
                description="Record a view, download or share and it is written back to every target object."
              />
            ) : (
              <ul className="space-y-3">
                {data.writebacks.map((row) => (
                  <li key={row.id}>
                    <Card>
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-mono text-sm text-foreground">
                            {row.object} / {row.external_id}
                          </p>
                          <p className="mt-0.5 text-xs text-muted-foreground">
                            {Object.entries(row.fields || {})
                              .map(([field, value]) => `${field}=${value}`)
                              .join(' ') || 'No fields projected'}
                          </p>
                          {row.reason && (
                            <p className="mt-0.5 text-xs text-destructive">{conflictText(row.reason)}</p>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <ObjectKindBadge kind={row.object_kind} />
                          <StateBadge state={row.state} />
                          {row.state !== 'synced' && (
                            <Button
                              icon="restore"
                              disabled={busy}
                              onClick={() => run(() => syncApi.retryWriteback(row.id))}
                            >
                              Retry
                            </Button>
                          )}
                        </div>
                      </div>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="events-heading" className="space-y-3">
            <h2 id="events-heading" className="font-display text-lg font-semibold text-foreground">
              Engagement events
            </h2>
            <ul className="space-y-2">
              {data.events.map((event) => (
                <li key={event.id}>
                  <Card>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <p className="text-sm text-foreground">
                        <span className="font-mono">{event.kind}</span> on{' '}
                        <span className="font-mono">{event.external_id}</span>
                      </p>
                      <p className="font-mono text-xs text-muted-foreground">
                        {shortTimestamp(event.at)}
                      </p>
                    </div>
                  </Card>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      {data.vocabulary?.not_implemented?.length > 0 && (
        <section aria-labelledby="not-built-heading" className="space-y-2">
          <h2
            id="not-built-heading"
            className="font-display text-lg font-semibold text-foreground"
          >
            Not built here
          </h2>
          <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
            {data.vocabulary.not_implemented.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
