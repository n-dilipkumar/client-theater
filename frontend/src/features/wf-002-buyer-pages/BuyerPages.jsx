import { useEffect, useState } from 'react'
import { Button, EmptyState, ErrorNote, Field, Spinner, inputClass, useAsync } from '@/components/ui'
import PageBuilder from './PageBuilder'
import RoomView from './RoomView'
import { pagesApi } from './api'

/**
 * WF-002: build the room's buyer-facing pages from DSR fragments.
 *
 * The researched flow is per room - "In the Rooms list, click a room's Actions
 * -> Edit. This opens the room with its pages in edit mode" - so this page is
 * scoped to one room and offers the two halves of the workflow side by side:
 * the editor, and the buyer view that the Publish step reaches.
 *
 * On picking the room
 * ------------------
 * The branch got the room from `#/rooms/:roomId/pages`, a deep link it had to
 * add to `App.jsx`. That is a shared-file edit and is not carried over, so this
 * page picks the room from a drop-down and keeps the selection in its own
 * state. The consequence a human should decide on: there is no deep link into a
 * room's editor, so the Rooms page cannot yet offer an "Edit pages" button. See
 * the port report.
 *
 * Tab state is local rather than in the URL, so switching between editing and
 * reading the buyer view does not change the hash and cannot fight the host's
 * own hash routing.
 */

const TABS = [
  { id: 'edit', label: 'Build pages' },
  { id: 'view', label: 'Buyer view' },
]

function TabList({ active, onChange }) {
  return (
    <div role="tablist" aria-label="Room pages" className="flex gap-1 border-b border-border-subtle/25">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`wf002-tab-${tab.id}`}
          aria-selected={tab.id === active}
          aria-controls={`wf002-panel-${tab.id}`}
          tabIndex={tab.id === active ? 0 : -1}
          onClick={() => onChange(tab.id)}
          className={`-mb-px min-h-11 cursor-pointer border-b-2 px-4 text-sm transition-colors duration-150 ${
            tab.id === active
              ? 'border-accent text-accent'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

export default function BuyerPages() {
  const rooms = useAsync(() => pagesApi.rooms(), [])
  const [roomId, setRoomId] = useState('')
  const [tab, setTab] = useState('edit')
  const [pageId, setPageId] = useState(null)
  const [slug, setSlug] = useState(null)

  // Default to the first room once they arrive, so the page is never an empty
  // shell when there is something to show.
  useEffect(() => {
    const first = rooms.data?.records?.[0]?.id
    if (first && !roomId) setRoomId(first)
  }, [rooms.data, roomId])

  // A new room is a new page selection and a new buyer-view slug.
  useEffect(() => {
    setPageId(null)
    setSlug(null)
  }, [roomId])

  if (rooms.loading) return <Spinner label="Loading sales rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const records = rooms.data?.records || []
  const room = records.find((item) => item.id === roomId)

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-mono text-2xl font-semibold">Buyer pages</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Build a room&apos;s buyer-facing pages from DSR fragments, then publish them.
          </p>
        </div>

        <div className="w-full sm:w-72">
          <Field id="wf002-room" label="Room" hint="Pages belong to a room.">
            <select
              id="wf002-room"
              className={`${inputClass} min-w-56`}
              value={roomId}
              onChange={(event) => setRoomId(event.target.value)}
            >
              {records.length === 0 && <option value="">No rooms yet</option>}
              {records.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.data?.name || item.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </header>

      {records.length === 0 ? (
        <EmptyState
          title="No sales rooms yet"
          description="A page belongs to a room. Create a room on the Sales rooms page, then come back to build its pages."
        />
      ) : (
        <>
          <TabList active={tab} onChange={setTab} />

          {tab === 'edit' ? (
            <div role="tabpanel" id="wf002-panel-edit" aria-labelledby="wf002-tab-edit">
              <PageBuilder
                roomId={roomId}
                pageId={pageId}
                onSelectPage={setPageId}
                onOpenBuyerView={(nextSlug) => {
                  setSlug(nextSlug)
                  setTab('view')
                }}
              />
            </div>
          ) : (
            <div role="tabpanel" id="wf002-panel-view" aria-labelledby="wf002-tab-view">
              <RoomView
                roomId={roomId}
                slug={slug}
                onEdit={(nextPageId) => {
                  setPageId(nextPageId)
                  setTab('edit')
                }}
                onOpenPage={setSlug}
                onShowAll={() => {
                  setSlug(null)
                  setTab('edit')
                }}
              />
            </div>
          )}

          <footer className="border-t border-border-subtle/25 pt-4">
            <Button
              icon="back"
              onClick={() => {
                // The host's hash routing is the public contract for moving
                // between core pages, so this uses it rather than duplicating a
                // router here.
                window.location.hash = '#/rooms'
              }}
            >
              All sales rooms
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              Editing {room?.data?.name || 'this room'} requires the Room Collaborator role. Every
              change is recorded in the audit trail.
            </p>
          </footer>
        </>
      )}
    </div>
  )
}
