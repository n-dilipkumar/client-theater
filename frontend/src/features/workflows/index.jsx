import { apiRequest } from '@/lib/api'
import { Badge, Card, EmptyState, ErrorNote, Spinner, StatCard, useAsync } from '@/components/ui'

/**
 * Workflows: what this install actually has.
 *
 * Reads the feature registry over HTTP rather than importing anything, so this
 * page stays correct as features are added and needs no edit when they are. It
 * is also the reference for how a feature under src/features/ is shaped: a
 * default export of { id, label, icon, Component } and nothing else.
 */
function WorkflowsPage() {
  const features = useAsync(() => apiRequest('/features'), [])

  if (features.loading) return <Spinner label="Loading workflows" />
  if (features.error) return <ErrorNote error={features.error} onRetry={features.refetch} />

  const data = features.data || {}
  const installed = data.features || []
  const failed = data.failed || []
  const routes = installed.reduce((sum, feature) => sum + (feature.routes?.length || 0), 0)

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Workflows</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Each workflow is a self-contained plugin: its own module, its own API prefix, its own
          page. Nothing here is hard-coded.
        </p>
      </header>

      <section aria-label="Feature metrics" className="grid gap-4 sm:grid-cols-3">
        <StatCard label="Installed" value={installed.length} icon="rooms" hint="Loaded feature plugins" />
        <StatCard label="API routes" value={routes} icon="schema" hint="Mounted under /api" />
        <StatCard
          label="Failed to load"
          value={failed.length}
          icon="audit"
          hint={failed.length ? 'Reported below' : 'All healthy'}
        />
      </section>

      {failed.length > 0 && (
        <Card className="border border-amber-500/40">
          <h2 className="font-mono text-sm font-semibold tracking-wide uppercase text-amber-300">
            Features that did not load
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            A broken feature is skipped so the rest of the product keeps working. It is listed here
            rather than hidden.
          </p>
          <ul className="mt-3 space-y-2">
            {failed.map((feature) => (
              <li key={feature.module} className="rounded-lg border border-border-subtle/25 p-3">
                <p className="font-mono text-sm text-foreground">{feature.id || feature.module}</p>
                <p className="mt-1 font-mono text-xs text-destructive">{feature.error}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">Installed features</h2>
        {installed.length === 0 ? (
          <div className="mt-4">
            <EmptyState
              title="No feature plugins installed"
              description="Add a module under backend/dsr/features/ and a folder under frontend/src/features/ to ship a workflow."
            />
          </div>
        ) : (
          <ul className="mt-4 divide-y divide-border-subtle/15">
            {installed.map((feature) => (
              <li key={feature.id} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  {feature.ticket && <Badge tone="update">{feature.ticket}</Badge>}
                  <span className="text-sm font-medium text-foreground">{feature.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">{feature.prefix || '—'}</span>
                </div>
                {feature.description && (
                  <p className="mt-1 text-sm text-muted-foreground">{feature.description}</p>
                )}
                {(feature.routes || []).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {feature.routes.map((route) => (
                      <span
                        key={`${route.path}-${route.methods.join(',')}`}
                        className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
                      >
                        {route.methods.join('|')} {route.path}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

export default {
  id: 'workflows',
  label: 'Workflows',
  icon: 'rooms',
  order: 50,
  Component: WorkflowsPage,
}
