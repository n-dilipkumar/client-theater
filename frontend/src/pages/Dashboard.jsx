import { api, relativeTime } from '../lib/api'
import { Badge, Card, EmptyState, ErrorNote, JsonView, Spinner, StatCard, useAsync } from '../components/ui'

/**
 * Dashboard: the operational overview.
 *
 * Counts come straight from the audit trail, so the numbers on this page and
 * the rows in the audit log can never disagree.
 */
export default function Dashboard() {
  const stats = useAsync(() => api.stats(), [])
  const collections = useAsync(() => api.collections(), [])
  const recent = useAsync(() => api.audit({ limit: 8 }), [])

  if (stats.loading) return <Spinner label="Loading dashboard" />
  if (stats.error) return <ErrorNote error={stats.error} onRetry={stats.refetch} />

  const data = stats.data || {}
  const byCollection = Object.entries(data.by_collection || {})
  const byAction = Object.entries(data.by_action || {})
  const total = byCollection.reduce((sum, [, count]) => sum + count, 0) || 1

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Buyer-facing sales rooms on an audited SQLite store.
        </p>
      </header>

      <section aria-label="Key metrics" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Live records" value={data.records ?? 0} icon="database" hint="Across all collections" />
        <StatCard label="Soft deleted" value={data.deleted ?? 0} icon="trash" hint="Recoverable via restore" />
        <StatCard label="Audit entries" value={data.audit_entries ?? 0} icon="audit" hint="One per change, always" />
        <StatCard label="Collections" value={byCollection.length} icon="schema" hint="Schema-flexible" />
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Records by collection</h2>
          {byCollection.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">No records yet.</p>
          ) : (
            <ul className="mt-4 space-y-3">
              {byCollection.map(([name, count]) => (
                <li key={name}>
                  <div className="mb-1 flex items-baseline justify-between text-sm">
                    <span className="font-mono text-foreground">{name}</span>
                    <span className="font-mono text-muted-foreground">{count}</span>
                  </div>
                  <div
                    className="h-2 w-full overflow-hidden rounded-full bg-muted"
                    role="presentation"
                  >
                    <div
                      className="h-full rounded-full bg-accent transition-[width] duration-500"
                      style={{ width: `${Math.round((count / total) * 100)}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Changes by action</h2>
          {byAction.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">No changes recorded yet.</p>
          ) : (
            <ul className="mt-4 space-y-2">
              {byAction.map(([action, count]) => (
                <li key={action} className="flex items-center justify-between text-sm">
                  <Badge tone={action}>{action}</Badge>
                  <span className="font-mono text-muted-foreground">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Recent activity</h2>
          <a href="#/audit" className="text-sm text-accent underline underline-offset-2">
            View full audit log
          </a>
        </div>
        {recent.loading ? (
          <Spinner label="Loading activity" />
        ) : recent.error ? (
          <ErrorNote error={recent.error} onRetry={recent.refetch} />
        ) : (recent.data?.entries || []).length === 0 ? (
          <EmptyState
            title="Nothing has happened yet"
            description="Create a sales room and every change will be recorded here automatically."
          />
        ) : (
          <ul className="divide-y divide-border-subtle/15">
            {recent.data.entries.map((entry) => (
              <li key={entry.seq} className="flex flex-wrap items-center gap-3 py-2.5 text-sm">
                <Badge tone={entry.action}>{entry.action}</Badge>
                <span className="font-mono text-foreground">{entry.collection}</span>
                <span className="min-w-0 flex-1 truncate text-muted-foreground">{entry.summary}</span>
                <span className="text-xs text-muted-foreground">{relativeTime(entry.ts)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {collections.data && (collections.data.collections || []).length > 0 && (
        <Card>
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
            Discovered fields
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            No schema is declared in advance. These are the JSON paths actually in use, read
            straight from the dynamic index.
          </p>
          <div className="mt-3 space-y-4">
            {collections.data.collections.slice(0, 4).map((entry) => (
              <details key={entry.collection} className="rounded-lg border border-border-subtle/25 p-3">
                <summary className="cursor-pointer text-sm font-medium text-foreground">
                  <span className="font-mono">{entry.collection}</span>{' '}
                  <span className="text-muted-foreground">({entry.live} live)</span>
                </summary>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {(entry.fields || []).map((field) => (
                    <span
                      key={field.path}
                      className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
                    >
                      {field.path}
                    </span>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
