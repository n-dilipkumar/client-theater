import { useCallback, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  JsonView,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  AND_SEMANTICS,
  DOWNLOAD_TYPES,
  LIMITATION,
  READ_SOURCE,
  SCOPE_OWNER,
  listRooms,
  viewOnlyApi,
} from './api'
import { LOCK_ICON } from './icons'
import { DecisionResult, Notice, Toggle } from './primitives'

/**
 * WF-072: enforce view-only access and block bulk download.
 *
 * The page has three jobs, in this order, and the order is the design.
 *
 * **State the rule before showing the controls.** The specification's whole subject is that
 * three controls compose into one AND: the room's bulk download setting, the link's allow
 * download switch, and the item's own grant. A page that opened with three switches and no
 * statement of the AND would let a rep read them as three separate toggles, which is exactly
 * what the research warns against. So the AND is the first thing rendered, above the fold,
 * and the limitation sits beside it.
 *
 * **Let the two controls this workflow owns be changed here.** The room's bulk download
 * setting and the link's allow download switch are this workflow's rows, so they are switches
 * on this page. The per-item grant is not: it belongs to WF-074, and the page says so rather
 * than pretending to edit it.
 *
 * **Show the decision, not just the setting.** A security board that lists settings tells a
 * rep nothing about a specific download. The page also carries the three-way decision for one
 * item, and it renders the refusal the server sends back from a real attempt, so "which
 * control withheld this file" is answerable without reading the source.
 *
 * Every state this page can be in is rendered: loading, error, empty, and the two verdicts. A
 * security page that goes blank when the API is down looks like the controls failing, which is
 * the one reading that must never be possible.
 */

/** One link, its switch, and the decision for one item on it. */
function LinkPanel({ link, busy, onToggle }) {
  const [itemId, setItemId] = useState('')
  const [decision, setDecision] = useState(null)
  const [error, setError] = useState(null)
  const [checking, setChecking] = useState(false)

  const check = useCallback(
    async (attempt) => {
      const value = itemId.trim()
      if (!value) return
      setChecking(true)
      setError(null)
      try {
        const result = attempt
          ? await viewOnlyApi.download(link.id, { item_id: value, download_type: 'original' })
          : await viewOnlyApi.decision(link.id, value)
        // The read-only decision is returned whole; the download wraps it in `decision`.
        setDecision(attempt ? result.decision : result)
      } catch (err) {
        // A refused download is a 403 whose body is the decision. Any other failure is an
        // error the page should show as one.
        if (err.status === 403 && err.decision) {
          setDecision(err.decision)
        } else {
          setDecision(null)
          setError(err)
        }
      } finally {
        setChecking(false)
      }
    },
    [itemId, link.id],
  )

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-base font-semibold text-foreground">
            {link.name || 'Untitled link'}
          </h3>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{link.id}</p>
        </div>
        <Badge tone={link.allow_download ? 'update' : 'neutral'}>
          {link.allow_download ? 'Allow download on' : 'View-only'}
        </Badge>
      </div>

      <div className="mt-4 border-t border-border-subtle pt-3">
        <Toggle
          id={`allow-${link.id}`}
          label="Allow download on this link"
          hint="Off by default. A download still needs the room setting and the item's own grant."
          checked={Boolean(link.allow_download)}
          disabled={busy === link.id}
          onChange={(on) => onToggle(link, on)}
        />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
        <Field
          label="Check a download"
          id={`item-${link.id}`}
          hint="The item's own grant is read from WF-074's permission set."
        >
          <input
            id={`item-${link.id}`}
            className={inputClass}
            placeholder="item id"
            value={itemId}
            onChange={(event) => setItemId(event.target.value)}
          />
        </Field>
        <Button icon="search" disabled={checking || !itemId.trim()} onClick={() => check(false)}>
          Check
        </Button>
        <Button
          variant="primary"
          icon="audit"
          disabled={checking || !itemId.trim()}
          onClick={() => check(true)}
        >
          Attempt download
        </Button>
      </div>

      {error && (
        <div className="mt-3">
          <ErrorNote error={error} />
        </div>
      )}

      {decision && (
        <div className="mt-3">
          <DecisionResult decision={decision} />
        </div>
      )}
    </Card>
  )
}

