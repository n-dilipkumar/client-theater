/**
 * Pipeline triage: the Workspaces dashboard as a set of saved views (WF-022).
 *
 * The page follows the researched flow rather than a feature tour, because the
 * flow is what a rep does and the order matters:
 *
 *   1. Open the Workspaces dashboard. Step one here is the remembered open set -
 *      "The views you had open are unique to your user account. We'll remember
 *      which views you had open the next time you open the Workspaces
 *      dashboard." The account selector in the header is what makes that
 *      per-account state visible, and it is a plain text input because the
 *      research names no user directory to pick from.
 *   2. Add view, starting from a default. The five defaults are listed with the
 *      research's own definition where it has one, and marked where it does not,
 *      so a guess is never presented as a quote.
 *   3. Clone, then filter and sort. Clone is one click and lands you on a private
 *      copy, because "clone existing views to make your own customized copy" and a
 *      custom copy is one of the "private views for yourself".
 *   4. Edit and rearrange columns. The editor is a list you reorder, because the
 *      column list *is* the table's shape and position is meaningful.
 *   5. Save private or public.
 *
 * Two things this page is careful to show rather than hide:
 *
 *   - **`problems` from the server.** A filter the engine could not read is
 *     dropped and reported, and the row count is then wider than the view
 *     describes. Saying so is the whole point; a triage table that quietly
 *     dropped a condition would be showing a rep a pipeline that does not exist.
 *   - **Where a column's value came from.** A Salesforce column that is null
 *     because the workspace is linked to HubSpot looks identical to a Salesforce
 *     column that is null because nothing has synced, and the row's `meta` says
 *     which. The provider glyph marks the difference.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
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
import { triageApi } from './api'
import Glyph, { CLONE_ICON, INFERENCE_ICON, REORDER_ICON, SHARED_ICON, TABLE_ICON } from './icons'
import { Note, StatTile, Toggle } from './primitives'

const FOCUS =
  'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/** How a cell renders, per the catalog's declared type. */
function Cell({ column, value }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-muted-foreground/60">&mdash;</span>
  }
  if (column?.type === 'date') {
    return (
      <span title={absoluteTime(value)} className="font-mono text-[13px]">
        {relativeTime(value)}
      </span>
    )
  }
  if (column?.type === 'number') {
    return <span className="font-mono text-[13px] text-accent">{value.toLocaleString()}</span>
  }
  return <span className="text-[13px]">{value}</span>
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
        className={`flex w-full min-h-11 items-center gap-3 py-2 text-left transition-colors
          duration-150 hover:bg-muted/40 ${FOCUS}`}
      >
        <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
          {entry.topic}
        </span>
        {entry.topic_note && <Badge tone="insert">{entry.topic_note}</Badge>}
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
            <p className="font-medium tracking-wide text-muted-foreground uppercase">How to change it</p>
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

/** The column editor. Position is the point: the list *is* the table's shape. */
function ColumnEditor({ vocabulary, columns, onChange, onSave, onClose, saving }) {
  const [draft, setDraft] = useState(columns)
  useEffect(() => setDraft(columns), [columns])

  const meta = useMemo(
    () => Object.fromEntries(vocabulary.columns.map((column) => [column.key, column])),
    [vocabulary.columns],
  )
  const available = vocabulary.columns.filter((column) => !draft.includes(column.key))

  const move = (index, delta) => {
    const next = [...draft]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setDraft(next)
  }

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Glyph name="reorder" size={16} />
          Edit and rearrange columns
        </h3>
        <div className="flex gap-2">
          <Button onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            onClick={() => onSave(draft)}
            disabled={saving || draft.join() === columns.join()}
          >
            {saving ? 'Saving' : 'Save columns'}
          </Button>
        </div>
      </div>

      <ol className="divide-y divide-border-subtle/15">
        {draft.map((key, index) => (
          <li key={key} className="flex items-center gap-2 py-1">
            <span className="w-5 shrink-0 font-mono text-xs text-muted-foreground">{index + 1}</span>
            <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">
              {meta[key]?.label ?? key}
              {meta[key] ? null : (
                <Badge tone="insert" className="ml-2">
                  not a published field
                </Badge>
              )}
            </span>
            <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
              {meta[key]?.group ?? key.split('.')[0]}
            </span>
            <Button
              className="px-3"
              disabled={index === 0}
              onClick={() => move(index, -1)}
              aria-label={`Move ${meta[key]?.label ?? key} earlier`}
            >
              Up
            </Button>
            <Button
              className="px-3"
              disabled={index === draft.length - 1}
              onClick={() => move(index, 1)}
              aria-label={`Move ${meta[key]?.label ?? key} later`}
            >
              Down
            </Button>
            <Button
              variant="danger"
              className="px-3"
              onClick={() => setDraft(draft.filter((entry) => entry !== key))}
              aria-label={`Remove ${meta[key]?.label ?? key}`}
            >
              Remove
            </Button>
          </li>
        ))}
      </ol>

      {available.length > 0 && (
        <Field
          label="Add a column"
          hint="Fields this build publishes. A column it does not know can still be named - the cell stays empty until the field starts syncing."
        >
          <ul className="flex flex-wrap gap-2">
            {available.map((column) => (
              <li key={column.key}>
                <Button className="px-3" onClick={() => setDraft([...draft, column.key])}>
                  Add {column.label}
                </Button>
              </li>
            ))}
          </ul>
        </Field>
      )}
    </Card>
  )
}

