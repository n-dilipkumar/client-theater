/**
 * Revoke access early, and keep the row that proves it (WF-076).
 *
 * Five sections, in the order the researched user flow happens in them.
 *
 * **Share links** is the surface a rep starts from. Each row says whether the
 * public URL still resolves and whether the slug is free to reissue, and a live
 * link has a **Revoke** button. The result panel below the list shows the
 * retained row and its audit trail the moment a revoke lands - the researched
 * promise is not "the URL is dead" but "the URL is dead *and here is the record*",
 * so both halves are on screen together.
 *
 * **Audiences** covers the three ways to cut someone off without touching a link:
 * remove one membership (the underlying viewer is kept), flip an item's view /
 * download flags off, or delete the whole group (irreversible, cascading, and one
 * transaction). Each destructive control routes through a confirmation that
 * echoes the id it destroys, because the backend refuses anything else.
 *
 * **Documents** is the attach / detach pair. A frozen dataroom refuses both, and
 * the page says so from `/state` rather than letting a click discover it.
 *
 * **Slug resolver** is the guarantee as a question you can ask: does this URL
 * still work, for this requester, right now. It answers `revoked`,
 * `group_deleted`, `dataroom_deleted`, `not_a_member` or `not_found` - and the
 * distinction between `revoked` and `not_found` exists only because the deleted
 * row survives.
 *
 * **Audit trail** is every row this feature's own routes wrote in this room,
 * read from the core audit log. A cascade writes exactly one row there, and that
 * row is the only place the full before-state of everything it removed lives.
 *
 * **What this infers** is the research's own gaps made arguable. Every reading
 * comes from `/inferences`, never from a list compiled into this file, so a
 * reviewer who disagrees can change one constant rather than rewrite a page.
 */

import { useMemo, useState } from 'react'
import { absoluteTime, api, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  JsonView,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'
import { revocationApi } from './api'
import Glyphs from './icons'
import { ConfirmPanel, Fact, Facts, Note } from './primitives'

const SECTIONS = [
  { id: 'links', label: 'Share links', glyph: 'revoke' },
  { id: 'audiences', label: 'Audiences', glyph: 'group' },
  { id: 'documents', label: 'Documents', glyph: 'attach' },
  { id: 'resolver', label: 'Slug resolver', glyph: 'resolve' },
  { id: 'trail', label: 'Audit trail', glyph: 'retained' },
  { id: 'inferences', label: 'What this infers', glyph: 'inference' },
]

const CAUSE_LABEL = {
  revoked: 'Revoked directly',
  cascade_group: 'Swept up by a group delete',
  cascade_room: 'Swept up by a dataroom purge',
}

function Stat({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] tracking-[0.14em] text-muted-foreground uppercase">{label}</p>
          <p className="mt-2 font-mono text-3xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-1 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className="rounded-sm bg-muted p-2 text-accent">
          <Icon path={glyph} size={20} />
        </span>
      </div>
    </Card>
  )
}

/** One share link: whether it resolves, whether the slug is free, and the revoke. */
function LinkRow({ link, busy, onRevoke }) {
  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <div className="flex min-h-11 flex-wrap items-center gap-3 py-2">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-foreground">
            {link.label || link.slug}
          </span>
          <span className="block font-mono text-xs break-all text-muted-foreground">
            {link.slug}
            {link.original_slug ? ` (was ${link.original_slug})` : ''}
          </span>
        </span>
        {link.custom_domain && <Badge tone="update">custom domain</Badge>}
        {link.gated_on_membership && <Badge tone="neutral">group link</Badge>}
        <Badge tone={link.live ? 'insert' : 'delete'}>
          {link.live ? 'resolves' : 'revoked'}
        </Badge>
        <span
          className="shrink-0 font-mono text-xs text-muted-foreground"
          title={absoluteTime(link.revoked_at || link.issued_at)}
        >
          {relativeTime(link.revoked_at || link.issued_at)}
        </span>
        {link.live ? (
          <Button variant="danger" disabled={busy} onClick={() => onRevoke(link)}>
            <Icon path={Glyphs.revoke} />
            Revoke
          </Button>
        ) : (
          <span className="font-mono text-xs text-muted-foreground">{link.revoked_by}</span>
        )}
      </div>
      {!link.live && link.revocation_reason && (
        <p className="pb-2 text-xs text-muted-foreground">{link.revocation_reason}</p>
      )}
    </li>
  )
}

