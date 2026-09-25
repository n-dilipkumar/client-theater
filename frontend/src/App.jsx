import { useEffect, useState } from 'react'
import AuditLog from './pages/AuditLog'
import Dashboard from './pages/Dashboard'
import Rooms from './pages/Rooms'
import SchemaExplorer from './pages/SchemaExplorer'
import { Icon } from './components/ui'

/**
 * Routes are hash-based to keep the dependency surface small; deep links still
 * work, which matters because the audit log is something people share.
 */
const ROUTES = [
  { id: 'dashboard', label: 'Dashboard', icon: 'dashboard', Component: Dashboard },
  { id: 'rooms', label: 'Sales rooms', icon: 'rooms', Component: Rooms },
  { id: 'audit', label: 'Audit log', icon: 'audit', Component: AuditLog },
  { id: 'schema', label: 'Schema explorer', icon: 'schema', Component: SchemaExplorer },
]

function currentRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '')
  return ROUTES.find((route) => route.id === hash)?.id || 'dashboard'
}

export default function App() {
  const [route, setRoute] = useState(currentRoute)
  const [navOpen, setNavOpen] = useState(false)

  useEffect(() => {
    const onHashChange = () => {
      setRoute(currentRoute())
      setNavOpen(false)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const Active = ROUTES.find((item) => item.id === route)?.Component || Dashboard

  return (
    <div className="min-h-screen lg:flex">
      {/* Mobile nav toggle: hidden on desktop where the sidebar is permanent. */}
      <div className="flex items-center justify-between border-b border-border-subtle/25 bg-surface/60 px-4 py-3 backdrop-blur lg:hidden">
        <span className="font-mono text-sm font-semibold">Digital Sales Room</span>
        <button
          type="button"
          onClick={() => setNavOpen((open) => !open)}
          aria-expanded={navOpen}
          aria-controls="primary-nav"
          className="flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-muted text-foreground"
        >
          <span className="sr-only">Toggle navigation</span>
          <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
      </div>

      <nav
        id="primary-nav"
        aria-label="Primary"
        className={`${navOpen ? 'block' : 'hidden'} border-b border-border-subtle/25 bg-surface/60 backdrop-blur lg:sticky lg:top-0 lg:block lg:h-screen lg:w-64 lg:shrink-0 lg:border-r lg:border-b-0`}
      >
        <div className="flex h-full flex-col p-4">
          <div className="mb-6 hidden items-center gap-2 px-2 lg:flex">
            <span className="rounded-lg bg-accent/15 p-2 text-accent">
              <Icon name="database" size={20} />
            </span>
            <div className="min-w-0">
              <p className="truncate font-mono text-sm font-semibold">Digital Sales Room</p>
              <p className="truncate text-xs text-muted-foreground">open source</p>
            </div>
          </div>

          <ul className="flex flex-col gap-1">
            {ROUTES.map((item) => {
              const active = item.id === route
              return (
                <li key={item.id}>
                  <a
                    href={`#/${item.id}`}
                    aria-current={active ? 'page' : undefined}
                    className={`flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm transition-colors duration-150 ${
                      active
                        ? 'bg-accent/15 font-medium text-accent'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    <Icon name={item.icon} />
                    {item.label}
                  </a>
                </li>
              )
            })}
          </ul>

          <div className="mt-auto hidden rounded-lg border border-border-subtle/25 p-3 lg:block">
            <p className="font-mono text-xs text-foreground">Audited storage</p>
            <p className="mt-1 text-xs text-muted-foreground">
              All writes pass through one wrapper that records an audit row in the same
              transaction.
            </p>
          </div>
        </div>
      </nav>

      <main className="min-w-0 flex-1 p-4 sm:p-6 lg:p-8">
        <div className="mx-auto max-w-6xl">
          <Active />
        </div>
      </main>
    </div>
  )
}