/** The room settings panel: the room-wide escalation. */
function RoomPolicyPanel({ roomId, policy, loading, error, onToggle, busy }) {
  if (!roomId) return null
  if (loading) return <Spinner label="Loading the room policy" />
  if (error) return <ErrorNote error={error} />

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Room settings</h2>
      <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
        Bulk download allows viewers to download every document as a zip. It is the room-wide
        escalation, and it is the room's own control in the three-way AND.
      </p>
      <div className="mt-4 border-t border-border-subtle pt-3">
        <Toggle
          id="bulk-download"
          label="Bulk download"
          hint={
            policy?.configured
              ? "Allow viewers to download every document as a zip."
              : "Never set on this room. The documented default is off."
          }
          checked={Boolean(policy?.bulk_download)}
          disabled={busy}
          onChange={(on) => onToggle(on)}
        />
      </div>
    </Card>
  )
}

/** The recorded download events: the audit trail reads these rows. */
function DownloadLog({ downloads, loading, error, onRetry }) {
  if (loading) return <Spinner label="Loading the download log" />
  if (error) return <ErrorNote error={error} onRetry={onRetry} />
  const rows = downloads || []
  if (rows.length === 0) {
    return (
      <EmptyState
        title="No downloads recorded"
        description="A permitted download writes a view row with downloaded_at and download_type. A refused download writes nothing, so this log holds the downloads that happened."
      />
    )
  }
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Download log</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Every permitted fetch. The audit trail reads these rows, which is what makes a leak by
        download auditable.
      </p>
      <div className="mt-4">
        <JsonView value={rows.slice(0, 20)} />
      </div>
    </Card>
  )
}