/** The retained row and the audit trail written for it. */
function RetainedPanel({ retained }) {
  if (!retained) return null
  const data = retained.data || {}
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-mono text-base font-semibold">The row that survived</h3>
        <Badge tone="delete">soft-deleted</Badge>
        <Badge tone="neutral">kept for audit</Badge>
      </div>
      <Note tone="success" title="The URL stopped resolving; this row did not.">
        Revoked at {absoluteTime(data.revoked_at)} by{' '}
        <span className="font-mono">{data.revoked_by || 'unknown'}</span>
        {data.revocation_reason ? ` — ${data.revocation_reason}` : ''}.{' '}
        {CAUSE_LABEL[data.revoked_via] || data.revoked_via}.
      </Note>
      <Facts columns={4}>
        <Fact label="Collection" value={retained.collection} mono />
        <Fact label="Revision" value={retained.revision} mono />
        <Fact label="Slug it held" value={data.original_slug || data.slug} mono />
        <Fact label="Slug now" value={data.slug} mono />
        <Fact label="Deleted at" value={absoluteTime(retained.deleted_at)} mono />
        <Fact label="Grace period" value={`${data.grace_period_minutes} min`} mono />
        <Fact label="Cached copies" value={data.cached_copy_recall} mono />
        <Fact label="Reversible" value={data.reversible === false ? 'no' : 'yes'} mono />
      </Facts>
      <div>
        <p className="mb-1 text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
          Audit trail, same transaction as the change
        </p>
        <ul className="space-y-1">
          {(retained.audit || []).map((entry, index) => (
            <li key={index} className="flex flex-wrap items-center gap-2 text-[13px]">
              <Badge tone={entry.action === 'delete' ? 'delete' : 'update'}>{entry.action}</Badge>
              <span className="font-mono text-xs text-muted-foreground">{entry.source}</span>
              <span className="font-mono text-xs text-muted-foreground" title={entry.ts}>
                {relativeTime(entry.ts)}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
          The row as stored
        </p>
        <JsonView value={data} />
      </div>
    </Card>
  )
}

/** One audience: its buyers, its per-item flags, and the irreversible delete. */
function GroupCard({ group, busy, onRemoveMember, onTogglePermission, onDeleteGroup }) {
  const members = group.members || []
  const permissions = group.permissions || []
  const links = group.links || []
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-mono text-base font-semibold">{group.data?.name}</h3>
        <Badge tone="neutral">{members.length} members</Badge>
        <Badge tone="neutral">{links.length} links</Badge>
        <Badge tone="neutral">{permissions.length} items</Badge>
        <span className="font-mono text-xs text-muted-foreground">{group.id}</span>
      </div>

      {members.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nobody is in this audience.</p>
      ) : (
        <ul>
          {members.map((member) => (
            <li
              key={member.id}
              className="flex min-h-11 flex-wrap items-center gap-3 border-b border-border-subtle/15 py-2 last:border-0"
            >
              <Icon path={Glyphs.viewer} size={14} />
              <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
                {member.data?.viewer_email}
              </span>
              <Button variant="danger" disabled={busy} onClick={() => onRemoveMember(group, member)}>
                <Icon path={Glyphs.revoke} />
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}

      {permissions.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] tracking-[0.14em] text-muted-foreground uppercase">
            Per-item access
          </p>
          <ul>
            {permissions.map((permission) => {
              const view = permission.data?.view !== false
              return (
                <li
                  key={permission.id}
                  className="flex min-h-11 flex-wrap items-center gap-3 border-b border-border-subtle/15 py-2 last:border-0"
                >
                  <Icon path={Glyphs.permission} size={14} />
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
                    {permission.data?.document_id}
                  </span>
                  <Badge tone={view ? 'insert' : 'delete'}>
                    view {view ? 'on' : 'off'}
                  </Badge>
                  <Badge tone={permission.data?.download ? 'insert' : 'neutral'}>
                    download {permission.data?.download ? 'on' : 'off'}
                  </Badge>
                  <Button
                    disabled={busy}
                    onClick={() => onTogglePermission(group, permission.data?.document_id, view)}
                  >
                    {view ? 'Hide from this audience' : 'Show again'}
                  </Button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <ConfirmPanel
        glyph={Glyphs.group}
        label="Delete this audience, its memberships, its permissions and every link pointing at it."
        target={group.id}
        busy={busy}
        onConfirm={() => onDeleteGroup(group)}
      />
    </Card>
  )
}

/** One reading: what the research says, what this build did, and how to change it. */
function InferenceRow({ entry }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-border-subtle/15 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-3 py-2 text-left transition-colors duration-150 hover:bg-muted/40"
      >
        <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">{entry.id}</span>
        <Badge tone="neutral">inferred</Badge>
      </button>
      {open && (
        <div className="space-y-2 rounded-sm border border-border-subtle/25 bg-background/40 p-3 text-xs">
          <div>
            <p className="tracking-[0.14em] text-muted-foreground uppercase">What the research says</p>
            <p className="mt-0.5 text-foreground/90">{entry.claim}</p>
          </div>
          <div>
            <p className="tracking-[0.14em] text-muted-foreground uppercase">This build chose</p>
            <p className="mt-0.5 text-foreground/90">{entry.reading}</p>
          </div>
          <div>
            <p className="tracking-[0.14em] text-muted-foreground uppercase">Why</p>
            <p className="mt-0.5 text-foreground/90">{entry.basis}</p>
          </div>
          <div>
            <p className="tracking-[0.14em] text-muted-foreground uppercase">How to change it</p>
            <p className="mt-0.5 font-mono text-foreground/90">{entry.change_it}</p>
          </div>
          <div>
            <p className="tracking-[0.14em] text-muted-foreground uppercase">Affects</p>
            <p className="mt-0.5 text-foreground/90">{entry.blast_radius}</p>
          </div>
        </div>
      )}
    </li>
  )
}

export default function RevocationPage() {
  const [section, setSection] = useState('links')
  const [chosenRoomId, setChosenRoomId] = useState('')
  const [notice, setNotice] = useState(null)
  const [noticeError, setNoticeError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [retained, setRetained] = useState(null)

  const [reason, setReason] = useState('')
  const [slug, setSlug] = useState('')
  const [viewer, setViewer] = useState('')
  const [answer, setAnswer] = useState(null)

  const [documentId, setDocumentId] = useState('')
  const [documentTeam, setDocumentTeam] = useState('')

  const rooms = useAsync(() => api.listRecords('room', { limit: 100 }), [])
  const vocabulary = useAsync(() => revocationApi.vocabulary(), [])
  const inferences = useAsync(() => revocationApi.inferences(), [])

  // The core `api.listRecords` answers with a `{records: [...]}` envelope, so the
  // list is read off that key rather than off the response itself - the single
  // easiest mistake to make against a schema-flexible API, and one that renders
  // as a crash in an effect rather than as an obvious empty page.
  const roomOptions = useMemo(() => rooms.data?.records || [], [rooms.data])
  const roomId = chosenRoomId || roomOptions[0]?.id || ''

  const state = useAsync(
    () => (roomId ? revocationApi.roomState(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const links = useAsync(
    () => (roomId ? revocationApi.listLinks(roomId, { include_revoked: true }) : Promise.resolve(null)),
    [roomId],
  )
  const groups = useAsync(
    () => (roomId ? revocationApi.listGroups(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const viewers = useAsync(
    () => (roomId ? revocationApi.listViewers(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const documents = useAsync(
    () => (roomId ? revocationApi.listDocuments(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const trail = useAsync(
    () => (roomId ? revocationApi.trail(roomId, { limit: 100 }) : Promise.resolve(null)),
    [roomId],
  )

  const frozen = state.data?.frozen === true
  const linkRows = links.data?.links || []
  const groupRows = groups.data?.groups || []

  function refreshAll() {
    state.refetch()
    links.refetch()
    groups.refetch()
    viewers.refetch()
    documents.refetch()
    trail.refetch()
  }

  async function run(action, successMessage) {
    setBusy(true)
    setNoticeError(null)
    setNotice(null)
    try {
      const result = await action()
      setNotice(successMessage(result))
      refreshAll()
      return result
    } catch (error) {
      setNoticeError(error)
      return null
    } finally {
      setBusy(false)
    }
  }

  async function onRevoke(link) {
    const result = await run(
      () => revocationApi.revokeLink(roomId, link.id, { reason }),
      () => `Revoked ${link.slug}. The row is kept for audit.`,
    )
    if (result) {
      setRetained(result.retained)
      setReason('')
    }
  }

  async function onRemoveMember(group, member) {
    const result = await run(
      () => revocationApi.removeMember(roomId, group.id, member.id),
      () =>
        `Removed ${member.data?.viewer_email} from ${group.data?.name}. The viewer record is kept.`,
    )
    if (result) setRetained(null)
  }

  async function onTogglePermission(group, targetDocumentId, currentlyVisible) {
    await run(
      () =>
        revocationApi.setPermissions(roomId, group.id, {
          document_id: targetDocumentId,
          view: !currentlyVisible,
          download: !currentlyVisible,
        }),
      () =>
        `${currentlyVisible ? 'Hid' : 'Restored'} ${targetDocumentId} for ${group.data?.name}.`,
    )
  }

  async function onDeleteGroup(group) {
    const result = await run(
      () => revocationApi.deleteGroup(roomId, group.id, group.id),
      () =>
        `Deleted ${group.data?.name}: ${result?.removed ?? 0} records removed, every row kept.`,
    )
    if (result) setRetained(null)
  }

  async function onDetach(row) {
    await run(
      () => revocationApi.detachDocument(roomId, row.data?.document_id),
      () =>
        `Detached ${row.data?.document_id}. The team-library document and its other attachments are intact.`,
    )
  }

  async function onAttach(event) {
    event.preventDefault()
    await run(
      () =>
        revocationApi.attachDocument(roomId, {
          document_id: documentId,
          title: documentId,
          document_team: documentTeam || undefined,
        }),
      () => `Attached ${documentId} to this dataroom.`,
    )
    setDocumentId('')
    setDocumentTeam('')
  }

  async function onResolve(event) {
    event.preventDefault()
    setNoticeError(null)
    try {
      setAnswer(await revocationApi.resolve(roomId, { slug, viewer }))
    } catch (error) {
      setAnswer(null)
      setNoticeError(error)
    }
  }

  async function onInspect(row) {
    setNoticeError(null)
    try {
      setRetained(await revocationApi.retained(roomId, row.id))
    } catch (error) {
      setNoticeError(error)
    }
  }

  const counts = state.data?.counts || {}
  const stats = [
    {
      label: 'Live links',
      value: counts.links ?? 0,
      hint: `${counts.links_revoked ?? 0} revoked, rows kept`,
      glyph: Glyphs.revoke,
    },
    {
      label: 'Audiences',
      value: counts.groups ?? 0,
      hint: `${counts.members ?? 0} memberships`,
      glyph: Glyphs.group,
    },
    {
      label: 'Viewers kept',
      value: counts.viewers ?? 0,
      hint: 'a removed membership keeps its viewer',
      glyph: Glyphs.viewer,
    },
    {
      label: 'Grace period',
      value: `${vocabulary.data?.guarantee?.grace_period_minutes ?? 0} min`,
      hint: vocabulary.data?.guarantee?.cached_copy_recall || 'no cached-copy recall',
      glyph: Glyphs.clock,
    },
  ]

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Revoke access early</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Cut a share link, one buyer, or a whole dataroom&rsquo;s access — and keep every
          revoked row. Revocation is request-time: the public URL stops resolving on the next
          request, with no grace period, no cached-copy recall, and no way to undo it.
        </p>
      </header>

      {vocabulary.data && (
        <Note tone="info" title="The guarantee this page enforces">
          <span className="italic">{vocabulary.data.guarantee.quote}</span>
        </Note>
      )}

      {noticeError && (
        <ErrorNote
          error={noticeError}
          onRetry={() => {
            setNoticeError(null)
          }}
        />
      )}
      {notice && <Note tone="success">{notice}</Note>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => (
          <Stat key={stat.label} {...stat} />
        ))}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Room" id="revocation-room" hint="Revocation is always room-scoped.">
          <select
            id="revocation-room"
            className={inputClass}
            value={roomId}
            onChange={(event) => {
              setChosenRoomId(event.target.value)
              setRetained(null)
              setAnswer(null)
            }}
          >
            <option value="">Choose a room</option>
            {roomOptions.map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </select>
        </Field>
        {state.data && (
          <div className="flex flex-wrap items-center gap-2 pb-1">
            {state.data.frozen ? (
              <Badge tone="delete">
                <Icon path={Glyphs.frozen} size={13} /> frozen
              </Badge>
            ) : (
              <Badge tone="insert">open</Badge>
            )}
            <span className="font-mono text-xs text-muted-foreground">
              {state.data.team || 'no team on file'}
            </span>
          </div>
        )}
      </div>

      <div role="tablist" aria-label="Revocation sections" className="flex flex-wrap gap-2">
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={section === item.id}
            aria-controls={`panel-${item.id}`}
            onClick={() => setSection(item.id)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-sm px-4 text-sm transition-colors duration-150 ${
              section === item.id
                ? 'bg-accent/15 font-medium text-accent'
                : 'bg-muted text-muted-foreground hover:border-border-subtle hover:text-foreground'
            }`}
          >
            <Icon path={Glyphs[item.glyph]} size={16} />
            {item.label}
          </button>
        ))}
      </div>

      {/* -- share links -- */}
      {section === 'links' && (
        <div id="panel-links" role="tabpanel" aria-labelledby="tab-links" className="space-y-4">
          <div>
            <h2 className="font-mono text-lg font-semibold">Share links</h2>
            <p className="text-sm text-muted-foreground">
              A revoke cuts the public URL on the buyer&rsquo;s next request and keeps the row.
              A link on a custom domain also releases its slug, so the original can be reused.
            </p>
          </div>

          {links.loading && <Spinner label="Loading share links" />}
          {links.error && <ErrorNote error={links.error} onRetry={links.refetch} />}

          {!links.loading && !links.error && linkRows.length === 0 && (
            <EmptyState
              title="No share links yet"
              description="Issue one from the API and it will appear here with a revoke action."
            />
          )}

          {linkRows.length > 0 && (
            <Card className="p-4">
              <ul>
                {linkRows.map((link) => (
                  <LinkRow key={link.id} link={link} busy={busy} onRevoke={onRevoke} />
                ))}
              </ul>
            </Card>
          )}

          {linkRows.some((link) => !link.live) && (
            <Card className="space-y-2">
              <h3 className="font-mono text-base font-semibold">Inspect a retained row</h3>
              <p className="text-sm text-muted-foreground">
                Every revoked link is still readable. Open one to see its row and the audit trail
                written in the same transaction as the cut.
              </p>
              <ul className="flex flex-wrap gap-2">
                {linkRows
                  .filter((link) => !link.live)
                  .map((link) => (
                    <li key={link.id}>
                      <Button onClick={() => onInspect(link)}>
                        <Icon path={Glyphs.retained} />
                        {link.original_slug || link.slug}
                      </Button>
                    </li>
                  ))}
              </ul>
            </Card>
          )}

          <RetainedPanel retained={retained} />

          <Card className="space-y-2">
            <h3 className="font-mono text-base font-semibold">Reason for the next revoke</h3>
            <Field
              label="Reason"
              id="revocation-reason"
              hint="Stored on the row and in the audit entry. Never on the URL."
            >
              <input
                id="revocation-reason"
                className={inputClass}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Deal went cold."
              />
            </Field>
          </Card>
        </div>
      )}

      {/* -- audiences -- */}
      {section === 'audiences' && (
        <div
          id="panel-audiences"
          role="tabpanel"
          aria-labelledby="tab-audiences"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">Audiences</h2>
            <p className="text-sm text-muted-foreground">
              Three ways to cut someone off without touching a link: remove one membership, hide an
              item from the audience, or delete the audience and everything pointing at it.
            </p>
          </div>

          {groups.loading && <Spinner label="Loading audiences" />}
          {groups.error && <ErrorNote error={groups.error} onRetry={groups.refetch} />}

          {!groups.loading && !groups.error && groupRows.length === 0 && (
            <EmptyState
              title="No audiences yet"
              description="A group is a set of buyers a single revocation can cut off together."
            />
          )}

          {groupRows.map((group) => (
            <GroupCard
              key={group.id}
              group={group}
              busy={busy}
              onRemoveMember={onRemoveMember}
              onTogglePermission={onTogglePermission}
              onDeleteGroup={onDeleteGroup}
            />
          ))}

          <Card className="space-y-2">
            <h3 className="font-mono text-base font-semibold">The viewers underneath</h3>
            <p className="text-sm text-muted-foreground">
              Removing a membership leaves the viewer record alone — &ldquo;the underlying viewer
              is kept&rdquo;. These rows are what proves it.
            </p>
            {viewers.loading && <Spinner label="Loading viewers" />}
            {viewers.error && <ErrorNote error={viewers.error} onRetry={viewers.refetch} />}
            {(viewers.data?.viewers || []).length > 0 && (
              <ul>
                {(viewers.data?.viewers || []).map((viewer) => (
                  <li
                    key={viewer.id}
                    className="flex min-h-11 flex-wrap items-center gap-3 border-b border-border-subtle/15 py-2 last:border-0"
                  >
                    <Icon path={Glyphs.viewer} size={14} />
                    <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
                      {viewer.data?.email}
                    </span>
                    <Badge tone={viewer.in_any_group ? 'insert' : 'neutral'}>
                      {viewer.memberships} memberships
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}

      {/* -- documents -- */}
      {section === 'documents' && (
        <div
          id="panel-documents"
          role="tabpanel"
          aria-labelledby="tab-documents"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">Documents in this dataroom</h2>
            <p className="text-sm text-muted-foreground">
              Detaching is a join-row delete: the team-library document and its attachments to other
              datarooms are left intact. A frozen dataroom refuses both attach and detach.
            </p>
          </div>

          {frozen && (
            <Note tone="warning" title="This dataroom is frozen">
              Frozen datarooms refuse new attachments, detaches and moves. Revocation is not in that
              list, so you can still revoke a link, remove a member or delete an audience here.
            </Note>
          )}

          {documents.loading && <Spinner label="Loading documents" />}
          {documents.error && <ErrorNote error={documents.error} onRetry={documents.refetch} />}

          {!documents.loading && !documents.error && (documents.data?.documents || []).length === 0 && (
            <EmptyState
              title="Nothing attached yet"
              description="Attach a team-library document below and it will appear in this list."
            />
          )}

          {(documents.data?.documents || []).length > 0 && (
            <Card className="p-4">
              <ul>
                {(documents.data?.documents || []).map((row) => (
                  <li
                    key={row.id}
                    className="flex min-h-11 flex-wrap items-center gap-3 border-b border-border-subtle/15 py-2 last:border-0"
                  >
                    <Icon path={Glyphs.attach} size={14} />
                    <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
                      {row.data?.title || row.data?.document_id}
                    </span>
                    {row.data?.document_team && (
                      <Badge tone={row.data.document_team === state.data?.team ? 'neutral' : 'delete'}>
                        team {row.data.document_team}
                      </Badge>
                    )}
                    <Badge tone={row.attached ? 'insert' : 'delete'}>
                      {row.attached ? 'attached' : 'detached'}
                    </Badge>
                    {row.attached ? (
                      <Button variant="danger" disabled={busy} onClick={() => onDetach(row)}>
                        <Icon path={Glyphs.revoke} />
                        Detach
                      </Button>
                    ) : (
                      <span className="font-mono text-xs text-muted-foreground">
                        {row.data?.document_id}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card>
            <form className="space-y-3" onSubmit={onAttach}>
              <h3 className="font-mono text-base font-semibold">Attach a document</h3>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Document id or title" id="attach-document">
                  <input
                    id="attach-document"
                    className={inputClass}
                    required
                    value={documentId}
                    onChange={(event) => setDocumentId(event.target.value)}
                  />
                </Field>
                <Field
                  label="Document team"
                  id="attach-team"
                  hint="Leave blank when the document carries no team of its own. A different team is refused."
                >
                  <input
                    id="attach-team"
                    className={inputClass}
                    value={documentTeam}
                    onChange={(event) => setDocumentTeam(event.target.value)}
                  />
                </Field>
              </div>
              <Button type="submit" disabled={busy || frozen} icon="refresh">
                {frozen ? 'Refused while frozen' : 'Attach'}
              </Button>
            </form>
          </Card>
        </div>
      )}

      {/* -- slug resolver -- */}
      {section === 'resolver' && (
        <div
          id="panel-resolver"
          role="tabpanel"
          aria-labelledby="tab-resolver"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">Slug resolver</h2>
            <p className="text-sm text-muted-foreground">
              The researched guarantee as a question you can ask: does this public URL still work,
              for this requester, right now. No grace period and no cache, so the answer cannot
              disagree with the database by more than one request.
            </p>
          </div>

          <Card>
            <form className="space-y-3" onSubmit={onResolve}>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Slug" id="resolve-slug" hint="The public slug the buyer is holding.">
                  <input
                    id="resolve-slug"
                    className={inputClass}
                    required
                    value={slug}
                    onChange={(event) => setSlug(event.target.value)}
                  />
                </Field>
                <Field
                  label="Viewer"
                  id="resolve-viewer"
                  hint="Only a live member of the audience gets a group link."
                >
                  <input
                    id="resolve-viewer"
                    className={inputClass}
                    value={viewer}
                    onChange={(event) => setViewer(event.target.value)}
                  />
                </Field>
              </div>
              <Button type="submit" disabled={!roomId}>
                <Icon path={Glyphs.resolve} />
                Resolve now
              </Button>
            </form>
          </Card>

          {answer && (
            <Card className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <h3 className="font-mono text-base font-semibold">The answer</h3>
                <Badge tone={answer.resolves ? 'insert' : 'delete'}>{answer.reason}</Badge>
              </div>
              <Note
                tone={answer.resolves ? 'success' : 'warning'}
                title={
                  answer.resolves
                    ? 'This URL resolves right now.'
                    : 'This URL does not resolve, and the reason is on record.'
                }
              >
                {answer.resolves
                  ? 'Request-time only: revoke it and the next request gets the answer above.'
                  : `Withheld by ${answer.withheld_by || 'nobody — the slug was never issued'}. The row that held it is still in the database.`}
              </Note>
              <Facts columns={4}>
                <Fact label="Room" value={answer.room_id} mono />
                <Fact label="Slug" value={answer.slug} mono />
                <Fact label="Viewer" value={answer.viewer} mono />
                <Fact label="Checked" value={absoluteTime(answer.checked_at)} mono />
                <Fact label="Link id" value={answer.link_id} mono />
                <Fact label="Target" value={answer.target} mono />
                <Fact label="Grace period" value={`${answer.grace_period_minutes} min`} mono />
                <Fact label="Cached copies" value={answer.cached_copy_recall} mono />
              </Facts>
            </Card>
          )}
        </div>
      )}

      {/* -- audit trail -- */}
      {section === 'trail' && (
        <div
          id="panel-trail"
          role="tabpanel"
          aria-labelledby="tab-trail"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">Audit trail</h2>
            <p className="text-sm text-muted-foreground">
              Every row this feature&rsquo;s own routes wrote in this room, read from the core audit
              log. A cascade writes exactly one row there, and that row is the only place the full
              before-state of everything it removed lives.
            </p>
          </div>

          {trail.loading && <Spinner label="Loading the audit trail" />}
          {trail.error && <ErrorNote error={trail.error} onRetry={trail.refetch} />}

          {!trail.loading && !trail.error && (trail.data?.trail || []).length === 0 && (
            <EmptyState
              title="Nothing recorded yet"
              description="Revoke a link or delete an audience and the row appears here immediately."
            />
          )}

          {(trail.data?.trail || []).length > 0 && (
            <Card className="p-4">
              <ul>
                {(trail.data?.trail || []).map((entry) => (
                  <li
                    key={entry.seq}
                    className="flex min-h-11 flex-wrap items-center gap-3 border-b border-border-subtle/15 py-2 last:border-0"
                  >
                    <Badge tone={entry.action === 'delete' ? 'delete' : 'update'}>
                      {entry.action}
                    </Badge>
                    {entry.counts ? (
                      <span className="font-mono text-[13px] text-foreground">
                        {entry.counts} records
                      </span>
                    ) : (
                      <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-foreground">
                        {entry.summary}
                      </span>
                    )}
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
                      {entry.source}
                    </span>
                    <span
                      className="shrink-0 font-mono text-xs text-muted-foreground"
                      title={absoluteTime(entry.ts)}
                    >
                      {relativeTime(entry.ts)}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      {/* -- what this infers -- */}
      {section === 'inferences' && (
        <div
          id="panel-inferences"
          role="tabpanel"
          aria-labelledby="tab-inferences"
          className="space-y-4"
        >
          <div>
            <h2 className="font-mono text-lg font-semibold">What this infers</h2>
            <p className="text-sm text-muted-foreground">
              The six operations and the guarantee are sourced. The rest is judgement: the parts of
              this workflow the research makes no claims about. Each reading is listed with the
              reason for it and the single lever that would change it.
            </p>
          </div>

          {inferences.loading && <Spinner label="Loading inferences" />}
          {inferences.error && <ErrorNote error={inferences.error} onRetry={inferences.refetch} />}

          {inferences.data && (
            <>
              <Card className="p-4">
                <ul>
                  {(inferences.data.inferences || []).map((entry) => (
                    <InferenceRow key={entry.id} entry={entry} />
                  ))}
                </ul>
              </Card>
              <JsonView value={inferences.data.rule} />
            </>
          )}
        </div>
      )}
    </div>
  )
}