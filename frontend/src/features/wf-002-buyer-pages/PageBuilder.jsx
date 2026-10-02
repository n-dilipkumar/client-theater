import { useCallback, useEffect, useMemo, useState } from 'react'
import { absoluteTime } from '@/lib/api'
import { Badge, Button, Card, EmptyState, ErrorNote, Field, inputClass, useAsync } from '@/components/ui'
import BlockRenderer from './BlockRenderer'
import ConfigPanel from './ConfigPanel'
import Glyph from './icons'
import { Note } from './primitives'
import { isPermissionDenied, pagesApi } from './api'

/**
 * The room page editor: WF-002's "Build the room's buyer-facing pages from DSR
 * fragments".
 *
 * The flow it implements, from the research:
 *   1. open the room with its pages in edit mode
 *   2. choose *Components* -> *Fragments*, then open a fragment set
 *   3. drag a fragment onto the page
 *   4. select the fragment on the page and set its fields in the configuration panel
 *   5. click **Publish** - "the fragment appears on the page the next time a
 *      member opens the room"
 *
 * Step 3 is a drag and step 5 is a separate button, because both are load
 * bearing. Nothing here publishes: an edit stays a draft until Publish is
 * pressed, and the header says so at all times.
 *
 * Accessibility: HTML drag-and-drop is pointer-only, so every drag has a
 * keyboard equivalent. Each palette entry is a button that appends the fragment,
 * and each placed block has Move up / Move down controls. Both paths call the
 * same API, so neither can drift from the other.
 *
 * Props come from `BuyerPages`, not from the host. The host renders a feature's
 * component with no props at all (`<Active />` in App.jsx), so a feature that
 * needs a room picks one itself rather than waiting for a route that the shared
 * router file would have to learn about.
 */

/** Which actor the editor acts as. Resolved against the room's own grant. */
const ACTOR_KEY = 'dsr.actor'

function readActor() {
  try {
    return window.localStorage.getItem(ACTOR_KEY) || ''
  } catch {
    return ''
  }
}

function writeActor(value) {
  try {
    if (value) window.localStorage.setItem(ACTOR_KEY, value)
    else window.localStorage.removeItem(ACTOR_KEY)
  } catch {
    // A blocked localStorage is not a reason to break the editor.
  }
}

function slugify(value) {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
}

