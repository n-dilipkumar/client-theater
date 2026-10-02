import { useMemo, useState } from 'react'

import { apiRequest } from '@/lib/api'
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

import { rolesApi } from './api'
import { Action, Notice } from './primitives'
import ShareDialog from './ShareDialog'

/**
 * WF-004: invite buyers to a room with a role and an access expiry.
 *
 * Ported from `feature/WF-004-invite-buyers-to-a-room-with-a-role`. The branch
 * put this workflow's UI inside `frontend/src/pages/Rooms.jsx`: a Share button on
 * each room card, a *Viewing as* control in the header, and the Share dialog
 * itself living in the shared `components/` directory. `Rooms.jsx` is a shared
 * file - WF-001 edits it too - and a feature must not own a file another feature
 * owns, so none of that could be carried. The same surface is therefore this
 * feature's own page, which is also how it reaches the nav: `App.jsx` builds its
 * list from the discovered features and never learns this name.
 *
 * Two things this page is careful about, because both are easy to get wrong:
 *
 * 1. **Whether Share is offered is the server's answer, not this page's.** The
 *    research says a Viewer has no Share button. The capability comes from the
 *    same snapshot the dialog reads, so the button and the dialog can never
 *    disagree, and hiding it is a courtesy rather than the enforcement - every
 *    refusal is also enforced server-side and returns 403.
 * 2. **There is no authentication, so the acting identity is explicit.** A
 *    *Viewing as* control names who is asking, because the delegation rule
 *    depends on it. That is a demo affordance, not a security model: a real
 *    deployment resolves identity from a session. Nothing is granted by the
 *    control.
 */

