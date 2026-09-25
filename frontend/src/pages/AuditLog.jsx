/**
 * Audit log: the UI wrapper over SQLite's audit trail.
 *
 * Every mutation anywhere in the system lands in `audit_log` in the same
 * transaction as the change, so this view is the authoritative history rather
 * than a debug aid. It shows the before/after state and the field-level diff
 * for each entry.
 */

import { useMemo, useState } from 'react'
import { api, absoluteTime, relativeTime } from '../lib/api'
import { Badge, Button, Card, EmptyState, ErrorNote, JsonView, Spinner, inputClass, useAsync } from '../components/ui'

const ACTIONS = ['', 'insert', 'update', 'delete', 'restore']

function DiffTable({ diff }) {
  const keys = Object.keys(diff || {})
  if (keys.length === 0) {
    return <p className="text-sm text-muted-foreground">No field-level changes recorded.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-left text-sm">
        <thead>
          <tr className="border-b border-border-subtle/30 text-xs tracking-wide text-muted-foreground uppercase">
            <th scope="col" className="py-2 pr-4 font-medium">Field</th>
            <th scope="col" className="py-2 pr-4 font-medium">Before</th>
            <th scope="col" className="py-2 font-medium">After</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key} className="border-b border-border-subtle/15 last:border-0">
              <td className="py-2 pr-4 font-mono text-[13px] text-foreground">{key}</td>
              <td className="py-2 pr-4 font-mono text-[13px] text-destructive/90">
                {format(diff[key].from)}
              </td>
              <td className="py-2 font-mono text-[13px] text-accent">{format(diff[key].to)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function format(value) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function EntryRow({ entry }) {
  const [open, setOpen] = useState(false)
  const hasDetail = Boolean(entry.diff && Object.keys(entry.diff).length > 0) || entry.before_state || entry.after_state

  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="grid w-full grid-cols-12 items-center gap-3 px-1 py-3 text-left transition-colors duration-150 hover:bg-muted/40"
      >
        <span className="col-span-2 sm:col-span-1 font-mono text-xs text-muted-foreground">#{entry.seq}</span>
        <span className="col-span-3 sm:col-span-2">
          <Badge tone={entry.action}>{entry.action}</Badge>
        </span>
        <span className="col-span-4 truncate font-mono text-[13px] text-foreground">
          {entry.collection || '—'}
        </span>
        <span className="col-span-3 hidden truncate font-mono text-xs text-muted-foreground sm:block">
          {entry.summary || '—'}
        </span>
        <span
          className="col-span-3 text-right text-xs text-muted-foreground sm:col-span-2"
          title={absoluteTime(entry.ts)}
        >
          {relativeTime(entry.ts)}
        </span>
        <span className="col-span-1 text-right text-muted-foreground">
          <span
            className={`inline-block transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
            aria-hidden="true"
          >
            ›
          </span>
        </span>
      </button>

      {open && hasDetail && (
        <div className="space-y-4 rounded-lg border border-border-subtle/25 bg-background/40 p-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">Actor</dt>
              <dd className="font-mono text-foreground">{entry.actor || '—'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Timestamp</dt>
              <dd className="font-mono text-foreground">{absoluteTime(entry.ts)}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-muted-foreground">Source</dt>
              <dd className="truncate font-mono text-foreground">{entry.source || '—'}</dd>
            </div>
          </dl>

          {entry.diff && Object.keys(entry.diff).length > 0 && <DiffTable diff={entry.diff} />}

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Before
              </p>
              <JsonView value={entry.before_state} />
            </div>
            <div>
              <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                After
              </p>
              <JsonView value={entry.after_state} />
            </div>
          </div>
        </div>
      )}
    </li>
  )
}

export default function AuditLog() {
  const [collection, setCollection] = useState('')
  const [action, setAction] = useState('')
  const [actor, setActor] = useState('')

  const params = useMemo(
    () => ({ collection, action, actor, limit: 200 }),
    [collection, action, actor],
  )
  const { data, error, loading, refetch } = useAsync(() => api.audit(params), [params])

  const entries = data?.entries || []
  const active = collection || action || actor

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-2xl font-semibold">Audit log</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every SQLite change, recorded in the same transaction as the change itself.
          </p>
        </div>
        <Button icon="refresh" onClick={refetch} disabled={loading}>
          Refresh
        </Button>
      </header>

      <Card>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="audit-collection" className="text-xs font-medium text-muted-foreground">
              Collection
            </label>
            <input
              id="audit-collection"
              className={inputClass}
              placeholder="e.g. room"
              value={collection}
              onChange={(event) => setCollection(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="audit-action" className="text-xs font-medium text-muted-foreground">
              Action
            </label>
            <select
              id="audit-action"
              className={inputClass}
              value={action}
              onChange={(event) => setAction(event.target.value)}
            >
              {ACTIONS.map((value) => (
                <option key={value || 'all'} value={value}>
                  {value || 'All actions'}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="audit-actor" className="text-xs font-medium text-muted-foreground">
              Actor
            </label>
            <input
              id="audit-actor"
              className={inputClass}
              placeholder="e.g. api"
              value={actor}
              onChange={(event) => setActor(event.target.value)}
            />
          </div>
        </div>
        {active && (
          <p className="mt-3 text-xs text-muted-foreground">
            Showing {entries.length} entries matching the current filters.{' '}
            <button
              type="button"
              className="text-accent underline underline-offset-2"
              onClick={() => {
                setCollection('')
                setAction('')
                setActor('')
              }}
            >
              Clear filters
            </button>
          </p>
        )}
      </Card>

      {loading && <Spinner label="Loading audit trail" />}
      {error && <ErrorNote error={error} onRetry={refetch} />}

      {!loading && !error && (
        <Card>
          {entries.length === 0 ? (
            <EmptyState
              title="No audit entries yet"
              description="Mutations appear here the moment anything is written through the audited store."
            />
          ) : (
            <>
              <p className="mb-2 text-xs tracking-wide text-muted-foreground uppercase">
                {entries.length} entries, newest first
              </p>
              {/* Column header mirrors the grid used by each row. */}
              <div className="grid grid-cols-12 gap-3 border-b border-border-subtle/40 px-1 pb-2 text-xs tracking-wide text-muted-foreground uppercase">
                <span className="col-span-2 sm:col-span-1">Seq</span>
                <span className="col-span-3 sm:col-span-2">Action</span>
                <span className="col-span-4">Collection</span>
                <span className="col-span-3 hidden sm:block">Summary</span>
                <span className="col-span-3 text-right sm:col-span-2">When</span>
                <span className="col-span-1" />
              </div>
              <ul>
                {entries.map((entry) => (
                  <EntryRow key={entry.seq} entry={entry} />
                ))}
              </ul>
            </>
          )}
        </Card>
      )}
    </div>
  )
}
