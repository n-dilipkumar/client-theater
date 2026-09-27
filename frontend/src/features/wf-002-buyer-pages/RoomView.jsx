import { useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
import { Badge, Button, Card, EmptyState, ErrorNote, useAsync } from '@/components/ui'
import BlockRenderer from './BlockRenderer'
import Glyph from './icons'
import { Note } from './primitives'
import { pagesApi } from './api'

/**
 * The buyer-facing view of a room: "the fragment appears on the page the next
 * time a member opens the room."
 *
 * This reads `/api/wf-002/rooms/{id}/view`, which serves published revisions
 * only. A collaborator's unsaved draft is not on that path at all, so what a
 * member sees is what was actually published - not what is sitting in the editor.
 */

function PublishedPage({ page, room, onEdit }) {
  return (
    <article className="space-y-3">
      <header>
        <h2 className="font-mono text-xl font-semibold text-foreground">{page.title}</h2>
        {page.summary && <p className="mt-1 text-sm text-muted-foreground">{page.summary}</p>}
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          revision {page.revision.number} · published {relativeTime(page.revision.published_at)}
          {page.revision.published_by ? ` by ${page.revision.published_by}` : ''}
        </p>
      </header>
      <div className="space-y-3">
        {page.blocks.map((block) => (
          <BlockRenderer
            key={block.id}
            block={block}
            room={room}
            documents={page.documents?.[block.id] || []}
          />
        ))}
      </div>
      {onEdit && (
        <p>
          <Button icon="page" onClick={onEdit}>
            Edit this page
          </Button>
        </p>
      )}
    </article>
  )
}

export default function RoomView({ roomId, slug, onEdit, onOpenPage, onShowAll }) {
  const view = useAsync(
    () => (slug ? pagesApi.viewPage(roomId, slug) : pagesApi.view(roomId)),
    [roomId, slug],
  )
  const room = useAsync(() => pagesApi.room(roomId), [roomId])
  const [showMeta, setShowMeta] = useState(false)

  if (view.loading) {
    return <p className="text-sm text-muted-foreground">Opening the room…</p>
  }
  if (view.error) {
    if (view.error.status === 404) {
      return (
        <EmptyState
          title="Nothing published here yet"
          description="This page has no published revision. A page reaches buyers only after it is published."
          action={<Button icon="back" onClick={onShowAll}>Back to the editor</Button>}
        />
      )
    }
    return <ErrorNote error={view.error} onRetry={view.refetch} />
  }

  const pages = slug ? [view.data] : view.data.pages
  const roomData = { id: roomId, data: room.data?.data }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate font-mono text-2xl font-semibold">
            {roomData?.name || 'Sales room'}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            What a member sees. {pages.length} published page{pages.length === 1 ? '' : 's'}.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon="page" onClick={onEdit}>
            Open in the editor
          </Button>
          {!slug && pages.length > 1 && (
            <Button icon="eye" onClick={() => setShowMeta((value) => !value)}>
              {showMeta ? 'Hide page details' : 'Page details'}
            </Button>
          )}
        </div>
      </header>

      {roomData?.status === 'archived' && (
        <Note tone="warn" title="Archived room">
          This digital sales room is archived. New comments cannot be added, and it can no longer
          be shared.
        </Note>
      )}

      {pages.length === 0 ? (
        <EmptyState
          title="No published pages"
          description="A room reaches its buyers through published pages. Build one in the editor, then publish it."
          action={
            <Button icon="page" variant="primary" onClick={onEdit}>
              Build the room&apos;s pages
            </Button>
          }
        />
      ) : (
        <>
          {!slug && pages.length > 1 && (
            <nav aria-label="Room pages">
              <ul className="flex flex-wrap gap-2">
                {pages.map((page) => (
                  <li key={page.id}>
                    <button
                      type="button"
                      onClick={() => onOpenPage(page.slug)}
                      aria-current={page.slug === slug ? 'page' : undefined}
                      className={`inline-flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm transition-colors duration-200 ${
                        page.slug === slug
                          ? 'bg-accent/15 text-accent'
                          : 'bg-muted text-foreground hover:bg-border-subtle'
                      }`}
                    >
                      <Glyph name="page" size={16} />
                      {page.title}
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          )}

          {pages.map((page) => (
            <Card key={page.id} className="space-y-4">
              <PublishedPage page={page} room={roomData} onEdit={() => onEdit(page.id)} />
              {showMeta && (
                <details className="rounded-lg border border-border-subtle/25 bg-background/40 p-3">
                  <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                    Page details
                  </summary>
                  <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                    <div>
                      <dt className="text-muted-foreground">Template</dt>
                      <dd className="truncate font-mono text-foreground">
                        {page.template_id || 'not set'}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Template version</dt>
                      <dd className="truncate font-mono text-foreground">
                        {page.template_version_id || 'not set'}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Fragment sets used</dt>
                      <dd className="font-mono text-foreground">
                        {(page.fragment_sets || []).join(', ') || 'none'}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Published</dt>
                      <dd className="text-foreground">{absoluteTime(page.revision.published_at)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Revision digest</dt>
                      <dd className="truncate font-mono text-foreground">
                        {page.revision.digest}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Blocks</dt>
                      <dd className="text-foreground">
                        <Badge>{page.blocks.length}</Badge>
                      </dd>
                    </div>
                  </dl>
                </details>
              )}
            </Card>
          ))}
        </>
      )}
    </div>
  )
}