/** The triage table itself. `column_meta` carries each column's label and type. */
function TriageTable({ table, onSort }) {
  const meta = Object.fromEntries(table.column_meta.map((column) => [column.key, column]))

  if (table.rows.length === 0) {
    return (
      <EmptyState
        title="No workspaces match this view"
        description="Either nothing has been typed into these fields yet, or the view's filters exclude everything. The view's own filters are shown above, so it is the second."
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-left">
        <thead>
          <tr className="border-b border-border-subtle/40">
            {table.columns.map((key) => {
              const column = meta[key]
              const sorted = table.sort?.field === key
              return (
                <th
                  key={key}
                  scope="col"
                  aria-sort={sorted ? (table.sort.direction === 'desc' ? 'descending' : 'ascending') : 'none'}
                  className="px-3 py-2"
                >
                  <button
                    type="button"
                    onClick={() => onSort(key)}
                    className={`flex min-h-11 items-center gap-1.5 rounded px-1 text-xs font-medium
                      tracking-wide text-muted-foreground uppercase hover:text-foreground ${FOCUS}`}
                  >
                    <span>{column?.label ?? key}</span>
                    {sorted && (
                      <span aria-hidden="true" className="text-accent">
                        {table.sort.direction === 'desc' ? 'v' : '^'}
                      </span>
                    )}
                    <span className="sr-only">
                      {sorted ? ', sorted' : ', not sorted. Activate to sort by this column.'}
                    </span>
                  </button>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row.id} className="border-b border-border-subtle/15 last:border-0">
              {table.columns.map((key) => {
                const isName = key === 'dock.name'
                const gated =
                  row.meta?.crm_provider &&
                  key.startsWith(`${key.split('.')[0]}.`) &&
                  key.split('.')[0] !== row.meta.crm_provider
                return (
                  <td
                    key={key}
                    className={`px-3 py-2 align-top ${isName ? 'font-medium text-foreground' : ''}`}
                  >
                    <span className="flex items-center gap-1.5">
                      <Cell column={meta[key]} value={row[key]} />
                      {gated && (
                        <span title="This workspace is not connected to that CRM" className="text-muted-foreground/60">
                          <Glyph name="unplugged" size={13} />
                          <span className="sr-only">not connected to that CRM</span>
                        </span>
                      )}
                    </span>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function PipelineTriage() {
  // The remembered open set is per user account, so the page is per account too.
  const [actor, setActor] = useState('dana')
  const [activeId, setActiveId] = useState(null)
  const [note, setNote] = useState(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)

  const vocab = useAsync(() => triageApi.vocabulary(), [])
  const board = useAsync(() => triageApi.dashboard(actor), [actor])
  const active = activeId || board.data?.active_view_id || null
  const table = useAsync(
    () => (active ? triageApi.rows(active, { actor }) : Promise.resolve(null)),
    [active, actor],
  )
  const inferences = useAsync(() => triageApi.inferences(), [])

  const remember = useCallback(
    async (viewIds, activeViewId) => {
      try {
        await triageApi.rememberOpenViews({
          actor,
          view_ids: viewIds.filter(Boolean),
          active_view_id: activeViewId ?? null,
        })
        setNote({ tone: 'good', text: 'Saved. These views open next time you come here.' })
        board.refetch()
      } catch (error) {
        setNote({ tone: 'warn', text: `Could not remember the open views: ${error.message}` })
      }
    },
    [actor, board],
  )

  if (vocab.loading || board.loading) {
    return <Spinner label="Loading the Workspaces dashboard" />
  }
  if (vocab.error) return <ErrorNote error={vocab.error} onRetry={vocab.refetch} />
  if (board.error) return <ErrorNote error={board.error} onRetry={board.refetch} />

  const views = board.data?.views ?? []
  const openIds = board.data?.open_views ?? []
  const yourViews = board.data?.your_views ?? []
  const defaults = board.data?.default_views ?? []
  const problems = table.data?.problems ?? []

  const run = async (work) => {
    setBusy(true)
    setNote(null)
    try {
      await work()
    } catch (error) {
      setNote({ tone: 'warn', text: error.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground">
            <Glyph name="table" size={20} />
            Pipeline triage
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Saved views over the joined pipeline: workspace metadata, engagement, and whatever
            the CRM and the order forms have told us.
          </p>
        </div>
        <div className="w-56">
          <Field
            label="Signed in as"
            id="wf-022-actor"
            hint="The remembered open set is per account, and a private view is yours alone."
          >
            <input
              id="wf-022-actor"
              className={inputClass}
              value={actor}
              onChange={(event) => {
                setActor(event.target.value.trim())
                setActiveId(null)
              }}
              placeholder="your name"
            />
          </Field>
        </div>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Workspaces in view" value={table.data?.total ?? 0} path={TABLE_ICON} />
        <StatTile
          label="Open views"
          value={openIds.length}
          hint={board.data?.remembered ? 'remembered for this account' : 'nothing remembered yet'}
          path={SHARED_ICON}
        />
        <StatTile label="Your views" value={yourViews.length} path={CLONE_ICON} />
        <StatTile
          label="Unreadable filters"
          value={problems.length}
          hint={problems.length ? 'reported below, not hidden' : 'every filter in this view is readable'}
          path={INFERENCE_ICON}
        />
      </div>

      {note && <Note tone={note.tone}>{note.text}</Note>}

      {board.data?.missing_view_ids?.length > 0 && (
        <Note tone="warn">
          {board.data.missing_view_ids.length} view(s) you had open are gone or no longer yours:{' '}
          <span className="font-mono">{board.data.missing_view_ids.join(', ')}</span>. The rest opened
          fine.
        </Note>
      )}

      <div className="grid gap-5 lg:grid-cols-[19rem_1fr]">
        <aside className="space-y-4">
          <Card className="space-y-2">
            <h2 className="text-sm font-semibold text-foreground">Open</h2>
            {openIds.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nothing remembered for this account yet. Add a view below and it will be here next
                time.
              </p>
            ) : (
              <ul className="space-y-1">
                {openIds.map((id) => {
                  const view = views.find((entry) => entry.id === id)
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        onClick={() => setActiveId(id)}
                        aria-current={id === active ? 'true' : undefined}
                        className={`flex min-h-11 w-full items-center gap-2 rounded-lg px-2 text-left
                          text-sm transition-colors duration-150 hover:bg-muted/50 ${FOCUS}
                          ${id === active ? 'bg-muted text-foreground' : 'text-muted-foreground'}`}
                      >
                        <span className="min-w-0 flex-1 truncate">
                          {view?.name ?? <span className="italic">a view that is gone</span>}
                        </span>
                        {view?.visibility === 'public' && <Badge tone="insert">team</Badge>}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </Card>

          <Card className="space-y-2">
            <h2 className="text-sm font-semibold text-foreground">Add view</h2>
            <p className="text-xs text-muted-foreground">
              Start from one of the five defaults. Where the source defines the view its own words
              are shown; where it does not, that is said rather than hidden.
            </p>
            <ul className="space-y-2">
              {defaults.map((preset) => (
                <li key={preset.id} className="rounded-lg border border-border-subtle/25 p-2">
                  <p className="flex items-center gap-2 text-sm text-foreground">
                    <span className="min-w-0 flex-1 truncate">{preset.label}</span>
                    {preset.inferred ? <Badge tone="update">inferred</Badge> : <Badge>sourced</Badge>}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">{preset.definition}</p>
                  <Button
                    className="mt-2 w-full"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        const created = await triageApi.createView({ base: preset.id }, { actor })
                        setActiveId(created.id)
                        await remember([...openIds, created.id], created.id)
                      })
                    }
                  >
                    Add {preset.label}
                  </Button>
                </li>
              ))}
            </ul>
          </Card>
        </aside>

        <section className="space-y-4">
          {!active ? (
            <EmptyState
              title="No view open"
              description="Add one of the defaults on the left, or pick a view you had open last time."
            />
          ) : (
            <>
              <Card className="space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      {table.data?.view?.name ?? 'View'}
                      {table.data?.view?.visibility === 'public' ? (
                        <Badge tone="insert">team</Badge>
                      ) : (
                        <Badge>private</Badge>
                      )}
                      {table.data?.view?.cloned_from && <Badge tone="update">clone</Badge>}
                    </h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {table.data?.total ?? 0} workspaces &middot; {table.data?.columns.length ?? 0}{' '}
                      columns &middot; sorted by{' '}
                      <span className="font-mono">
                        {table.data?.sort?.field} {table.data?.sort?.direction}
                      </span>
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      disabled={busy}
                      onClick={() => setEditing((value) => !value)}
                      aria-expanded={editing}
                    >
                      <Glyph name="reorder" size={16} />
                      Columns
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          const copy = await triageApi.cloneView(active, {}, { actor })
                          setActiveId(copy.id)
                          await remember([...openIds, copy.id], copy.id)
                        })
                      }
                    >
                      <Glyph name="clone" size={16} />
                      Clone
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          const next = !table.data?.view?.visibility || table.data.view.visibility === 'private'
                          await triageApi.updateView(
                            active,
                            { visibility: next ? 'public' : 'private' },
                            { actor },
                          )
                          board.refetch()
                          table.refetch()
                        })
                      }
                    >
                      <Glyph name="shared" size={16} />
                      Make {table.data?.view?.visibility === 'public' ? 'private' : 'public'}
                    </Button>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border-subtle/20 pt-3">
                  <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                    Both filter groups must
                  </span>
                  <Toggle
                    checked={table.data?.match === 'all'}
                    onChange={() =>
                      run(async () => {
                        await triageApi.updateView(
                          active,
                          { match: table.data?.match === 'all' ? 'any' : 'all' },
                          { actor },
                        )
                        table.refetch()
                      })
                    }
                    label="Require both filter groups to match (All) or either one (Any)"
                  />
                  <span className="text-xs text-muted-foreground">
                    Active Pipeline needs <span className="font-mono">any</span>: its two arms are
                    alternatives.
                  </span>
                </div>

                <details className="text-xs">
                  <summary className={`min-h-11 cursor-pointer py-2 text-muted-foreground ${FOCUS}`}>
                    This view&apos;s filters
                  </summary>
                  <div className="grid gap-3 pt-2 sm:grid-cols-2">
                    <div>
                      <p className="font-medium tracking-wide text-muted-foreground uppercase">
                        workspaceFilters
                      </p>
                      <JsonView value={table.data?.workspace_filters} />
                    </div>
                    <div>
                      <p className="font-medium tracking-wide text-muted-foreground uppercase">
                        workspaceDomainFilters
                      </p>
                      <JsonView value={table.data?.workspace_domain_filters} />
                    </div>
                  </div>
                </details>
              </Card>

              {problems.length > 0 && (
                <Note tone="warn">
                  <span className="block font-medium">
                    {problems.length} condition(s) in this view could not be read, so they were left
                    out. The rows below may be wider than the view describes.
                  </span>
                  <ul className="mt-1 list-disc pl-4">
                    {problems.map((problem, index) => (
                      <li key={index}>{problem.detail}</li>
                    ))}
                  </ul>
                </Note>
              )}

              {editing && (
                <ColumnEditor
                  vocabulary={vocab.data}
                  columns={table.data?.columns ?? []}
                  saving={busy}
                  onClose={() => setEditing(false)}
                  onSave={(columns) =>
                    run(async () => {
                      await triageApi.updateView(active, { columns }, { actor })
                      setEditing(false)
                      table.refetch()
                    })
                  }
                />
              )}

              {table.loading ? (
                <Spinner label="Joining the pipeline" />
              ) : table.error ? (
                <ErrorNote error={table.error} onRetry={table.refetch} />
              ) : table.data ? (
                <Card className="p-0">
                  <TriageTable
                    table={table.data}
                    onSort={(field) =>
                      run(async () => {
                        const next =
                          table.data.sort.field === field && table.data.sort.direction === 'asc'
                            ? 'desc'
                            : 'asc'
                        await triageApi.updateView(active, { sort: { field, direction: next } }, { actor })
                        table.refetch()
                      })
                    }
                  />
                </Card>
              ) : null}
            </>
          )}
        </section>
      </div>

      <Card className="space-y-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Glyph name="inference" size={16} />
          What this infers
        </h2>
        <p className="text-xs text-muted-foreground">
          The research documents this workflow as a UI capability and gives two of the five default
          views a definition. Everything it left open is listed here by name, with the reading this
          build took and how to change it &mdash; so it can be disagreed with rather than discovered.
        </p>
        {inferences.loading ? (
          <Spinner label="Loading inferences" />
        ) : inferences.error ? (
          <ErrorNote error={inferences.error} onRetry={inferences.refetch} />
        ) : (
          <ul>
            {(inferences.data?.inferences ?? []).map((entry) => (
              <InferenceRow key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