function FragmentPalette({ catalogue, onAdd, disabled, adding }) {
  const [openSet, setOpenSet] = useState(catalogue.sets[0]?.key ?? null)

  return (
    <div className="space-y-3">
      {catalogue.sets.map((set) => {
        const open = openSet === set.key
        const fragments = catalogue.fragments.filter((item) => item.set === set.key)
        return (
          <section key={set.key} className="rounded-lg border border-border-subtle/30">
            <h3>
              <button
                type="button"
                onClick={() => setOpenSet(open ? null : set.key)}
                aria-expanded={open}
                className="flex min-h-11 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left transition-colors duration-200 hover:bg-muted"
              >
                <span className="min-w-0">
                  <span className="block font-mono text-xs font-semibold text-foreground">
                    {set.name}
                  </span>
                  <span className="block text-xs text-muted-foreground">
                    {fragments.length} fragment{fragments.length === 1 ? '' : 's'}
                    {set.source === 'custom' && ' · added by your team'}
                  </span>
                </span>
                <span
                  aria-hidden="true"
                  className={`shrink-0 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none ${
                    open ? 'rotate-90' : ''
                  }`}
                >
                  <Glyph name="chevron" size={16} />
                </span>
              </button>
            </h3>
            {open && (
              <ul className="space-y-1 px-2 pb-2">
                {fragments.map((fragment) => (
                  <li key={fragment.key}>
                    <button
                      type="button"
                      draggable
                      disabled={disabled}
                      onDragStart={(event) => {
                        event.dataTransfer.setData('text/plain', fragment.key)
                        event.dataTransfer.effectAllowed = 'copy'
                      }}
                      onClick={() => onAdd(fragment.key)}
                      className="flex min-h-11 w-full cursor-grab items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-200 hover:bg-muted active:cursor-grabbing disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="shrink-0 text-accent">
                        <Glyph name={fragment.icon} size={16} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-foreground">
                          {fragment.name}
                        </span>
                        {fragment.summary && (
                          <span className="block truncate text-xs text-muted-foreground">
                            {fragment.summary}
                          </span>
                        )}
                      </span>
                      <span className="shrink-0 text-muted-foreground" aria-hidden="true">
                        <Glyph name="plus" size={14} />
                      </span>
                      <span className="sr-only">Add to page</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )
      })}
      {adding === 'add' && (
        <p role="status" className="text-xs text-muted-foreground">
          Adding fragment…
        </p>
      )}
    </div>
  )
}

function PageList({ pages, selectedId, onSelect, onCreate, creating }) {
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-mono text-sm font-semibold text-foreground">Pages</h2>
        <Button icon="plus" onClick={onCreate} disabled={creating}>
          New page
        </Button>
      </div>
      {pages.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          This room has no pages yet. Create one, then add fragments to it.
        </p>
      ) : (
        <ul className="space-y-1">
          {pages.map((page) => {
            const active = page.id === selectedId
            return (
              <li key={page.id}>
                <button
                  type="button"
                  onClick={() => onSelect(page.id)}
                  aria-current={active ? 'true' : undefined}
                  className={`flex min-h-11 w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left transition-colors duration-200 ${
                    active ? 'bg-accent/15 text-accent' : 'text-foreground hover:bg-muted'
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{page.data.title}</span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">
                      /{page.data.slug}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1">
                    {page.has_unpublished_changes && <Badge tone="restore">draft</Badge>}
                    {page.published ? <Badge tone="insert">live</Badge> : <Badge>draft</Badge>}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

function PageCanvas({
  page,
  fragmentsByKey,
  room,
  selectedId,
  onSelect,
  onMove,
  onRemove,
  onDropFragment,
  onTitleChange,
  savingTitle,
}) {
  const [dropActive, setDropActive] = useState(false)
  const blocks = page.blocks || []

  return (
    <div className="space-y-3">
      <Field label="Page title" id="page-title" hint="Also the page heading buyers see.">
        <input
          id="page-title"
          className={inputClass}
          value={page.data.title}
          disabled={savingTitle}
          onChange={(event) => onTitleChange(event.target.value)}
        />
      </Field>

      <div
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes('text/plain')) {
            event.preventDefault()
            event.dataTransfer.dropEffect = 'copy'
            setDropActive(true)
          }
        }}
        onDragLeave={() => setDropActive(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDropActive(false)
          const key = event.dataTransfer.getData('text/plain')
          if (key) onDropFragment(key)
        }}
        className={`space-y-2 rounded-xl border-2 border-dashed p-3 transition-colors duration-200 ${
          dropActive ? 'border-accent bg-accent/5' : 'border-border-subtle/40'
        }`}
      >
        {blocks.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-muted-foreground">
            Nothing on this page yet. Drag a fragment here, or use the Add button in the palette.
          </p>
        ) : (
          <ol className="space-y-2">
            {blocks.map((block, index) => {
              const fragment = fragmentsByKey[block.fragment]
              const active = block.id === selectedId
              return (
                <li key={block.id}>
                  <div
                    className={`rounded-lg border transition-colors duration-200 ${
                      active
                        ? 'border-accent bg-accent/5'
                        : 'border-border-subtle/40 hover:border-border-subtle/70'
                    }`}
                  >
                    <div className="flex items-center gap-2 border-b border-border-subtle/20 px-2 py-1.5">
                      <button
                        type="button"
                        onClick={() => onSelect(block.id)}
                        aria-pressed={active}
                        className="flex min-h-11 flex-1 cursor-pointer items-center gap-2 rounded-md px-1 text-left"
                      >
                        <span className="shrink-0 text-accent" aria-hidden="true">
                          <Glyph name={fragment?.icon || 'schema'} size={16} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm text-foreground">
                            {fragment?.name || block.fragment}
                          </span>
                          <span className="block font-mono text-xs text-muted-foreground">
                            position {index + 1} of {blocks.length}
                          </span>
                        </span>
                      </button>
                      <span className="flex shrink-0 items-center gap-1">
                        <Button
                          icon="up"
                          onClick={() => onMove(block.id, -1)}
                          disabled={index === 0}
                          aria-label={`Move ${fragment?.name || block.fragment} up`}
                          className="px-2"
                        >
                          <span className="sr-only">Up</span>
                        </Button>
                        <Button
                          icon="down"
                          onClick={() => onMove(block.id, 1)}
                          disabled={index === blocks.length - 1}
                          aria-label={`Move ${fragment?.name || block.fragment} down`}
                          className="px-2"
                        >
                          <span className="sr-only">Down</span>
                        </Button>
                        <Button
                          icon="trash"
                          variant="danger"
                          onClick={() => onRemove(block.id)}
                          aria-label={`Remove ${fragment?.name || block.fragment}`}
                          className="px-2"
                        >
                          <span className="sr-only">Remove</span>
                        </Button>
                      </span>
                    </div>
                    <div className="p-3">
                      <BlockRenderer
                        block={{ ...block, fields: fragment?.fields || [] }}
                        room={room}
                        documents={[]}
                      />
                    </div>
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </div>
  )
}

export default function PageBuilder({ roomId, pageId, onSelectPage, onOpenBuyerView }) {
  const [actor, setActor] = useState(readActor)
  // The selected block is scoped to the room it was selected in: a change of room
  // invalidates it, so it reads as nothing selected rather than pointing at an
  // id that is no longer on the page. Deriving it means the first render of the
  // new room is already correct.
  const [blockChoice, setBlockChoice] = useState({ forRoom: undefined, blockId: null })
  const selectedBlockId = blockChoice.forRoom === roomId ? blockChoice.blockId : null
  const setSelectedBlockId = (next) =>
    setBlockChoice({
      forRoom: roomId,
      blockId: typeof next === 'function' ? next(selectedBlockId) : next,
    })
  const [busy, setBusy] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [status, setStatus] = useState('')
  const [newPageOpen, setNewPageOpen] = useState(false)
  const [newPageTitle, setNewPageTitle] = useState('')

  useEffect(() => writeActor(actor), [actor])

  const room = useAsync(() => pagesApi.list(roomId), [roomId])
  const catalogue = useAsync(() => pagesApi.catalogue(), [])
  // The document selectors take a file from the room's documents, so the
  // configuration panel offers this room's documents and nothing else.
  const documents = useAsync(() => pagesApi.documents(roomId), [roomId])

  const params = actor ? { actor } : {}

  const pages = useMemo(() => room.data?.pages || [], [room.data])
  const current = useMemo(
    () => pages.find((page) => page.id === pageId) || pages[0] || null,
    [pages, pageId],
  )
  const fragmentsByKey = useMemo(() => {
    const map = {}
    for (const fragment of catalogue.data?.fragments || []) map[fragment.key] = fragment
    return map
  }, [catalogue.data])

  const block = (current?.blocks || []).find((item) => item.id === selectedBlockId) || null
  const fragment = block ? fragmentsByKey[block.fragment] : null

  const announce = useCallback((message) => {
    setStatus('')
    window.setTimeout(() => setStatus(message), 30)
  }, [])

  const reload = useCallback(() => {
    room.refetch()
  }, [room])

  async function run(key, action) {
    setBusy(key)
    setActionError(null)
    try {
      return await action()
    } catch (error) {
      // The whole error, not just its message: the status is what tells a
      // permission refusal apart from a rejected value.
      setActionError(error)
      return null
    } finally {
      setBusy(null)
    }
  }

  const addFragment = (key) =>
    run('add', async () => {
      if (!current) return
      const updated = await pagesApi.addBlock(
        roomId,
        current.id,
        { fragment: key },
        { ...params, expected_revision: current.revision },
      )
      setSelectedBlockId(updated.blocks[updated.blocks.length - 1]?.id ?? null)
      announce(`${fragmentsByKey[key]?.name || key} added to the draft. Publish to reach buyers.`)
      reload()
    })

  const removeBlock = (blockId) =>
    run('remove', async () => {
      await pagesApi.removeBlock(roomId, current.id, blockId, {
        ...params,
        expected_revision: current.revision,
      })
      setSelectedBlockId(null)
      announce('Fragment removed from the draft.')
      reload()
    })

  const moveBlock = (blockId, delta) =>
    run('move', async () => {
      const ids = current.blocks.map((item) => item.id)
      const from = ids.indexOf(blockId)
      const to = from + delta
      if (to < 0 || to >= ids.length) return
      ids.splice(to, 0, ids.splice(from, 1)[0])
      await pagesApi.reorder(roomId, current.id, ids, {
        ...params,
        expected_revision: current.revision,
      })
      announce('Block order changed in the draft.')
      reload()
    })

  const changeConfig = (configPatch) =>
    run('config', async () => {
      await pagesApi.updateBlock(
        roomId,
        current.id,
        block.id,
        { config: configPatch },
        { ...params, expected_revision: current.revision },
      )
      announce('Configuration saved to the draft.')
      reload()
    })

  const saveTitle = (title) =>
    run('title', async () => {
      await pagesApi.update(
        roomId,
        current.id,
        { title, slug: slugify(title) || current.data.slug },
        { ...params, expected_revision: current.revision },
      )
      reload()
    })

  const createPage = () =>
    run('create', async () => {
      const created = await pagesApi.create(
        roomId,
        { title: newPageTitle || 'Untitled page' },
        params,
      )
      setNewPageOpen(false)
      setNewPageTitle('')
      onSelectPage(created.id)
      announce('Page created as a draft.')
      reload()
    })

  const publish = () =>
    run('publish', async () => {
      await pagesApi.publish(
        roomId,
        current.id,
        {},
        { ...params, expected_revision: current.revision },
      )
      announce('Published. Buyers see this on their next room load.')
      reload()
    })

  const unpublish = () =>
    run('unpublish', async () => {
      await pagesApi.unpublish(roomId, current.id, {
        ...params,
        expected_revision: current.revision,
      })
      announce('Page withdrawn from the buyer view. The draft and its history are kept.')
      reload()
    })

  if (room.loading || catalogue.loading) return <p className="text-sm text-muted-foreground">Loading the page editor…</p>
  if (room.error) return <ErrorNote error={room.error} onRetry={room.refetch} />
  if (catalogue.error) return <ErrorNote error={catalogue.error} onRetry={catalogue.refetch} />

  const gated = room.data.permission_source === 'room'
  // A 403 is the documented Room Collaborator requirement, so it is rendered as
  // the state it is rather than as a failure with a raw message.
  const refused = actionError && isPermissionDenied(actionError)

  return (
    <div className="space-y-5">
      <p role="status" aria-live="polite" className="sr-only">
        {status}
      </p>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Build the buyer-facing pages from fragments, then publish. An edit stays a draft until
          you press Publish.
        </p>
        <div className="w-full sm:w-64">
          <Field
            label="Acting as"
            id="actor"
            hint={
              gated
                ? 'Must be a Room Collaborator on this room.'
                : 'This room grants no one yet, so any name works.'
            }
          >
            <input
              id="actor"
              className={inputClass}
              placeholder="dana"
              value={actor}
              onChange={(event) => setActor(event.target.value)}
            />
          </Field>
        </div>
      </div>

      {gated && !actor && (
        <Note tone="warn" title="This room restricts page editing">
          Editing a room&apos;s pages requires the Room Collaborator role. Enter who you are above
          to make changes.
        </Note>
      )}
      {refused ? (
        <Note tone="warn" title="Not a Room Collaborator on this room">
          {actionError.message}
        </Note>
      ) : (
        actionError && <ErrorNote error={actionError} />
      )}

      <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)_20rem]">
        {/* -- left: the page list ---------------------------------------- */}
        <div className="space-y-4">
          <PageList
            pages={pages}
            selectedId={current?.id}
            onSelect={onSelectPage}
            creating={busy === 'create'}
            onCreate={() => setNewPageOpen((open) => !open)}
          />
          {newPageOpen && (
            <Card className="space-y-3">
              <Field label="New page title" id="new-page-title">
                <input
                  id="new-page-title"
                  className={inputClass}
                  value={newPageTitle}
                  onChange={(event) => setNewPageTitle(event.target.value)}
                />
              </Field>
              <div className="flex gap-2">
                <Button variant="primary" icon="check" onClick={createPage} disabled={busy === 'create'}>
                  Create
                </Button>
                <Button onClick={() => setNewPageOpen(false)}>Cancel</Button>
              </div>
            </Card>
          )}
        </div>

        {/* -- middle: the canvas ------------------------------------------ */}
        <div className="min-w-0 space-y-4">
          {!current ? (
            <EmptyState
              title="No page selected"
              description="Create a page to start placing fragments on it."
            />
          ) : (
            <>
              <Card className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={current.published ? 'insert' : 'neutral'}>
                      {current.published ? 'published' : 'draft'}
                    </Badge>
                    {current.has_unpublished_changes && (
                      <Badge tone="restore">unpublished changes</Badge>
                    )}
                    <span className="font-mono text-xs text-muted-foreground">
                      rev {current.revision}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button icon="eye" onClick={() => onOpenBuyerView(current.data.slug)}>
                      Buyer view
                    </Button>
                    {current.published ? (
                      <Button icon="lock" onClick={unpublish} disabled={busy === 'unpublish'}>
                        Unpublish
                      </Button>
                    ) : null}
                    <Button
                      icon="publish"
                      variant="primary"
                      onClick={publish}
                      disabled={busy === 'publish'}
                    >
                      {busy === 'publish' ? 'Publishing…' : 'Publish'}
                    </Button>
                  </div>
                </div>

                {current.has_unpublished_changes ? (
                  <Note tone="warn" title="Buyers are not seeing these changes">
                    The published revision is unchanged. Press Publish when the draft is ready.
                  </Note>
                ) : current.published ? (
                  <Note tone="good" title="Published">
                    Buyers see this revision from {absoluteTime(current.published_at)}
                    {current.published_by ? `, published by ${current.published_by}` : ''}.
                  </Note>
                ) : (
                  <Note tone="info" title="Draft only">
                    Nothing here is visible to a buyer until you press Publish.
                  </Note>
                )}
              </Card>

              <PageCanvas
                page={current}
                fragmentsByKey={fragmentsByKey}
                room={{ id: roomId, data: { name: room.data.room_name } }}
                selectedId={selectedBlockId}
                onSelect={setSelectedBlockId}
                onMove={moveBlock}
                onRemove={removeBlock}
                onDropFragment={addFragment}
                onTitleChange={saveTitle}
                savingTitle={busy === 'title'}
              />
            </>
          )}
        </div>

        {/* -- right: palette and configuration ---------------------------- */}
        <div className="min-w-0 space-y-4">
          <Card>
            <h2 className="mb-2 font-mono text-sm font-semibold text-foreground">
              Components → Fragments
            </h2>
            <FragmentPalette
              catalogue={catalogue.data}
              onAdd={addFragment}
              disabled={!current || busy === 'add'}
              adding={busy}
            />
          </Card>

          <Card>
            <h2 className="mb-2 font-mono text-sm font-semibold text-foreground">Configuration</h2>
            <ConfigPanel
              block={block}
              fragment={fragment}
              documents={documents.data?.records || []}
              onChange={changeConfig}
              saving={busy === 'config'}
              error={busy === 'config' ? actionError : null}
            />
          </Card>
        </div>
      </div>
    </div>
  )
}
