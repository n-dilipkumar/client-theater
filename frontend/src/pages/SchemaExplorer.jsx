import { api } from '../lib/api'
import { Card, EmptyState, ErrorNote, Spinner, useAsync } from '../components/ui'

/**
 * Schema explorer.
 *
 * The store declares no schema, so this page reads the dynamic index to show
 * which JSON paths are actually in use per collection. It doubles as living
 * proof that a team can add fields without a migration.
 */
export default function SchemaExplorer() {
  const { data, error, loading, refetch } = useAsync(() => api.collections(), [])

  if (loading) return <Spinner label="Reading the dynamic index" />
  if (error) return <ErrorNote error={error} onRetry={refetch} />

  const collections = data?.collections || []

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Schema explorer</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Fields are discovered at runtime from stored data, not declared up front. Any team can
          add a field to any collection and it becomes queryable immediately.
        </p>
      </header>

      {collections.length === 0 ? (
        <EmptyState
          title="No collections yet"
          description="Create a sales room and this page will fill in with the fields it discovers."
        />
      ) : (
        <ul className="space-y-4">
          {collections.map((entry) => (
            <li key={entry.collection}>
              <Card>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="font-mono text-base font-semibold">{entry.collection}</h2>
                  <p className="text-xs text-muted-foreground">
                    {entry.live} live · {entry.deleted || 0} deleted · last updated{' '}
                    {entry.last_updated ? new Date(entry.last_updated).toLocaleString() : '—'}
                  </p>
                </div>

                {(entry.fields || []).length === 0 ? (
                  <p className="mt-3 text-sm text-muted-foreground">No indexed fields.</p>
                ) : (
                  <>
                    <p className="mt-4 mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      Discovered JSON paths
                    </p>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[420px] text-left text-sm">
                        <thead>
                          <tr className="border-b border-border-subtle/30 text-xs tracking-wide text-muted-foreground uppercase">
                            <th scope="col" className="py-2 pr-4 font-medium">Path</th>
                            <th scope="col" className="py-2 text-right font-medium">Records</th>
                          </tr>
                        </thead>
                        <tbody>
                          {entry.fields.map((field) => (
                            <tr key={field.path} className="border-b border-border-subtle/15 last:border-0">
                              <td className="py-2 pr-4 font-mono text-[13px] text-foreground">
                                {field.path}
                              </td>
                              <td className="py-2 text-right font-mono text-[13px] text-muted-foreground">
                                {field.records}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Card>
        <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Query it yourself</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Any discovered path is filterable through the API without a schema change.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-background/60 p-3 font-mono text-xs text-muted-foreground">
{`# compact form
GET /api/records/room?where=stage=negotiation

# JSON form, for nested paths
GET /api/records/room?where={"branding.theme":"dark"}`}
        </pre>
      </Card>
    </div>
  )
}