export default function InviteBuyer() {
  const rooms = useAsync(() => apiRequest('/records/room?limit=100'), [])
  const [actor, setActor] = useState(null)
  const [sharing, setSharing] = useState(null)

  // Memoised so the `identities` below keeps a stable dependency. A fresh `[]`
  // each render made every dep change on every render, so the memo never held.
  const records = useMemo(() => rooms.data?.records || [], [rooms.data])

  // The room owners, plus the two demo identities the core seed uses, so the
  // delegation rule can be tried from both sides: the owner may hand out Room
  // Collaborator and a Viewer may not even open the dialog.
  const identities = useMemo(() => {
    const owners = records.map((room) => room.data?.owner).filter(Boolean)
    const seen = new Set()
    return [...owners, ...['dana', 'sam']].filter((name) => name && !seen.has(name) && seen.add(name))
  }, [records])

  const effectiveActor = actor || identities[0] || null

  // One snapshot per room, so each card knows whether to offer Share at all.
  // A room that cannot be read is treated as unshareable rather than failing the
  // page: one bad room should not cost the seller every other room.
  const capabilities = useAsync(async () => {
    if (!records.length || !effectiveActor) return {}
    const pairs = await Promise.all(
      records.map(async (room) => {
        try {
          const snapshot = await rolesApi.snapshot(room.id, effectiveActor)
          return [room.id, snapshot]
        } catch {
          return [room.id, null]
        }
      }),
    )
    return Object.fromEntries(pairs)
  }, [records.map((room) => room.id).join(','), effectiveActor])

  const shareable = records.filter((room) => capabilities.data?.[room.id]?.actor?.can_share)
  const totalMembers = Object.values(capabilities.data || {}).reduce(
    (sum, snapshot) => sum + (snapshot?.members?.length || 0),
    0,
  )
  const totalPending = Object.values(capabilities.data || {}).reduce(
    (sum, snapshot) => sum + (snapshot?.pending_invitations?.length || 0),
    0,
  )
  const totalExpiring = Object.values(capabilities.data || {}).reduce(
    (sum, snapshot) => sum + (snapshot?.expiring_soon_count || 0),
    0,
  )

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-mono text-xl font-semibold">Invite buyers</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Share a room by email. One role and one access-expiry date apply to the whole
          invitation, an invitation must be accepted within 48 hours, and access ends at the
          end of the expiration date in UTC. Every change is written through the audited
          store, so it appears in the audit log as it happens.
        </p>
      </header>

      {rooms.loading && <Spinner label="Loading rooms" />}
      {rooms.error && <ErrorNote error={rooms.error} onRetry={rooms.refetch} />}

      {rooms.data && (
        <>
          <Card>
            <div className="flex flex-wrap items-end justify-between gap-4">
              <Field
                label="Viewing as"
                id="wf004-actor"
                hint="Decides what this person may do. The server resolves the role and refuses anything it does not permit."
              >
                <select
                  id="wf004-actor"
                  className={`${inputClass} min-w-56`}
                  value={effectiveActor || ''}
                  onChange={(event) => setActor(event.target.value)}
                >
                  {identities.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </Field>

              <dl className="flex flex-wrap gap-6 text-sm">
                <div>
                  <dt className="text-xs text-muted-foreground">Rooms you can share</dt>
                  <dd className="font-mono text-2xl font-semibold">
                    {shareable.length}
                    <span className="text-base text-muted-foreground"> / {records.length}</span>
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">People with access</dt>
                  <dd className="font-mono text-2xl font-semibold">{totalMembers}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Pending invitations</dt>
                  <dd className="font-mono text-2xl font-semibold">{totalPending}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Expiring within 7 days</dt>
                  <dd className="font-mono text-2xl font-semibold">{totalExpiring}</dd>
                </div>
              </dl>
            </div>
          </Card>

          {capabilities.loading && <Spinner label="Reading who has access" />}

          {totalExpiring > 0 && (
            <Notice tone="warn" glyph="clock" title="Some access is expiring soon">
              {totalExpiring === 1
                ? '1 person has access expiring within 7 days.'
                : `${totalExpiring} people have access expiring within 7 days.`}{' '}
              Open a room to extend or clear the date. The warning only reaches people
              who can share the room, which is what the research specifies.
            </Notice>
          )}

          {records.length === 0 && (
            <EmptyState
              title="No rooms to share"
              description="A room is a schema-flexible record. Create one on the Sales rooms page, then come back to invite buyers into it."
            />
          )}

          <ul className="grid gap-3 sm:grid-cols-2">
            {records.map((room) => {
              const snapshot = capabilities.data?.[room.id]
              const actor_ = snapshot?.actor
              const canShare = Boolean(actor_?.can_share)
              return (
                <li key={room.id}>
                  <Card className="flex h-full flex-col gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-sm font-semibold text-foreground">
                        {room.data?.name || room.id}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Owner {room.data?.owner || 'unknown'} · {room.data?.account || 'no account'}
                      </p>
                    </div>

                    {snapshot ? (
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        <Badge tone={actor_?.is_owner ? 'insert' : 'neutral'}>
                          {actor_?.role_label || 'No access'}
                        </Badge>
                        <span className="text-muted-foreground">
                          {snapshot.members.length}{' '}
                          {snapshot.members.length === 1 ? 'person' : 'people'}
                        </span>
                        {snapshot.pending_invitations.length > 0 && (
                          <Badge tone="update">
                            {snapshot.pending_invitations.length} pending
                          </Badge>
                        )}
                        {snapshot.expiring_soon_count > 0 && (
                          <Badge tone="restore">
                            {snapshot.expiring_soon_count} expiring
                          </Badge>
                        )}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        Access for this room could not be read.
                      </p>
                    )}

                    <div className="mt-auto flex flex-wrap items-center gap-2">
                      {/* Rendered only when the server says this person can share.
                          A Viewer gets no Share action at all, which is the
                          researched behaviour rather than a UI decision. */}
                      {canShare ? (
                        <Action
                          glyph="share"
                          variant="primary"
                          onClick={() => setSharing(room)}
                          aria-label={`Share ${room.data?.name || 'room'}`}
                        >
                          Share
                        </Action>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {actor_?.role
                            ? `A ${actor_.role_label} cannot share this room.`
                            : 'You have no access to this room.'}
                        </span>
                      )}
                    </div>
                  </Card>
                </li>
              )
            })}
          </ul>
        </>
      )}

      {sharing && (
        <ShareDialog room={sharing} actor={effectiveActor} onClose={() => setSharing(null)} />
      )}
    </div>
  )
}
