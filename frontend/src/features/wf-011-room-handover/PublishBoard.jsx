import { useState } from 'react'
import { relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { publishingApi } from './api'
import ShareDialog from './ShareDialog'
import WebhookPanel from './WebhookPanel'
import { ACCESS_GLYPHS, SHARE } from './icons'
import { accessMarkers, statusTone } from './publish'

/**
 * Publish board: every room with its status badge, and the way from draft to
 * live.
 *
 * The board is a view over the same `room` records the generic API owns; nothing
 * here stores status in a place of its own. Filters mirror the researched list
 * behaviour: a status list, an exact tag, and a search box.
 *
 * Search is applied on submit rather than per keystroke, so typing does not fire
 * a request for every letter.
 *
 * This was `src/pages/Publish.jsx` on the branch. Both `src/pages` and the
 * `ROUTES` array in `App.jsx` are shared, and appending to that array is what
 * made twelve workflow branches unmergeable; the page moved here and the folder
 * is all the registration there is.
 */
export default function PublishBoard() {
  const [status, setStatus] = useState('')
  const [query, setQuery] = useState('')
  const [q, setQ] = useState('')
  const [includeArchived, setIncludeArchived] = useState(false)
  const [sharing, setSharing] = useState(null)

  const board = useAsync(
    () => publishingApi.board({ status, q, include_archived: includeArchived }),
    [status, q, includeArchived],
  )

  const rooms = board.data?.rooms || []
  const counts = board.data?.counts || {}
  const statuses = board.data?.statuses || []

  function applySearch(event) {
    event.preventDefault()
    setQ(query.trim())
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Publish</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Take a room from draft to live, then hand over the link. Publishing is the audited
          change, and this app sends nothing for you: you get a link to paste.
        </p>
      </header>

      <Card>
        <form onSubmit={applySearch} className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
          <Field label="Search rooms" id="board-search" hint="Matches name, account or id.">
            <input
              id="board-search"
              className={inputClass}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Northwind"
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" icon="search">
              Search
            </Button>
            {q && (
              <Button
                onClick={() => {
                  setQuery('')
                  setQ('')
                }}
              >
                Clear
              </Button>
            )}
          </div>
        </form>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <FilterChip label="All" active={status === ''} count={null} onClick={() => setStatus('')} />
          {statuses.map((value) => (
            <FilterChip
              key={value}
              label={value}
              count={counts[value]}
              active={status === value}
              onClick={() => setStatus(value)}
            />
          ))}
          <label className="ml-auto flex min-h-11 items-center gap-2 text-sm text-muted-foreground">
            <input
              type="checkbox"
              className="h-5 w-5 accent-[var(--color-accent)]"
              checked={includeArchived}
              onChange={(event) => setIncludeArchived(event.target.checked)}
            />
            Show archived
          </label>
        </div>
      </Card>

      {board.loading && <Spinner label="Loading rooms" />}
      {board.error && <ErrorNote error={board.error} onRetry={board.refetch} />}

      {!board.loading && !board.error && rooms.length === 0 && (
        <EmptyState
          title="No rooms match"
          description="Clear the filters, or create a room on the Sales rooms screen first."
        />
      )}

      {!board.loading && !board.error && rooms.length > 0 && (
        <ul className="grid gap-4 md:grid-cols-2">
          {rooms.map((room) => (
            <li key={room.id}>
              <Card className="card-hover h-full">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate font-mono text-base font-semibold text-foreground">
                      {room.name}
                    </h2>
                    <p className="mt-0.5 truncate text-sm text-muted-foreground">
                      {room.account || 'No account set'}
                      {room.owner ? ` · ${room.owner}` : ''}
                    </p>
                  </div>
                  <Badge tone={statusTone(room.status)}>{room.status}</Badge>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span
                    className={`text-xs ${room.published ? 'text-accent' : 'text-muted-foreground'}`}
                  >
                    {room.published ? 'Public link is on' : 'Public link is off'}
                  </span>
                  {room.archived && <Badge tone="restore">archived</Badge>}
                  {room.is_template && <Badge tone="neutral">template</Badge>}
                </div>

                {accessMarkers(room.access).length > 0 && (
                  <ul className="mt-3 flex flex-wrap gap-2">
                    {accessMarkers(room.access).map((marker) => (
                      <li
                        key={marker.label}
                        className="flex items-center gap-1.5 text-xs text-muted-foreground"
                      >
                        <span className="text-muted-foreground/80">
                          <Icon path={ACCESS_GLYPHS[marker.glyph]} size={14} />
                        </span>
                        {marker.label}
                      </li>
                    ))}
                  </ul>
                )}

                <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">
                    Updated {relativeTime(room.updated_at)}
                  </span>
                  <Button onClick={() => setSharing(room.id)}>
                    <Icon path={SHARE} />
                    Share
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <WebhookPanel />

      {sharing && (
        <ShareDialog
          roomId={sharing}
          onClose={() => setSharing(null)}
          onChanged={board.refetch}
        />
      )}
    </div>
  )
}

/** A status filter. `aria-pressed` rather than a role, so it reads as a toggle. */
function FilterChip({ label, count, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex min-h-11 items-center gap-2 rounded-lg border px-3 text-sm
        transition-colors duration-200 ${
          active
            ? 'border-accent/50 bg-accent/15 text-accent'
            : 'border-border-subtle/40 bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground'
        }`}
    >
      {label}
      {count !== null && count !== undefined && (
        <span className="font-mono text-xs opacity-80">{count}</span>
      )}
    </button>
  )
}