export function ViewOnlyAccessPage() {
  const [roomId, setRoomId] = useState('')
  const [busy, setBusy] = useState(null)
  const [policyError, setPolicyError] = useState(null)

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => viewOnlyApi.vocabulary(), [])
  const board = useAsync(() => viewOnlyApi.summary(roomId), [roomId])
  const links = useAsync(
    () => (roomId ? viewOnlyApi.links(roomId) : Promise.resolve({ links: [], count: 0 })),
    [roomId],
  )
  const policy = useAsync(
    () =>
      roomId
        ? viewOnlyApi.roomPolicy(roomId)
        : Promise.resolve({ configured: false, bulk_download: false }),
    [roomId],
  )
  const log = useAsync(
    () =>
      roomId
        ? viewOnlyApi.allDownloads(roomId)
        : Promise.resolve({ downloads: [], count: 0 }),
    [roomId],
  )

  const onToggleLink = useCallback(
    async (link, on) => {
      setBusy(link.id)
      try {
        // The controls live on the server, so a change is only real once the server has
        // answered. Refetching rather than patching local state means the page can never show
        // a switch in a position the store is not in, which for a security control is the one
        // failure that matters.
        await viewOnlyApi.updateLink(link.id, { allow_download: on })
        links.refetch()
        board.refetch()
      } finally {
        setBusy(null)
      }
    },
    [links, board],
  )

  const onToggleBulk = useCallback(
    async (on) => {
      setBusy('policy')
      setPolicyError(null)
      try {
        await viewOnlyApi.setRoomPolicy(roomId, { bulk_download: on })
        policy.refetch()
        board.refetch()
      } catch (error) {
        setPolicyError(error)
      } finally {
        setBusy(null)
      }
    },
    [roomId, policy, board],
  )

  if (rooms.loading || vocabulary.loading) {
    return <Spinner label="Loading view-only access" />
  }
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (links.error) return <ErrorNote error={links.error} onRetry={links.refetch} />

  const vocab = vocabulary.data
  const roomRows = rooms.data?.records || rooms.data || []
  const linkRows = links.data?.links || []
  const selectedRoom = roomRows.find((room) => room.id === roomId)

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">View-only access</h1>
        <p className="mt-2 max-w-3xl text-[15px] text-muted-foreground">
          Three controls compose into one AND. A download is served only when the room's bulk
          download setting, the link's allow download switch, and the item's own grant all
          permit it. Every permitted fetch is recorded so a leak by download is auditable.
        </p>
      </header>

      {/* The rule, first and above the fold, with the honest limit beside it. */}
      <Notice tone="info" title={AND_SEMANTICS}>
        <p>{LIMITATION}</p>
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Governed links" value={board.data?.links ?? 0} icon="rooms" />
        <StatCard
          label="Allow download on"
          value={board.data?.allow_download ?? 0}
          hint={`${board.data?.view_only_links ?? 0} view-only`}
          icon="schema"
        />
        <StatCard
          label="Bulk download rooms"
          value={board.data?.bulk_download ?? 0}
          hint={`${board.data?.rooms_configured ?? 0} policies set`}
          icon="database"
        />
        <StatCard
          label="Recorded downloads"
          value={board.data?.downloads ?? 0}
          hint={`${board.data?.views ?? 0} view rows`}
          icon="audit"
        />
      </div>

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Room"
            id="wf072-room"
            hint="Choose a room to govern its policy and its links."
          >
            <select
              id="wf072-room"
              className={inputClass}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              <option value="">Select a room</option>
              {roomRows.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.data?.name || room.id}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex items-end">
            <p className="text-xs text-muted-foreground">{READ_SOURCE}</p>
          </div>
        </div>
      </Card>

      <RoomPolicyPanel
        roomId={roomId}
        policy={policy.data}
        loading={policy.loading}
        error={policyError || policy.error}
        onToggle={onToggleBulk}
        busy={busy === 'policy'}
      />

      {!roomId ? (
        <EmptyState
          title="Select a room"
          description="The controls belong to a room and its links, so choose one to see the policy and the links it governs."
        />
      ) : (
        <section className="space-y-4">
          <h2 className="font-display text-lg font-semibold text-foreground">
            Links in {selectedRoom?.data?.name || roomId}
          </h2>
          {linkRows.length === 0 ? (
            <EmptyState
              title="No governed links yet"
              description="A link appears here once it is created for this room. A new link is view-only until its allow download switch is turned on."
            />
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {linkRows.map((link) => (
                <LinkPanel
                  key={link.id}
                  link={link}
                  busy={busy}
                  onToggle={onToggleLink}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {roomId && (
        <DownloadLog
          downloads={log.data?.downloads}
          loading={log.loading}
          error={log.error}
          onRetry={log.refetch}
        />
      )}

      <Card>
        <h2 className="font-display text-lg font-semibold text-foreground">
          What each control is worth
        </h2>
        <ul className="mt-3 divide-y divide-border-subtle">
          {(vocab?.controls || []).map((control) => (
            <li key={control.field} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">{control.label}</span>
                <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                  {control.cli_flag}
                </span>
              </span>
              <Badge tone={control.owned ? 'insert' : 'neutral'}>
                {control.owned ? 'Owned here' : 'Read from WF-074'}
              </Badge>
            </li>
          ))}
        </ul>
        <p className="mt-3 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
          {SCOPE_OWNER}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Download kinds: {DOWNLOAD_TYPES.join(', ')}.
        </p>
      </Card>
    </div>
  )
}

export default {
  id: 'wf-072-enforce-view-only-access-and-block-bulk',
  label: 'View-only access',
  // The glyph is not in the shared PATHS map, so `iconPath` carries it and `icon` falls back
  // to the shared mark. `components/ui.jsx` is not edited.
  icon: 'audit',
  iconPath: LOCK_ICON,
  order: 720,
  Component: ViewOnlyAccessPage,
}
