import { useEffect, useMemo, useState } from 'react'
import AuditLog from './pages/AuditLog'
import Dashboard from './pages/Dashboard'
import Rooms from './pages/Rooms'
import SchemaExplorer from './pages/SchemaExplorer'
import { clearOperator, Login, readOperator } from './pages/Login'
import { Icon } from './components/ui'
import { featureProblems, featureRoutes } from './lib/features'

/**
 * Routes are hash-based to keep the dependency surface small; deep links still
 * work, which matters because the audit log is something people share.
 *
 * The core pages are listed here. Everything built as a workflow feature is
 * discovered from src/features/ at build time and appended below, so a new
 * feature ships by adding a folder and never edits this file.
 */
const CORE_ROUTES = [
  { id: 'dashboard', label: 'Dashboard', icon: 'dashboard', Component: Dashboard },
  { id: 'rooms', label: 'Sales rooms', icon: 'rooms', Component: Rooms },
  { id: 'audit', label: 'Audit log', icon: 'audit', Component: AuditLog },
  { id: 'schema', label: 'Schema explorer', icon: 'schema', Component: SchemaExplorer },
]

const ROUTES = [
  ...CORE_ROUTES,
  ...featureRoutes.map((feature) => ({
    id: feature.id,
    label: feature.label || feature.id,
    icon: feature.icon,
    iconPath: feature.iconPath,
    Component: feature.Component,
  })),
]

function currentRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '')
  return ROUTES.find((route) => route.id === hash)?.id || 'dashboard'
}

export default function App() {
  const [operator, setOperator] = useState(() => readOperator())
  const [route, setRoute] = useState(currentRoute)
  const [navOpen, setNavOpen] = useState(false)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    const onHashChange = () => {
      setRoute(currentRoute())
      setNavOpen(false)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  // Fifty-odd entries in a sidebar is a wall of text. With a filter it is a
  // keyboard-navigable index, which is how anyone actually uses it.
  const needle = filter.trim().toLowerCase()
  const visibleRoutes = useMemo(
    () =>
      needle
        ? ROUTES.filter(
            (item) =>
              item.label.toLowerCase().includes(needle) || item.id.toLowerCase().includes(needle),
          )
        : ROUTES,
    [needle],
  )

  const activeRoute = ROUTES.find((item) => item.id === route)

  useEffect(() => {
    document.title = `${activeRoute?.label || 'Dashboard'} · Client Theater`
  }, [activeRoute?.label])

  if (!operator) {
    return <Login onSignedIn={setOperator} />
  }

  const signOut = () => {
    clearOperator()
    setOperator(null)
  }

  const Active = activeRoute?.Component || Dashboard

  return (
    <div className="min-h-[100dvh] lg:flex">
      {/* Mobile nav toggle: hidden on desktop where the sidebar is permanent. */}
      <div className="flex items-center justify-between border-b border-border-subtle bg-surface px-4 py-3 lg:hidden">
        <span className="font-display text-[15px] font-semibold">Client Theater</span>
        <button
          type="button"
          onClick={() => setNavOpen((open) => !open)}
          aria-expanded={navOpen}
          aria-controls="primary-nav"
          className="flex min-h-11 min-w-11 items-center justify-center rounded-sm border border-border-subtle bg-background text-foreground"
        >
          <span className="sr-only">Toggle navigation</span>
          <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
      </div>

      <nav
        id="primary-nav"
        aria-label="Primary"
        className={`${navOpen ? 'block' : 'hidden'} border-b border-border-subtle bg-surface lg:sticky lg:top-0 lg:block lg:h-[100dvh] lg:w-72 lg:shrink-0 lg:border-r lg:border-b-0`}
      >
        <div className="flex h-full flex-col">
          <div className="hidden items-center gap-2.5 border-b border-border-subtle px-4 py-4 lg:flex">
            <span aria-hidden className="size-3.5 rounded-xs bg-accent" />
            <div className="min-w-0">
              <p className="truncate font-display text-[15px] font-semibold">Client Theater</p>
              <p className="truncate font-mono text-[11px] text-muted-foreground">
                audited workspace
              </p>
            </div>
          </div>

          <div className="border-b border-border-subtle px-4 py-3">
            <label htmlFor="nav-filter" className="sr-only">
              Filter pages
            </label>
            <input
              id="nav-filter"
              type="search"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter pages"
              className="min-h-10 w-full rounded-sm border border-border-subtle bg-background px-3 text-[13px] placeholder:text-muted-foreground focus:border-accent"
            />
          </div>

          <ul className="flex-1 overflow-y-auto p-2">
            {visibleRoutes.map((item) => {
              const active = item.id === route
              return (
                <li key={item.id}>
                  <a
                    href={`#/${item.id}`}
                    aria-current={active ? 'page' : undefined}
                    className={`flex min-h-11 items-center gap-3 rounded-sm px-3 text-[13px] transition-colors duration-150 ${
                      active
                        ? 'bg-accent-soft font-medium text-accent'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    <Icon name={item.icon} path={item.iconPath} />
                    <span className="truncate">{item.label}</span>
                  </a>
                </li>
              )
            })}
            {visibleRoutes.length === 0 && (
              <li className="px-3 py-6 text-[13px] text-muted-foreground">
                No page matches “{filter}”.
              </li>
            )}
          </ul>

          <div className="border-t border-border-subtle p-4">
            <p className="font-mono text-[11px] text-muted-foreground">
              Signed in as {operator.name}
            </p>
            <button
              type="button"
              onClick={signOut}
              className="mt-3 min-h-11 w-full rounded-sm border border-border-subtle px-3 text-[13px] text-foreground transition-colors duration-150 hover:border-accent hover:text-accent"
            >
              Sign out
            </button>
          </div>
        </div>
      </nav>

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {featureProblems.length > 0 && (
            <div
              role="alert"
              className="mb-4 rounded-sm border border-warning/40 bg-warning/10 p-4"
            >
              <p className="text-[13px] font-semibold text-warning">
                {featureProblems.length} feature{featureProblems.length > 1 ? 's' : ''} could not be
                loaded
              </p>
              <ul className="mt-1 space-y-0.5">
                {featureProblems.map((problem) => (
                  <li key={problem.path} className="font-mono text-[12px] text-muted-foreground">
                    {problem.path}: {problem.error}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <Active />
        </div>
      </main>
    </div>
  )
}
