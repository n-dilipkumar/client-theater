import { useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Field,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

import { rolesApi } from './api'
import { EmailChips } from './EmailChips'
import { Action, Modal, Notice } from './primitives'

/**
 * The Share dialog: *Who Has Access* plus the invite form.
 *
 * Both live in one surface because the research puts them in one surface: the
 * imminent-expiry banner "appears in the Share dialog, so they reach only the
 * members who can share the room". Keeping them together is what makes that
 * claim true.
 *
 * Two rules are enforced here rather than only on the server, but never
 * *instead* of the server:
 *
 *  * a role the acting user may not assign is disabled, with the reason shown,
 *    so the delegation rule is legible rather than mysterious;
 *  * a destructive change asks first. The product this mirrors does not, which
 *    the research records as a safety gap.
 *
 * Moved here from `frontend/src/components/ShareDialog.jsx` on the branch and
 * re-pointed at this feature's own `rolesApi`, `Modal` and `Notice`. The branch
 * put the dialog in the shared `components/` directory, took its API from a new
 * `accessApi` on the shared `api` object, and used `Modal` and `Notice` that it
 * had added to the shared `components/ui.jsx`. All three are shared-file edits
 * and all three are refused; `./api.js` and `./primitives.jsx` say what replaced
 * them and why.
 */

/** Render the documented *No Expiration* row, or the UTC cut-off instant. */
function ExpiryLabel({ member }) {
  if (!member.access_valid_until) {
    return <span className="text-muted-foreground">No Expiration</span>
  }
  return (
    <span className="font-mono text-foreground">
      {member.access_valid_until}
      <span className="ml-1 text-muted-foreground">(UTC)</span>
    </span>
  )
}

function MemberRow({ member, snapshot, onRoleChange, onExpirySave, onExpiryClear, onRemove, busyId }) {
  // The row's own state is scoped to the grant it belongs to. A different grant's
  // expiry, role or id derives fresh values during render, which is what the old
  // effect's three setStates arranged -- except that it happened a render later,
// so for one render a newly-listed grant was shown with the previous grant's
  // half-typed expiry and an open confirmation.
  const [rowEdits, setRowEdits] = useState({ forGrant: undefined, edits: {} })

  const grantKey = `${member.id}|${member.role}|${member.access_valid_until || ''}`
  const edits = rowEdits.forGrant === grantKey ? rowEdits.edits : {}

  const editingExpiry = Boolean(edits.editingExpiry)
  const setEditingExpiry = (value) =>
    setRowEdits({ forGrant: grantKey, edits: { ...edits, editingExpiry: value } })

  const draftExpiry = edits.draftExpiry ?? member.access_valid_until ?? ''
  const setDraftExpiry = (value) =>
    setRowEdits({ forGrant: grantKey, edits: { ...edits, draftExpiry: value } })

  const confirmingRemove = Boolean(edits.confirmingRemove)
  const setConfirmingRemove = (value) =>
    setRowEdits({ forGrant: grantKey, edits: { ...edits, confirmingRemove: value } })

  const busy = busyId === member.id
  // The owner is the room, not a grant: it is neither reassignable nor
  // removable, and the row says so instead of offering controls that 400.
  const locked = member.owner

  return (
    <li className="rounded-lg border border-border-subtle/25 bg-background/30 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-mono text-sm text-foreground">{member.principal}</span>
            {member.owner && <Badge tone="neutral">Owner</Badge>}
            {member.expiring_soon && (
              <Badge tone="restore">
                <span className="inline-flex items-center gap-1">
                  Expiring in {member.days_until_expiry}d
                </span>
              </Badge>
            )}
            {member.joined_immediately && <Badge tone="insert">Joined on invite</Badge>}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {member.role_label}
            {' · '}
            <ExpiryLabel member={member} />
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {locked ? (
            <span className="text-xs text-muted-foreground">Cannot be changed</span>
          ) : (
            <>
              <label className="sr-only" htmlFor={`role-${member.id}`}>
                Role for {member.principal}
              </label>
              <select
                id={`role-${member.id}`}
                className={`${inputClass} min-w-40`}
                value={member.role}
                disabled={busy}
                onChange={(event) => onRoleChange(member, event.target.value)}
              >
                {snapshot.roles.map((role) => (
                  <option
                    key={role.id}
                    value={role.id}
                    disabled={!role.assignable && role.id !== member.role}
                  >
                    {role.label}
                    {role.assignable ? '' : ' — not assignable by you'}
                  </option>
                ))}
              </select>

              {!editingExpiry ? (
                <Action
                  glyph="pencil"
                  onClick={() => setEditingExpiry(true)}
                  disabled={busy}
                  aria-label={`Change expiry for ${member.principal}`}
                >
                  Edit date
                </Action>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <label className="sr-only" htmlFor={`expiry-${member.id}`}>
                    Access valid until for {member.principal}
                  </label>
                  <input
                    id={`expiry-${member.id}`}
                    type="date"
                    className={`${inputClass} min-w-40`}
                    value={draftExpiry}
                    onChange={(event) => setDraftExpiry(event.target.value)}
                  />
                  <Button
                    variant="primary"
                    disabled={busy}
                    onClick={async () => {
                      // Stay in the editor if the save was refused, so the
                      // reason is still visible next to the field.
                      if (await onExpirySave(member, draftExpiry || null)) {
                        setEditingExpiry(false)
                      }
                    }}
                  >
                    Save
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={async () => {
                      if (await onExpiryClear(member)) setEditingExpiry(false)
                    }}
                  >
                    Clear
                  </Button>
                  <Button variant="ghost" onClick={() => setEditingExpiry(false)} disabled={busy}>
                    Cancel
                  </Button>
                </div>
              )}

              {confirmingRemove ? (
                <span className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Remove {member.principal}?</span>
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={() => onRemove(member)}
                  >
                    Confirm
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmingRemove(false)} disabled={busy}>
                    Keep
                  </Button>
                </span>
              ) : (
                <Button
                  icon="trash"
                  variant="danger"
                  disabled={busy}
                  onClick={() => setConfirmingRemove(true)}
                  aria-label={`Remove ${member.principal} from this room`}
                >
                  Remove
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </li>
  )
}

export function ShareDialog({ room, actor, onClose }) {
  const snapshot = useAsync(() => rolesApi.snapshot(room.id, actor), [room.id, actor])
  const [emails, setEmails] = useState([])
  const [expiry, setExpiry] = useState('')
  const [sending, setSending] = useState(false)
  const [busyId, setBusyId] = useState(null)

  const data = snapshot.data
  const canShare = data?.actor?.can_share ?? false
  // Memoised so the `assignable` below keeps a stable dependency. A fresh `[]`
  // each render made every dep change on every render, so the memo never held.
  const roles = useMemo(() => data?.roles ?? [], [data])
  const assignable = useMemo(
    () => roles.filter((item) => item.assignable).map((item) => item.id),
    [roles],
  )

  // The role defaults to Viewer, and re-defaults when the actor changes to someone
  // whose allowed roles differ, so the form can never be left on a role this
  // person is not allowed to hand out. Derived rather than copied into state by
  // an effect: a role the new actor cannot assign is corrected on the first
  // render instead of a render later.
  const fallbackRole = assignable[assignable.length - 1] || 'viewer'
  const [roleChoice, setRoleChoice] = useState({ forSnapshot: undefined, role: null })
  const chosenRole = roleChoice.forSnapshot === data ? roleChoice.role : null
  const role = chosenRole && assignable.includes(chosenRole) ? chosenRole : fallbackRole
  const setRole = (next) =>
    setRoleChoice({ forSnapshot: data, role: typeof next === 'function' ? next(role) : next })

  // The problem, the confirmation and the result all describe an attempt made by
  // the current actor, so they read as absent when the snapshot changes.
  const [outcome, setOutcome] = useState({ forSnapshot: undefined, problem: null, confirmation: null, result: null })
  const current = outcome.forSnapshot === data ? outcome : { problem: null, confirmation: null, result: null }
  const problem = current.problem
  const confirmation = current.confirmation
  const result = current.result

  const setProblem = (next) =>
    setOutcome({ forSnapshot: data, ...current, problem: typeof next === 'function' ? next(problem) : next })
  const setConfirmation = (next) =>
    setOutcome({ forSnapshot: data, ...current, confirmation: typeof next === 'function' ? next(confirmation) : next })
  const setResult = (next) =>
    setOutcome({ forSnapshot: data, ...current, result: typeof next === 'function' ? next(result) : next })

  async function send(event) {
    event.preventDefault()
    if (!emails.length) {
      setProblem({ text: 'Add at least one email address.' })
      return
    }
    setSending(true)
    setProblem(null)
    try {
      const response = await rolesApi.invite(
        room.id,
        { emails, role, access_valid_until: expiry || null },
        actor,
      )
      setConfirmation(response.message)
      setResult(response)
      setEmails([])
      setExpiry('')
      snapshot.refetch()
    } catch (error) {
      setProblem({ text: error.message })
    } finally {
      setSending(false)
    }
  }

  /** Every mutation goes through here so a 428 can turn into a confirmation. */
  async function act(id, run) {
    setBusyId(id)
    setProblem(null)
    try {
      await run()
      return true
    } catch (error) {
      if (error.status === 428) {
        // The server asked for confirmation; say what it asked about.
        setProblem({ text: error.message, needsConfirm: true, id })
        return false
      }
      setProblem({ text: error.message })
      return false
    } finally {
      setBusyId(null)
    }
  }

  const refresh = () => snapshot.refetch()

  const onRoleChange = (member, next) =>
    act(member.id, () =>
      rolesApi.updateAccess(member.id, { role: next }, { actor, confirm: true }).then(refresh),
    )

  const onExpirySave = (member, value) =>
    act(member.id, () =>
      rolesApi
        .updateAccess(
          member.id,
          { access_valid_until: value, set_expiry: true },
          { actor, confirm: true },
        )
        .then(refresh),
    )

  const onExpiryClear = (member) =>
    act(member.id, () =>
      rolesApi
        .updateAccess(member.id, { access_valid_until: null, set_expiry: true }, { actor })
        .then(refresh),
    )

  const onRemove = (member) =>
    act(member.id, () => rolesApi.removeAccess(member.id, { actor, confirm: true }).then(refresh))

  const onAccept = (invitation) =>
    act(invitation.id, () => rolesApi.accept(invitation.id, actor).then(refresh))

  return (
    <Modal
      title={`Share ${room.data?.name || 'room'}`}
      description="Invite buyers with a role and an access expiry. Access ends at the end of the expiration date in UTC."
      onClose={onClose}
      wide
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Every change here is written through the audited store.
          </p>
          <Button onClick={onClose}>Done</Button>
        </div>
      }
    >
      {snapshot.loading && <Spinner label="Loading who has access" />}
      {snapshot.error && (
        <Notice tone="error" glyph="warning" title="Could not load access for this room">
          {snapshot.error.message}
        </Notice>
      )}

      {data && (
        <div className="space-y-5">
          {/*
            The documented banner. It is only reachable from inside this dialog,
            which is only openable by someone who can share, so it reaches only
            the people it is meant to.
          */}
          {data.banner && (
            <Notice tone="warn" glyph="clock" title="Access expiring soon">
              {data.banner}
            </Notice>
          )}

          {!canShare && (
            <Notice tone="info" glyph="shield" title="You cannot share this room">
              You are viewing as {data.actor.principal || 'an anonymous visitor'}
              {data.actor.role ? ` with the ${data.actor.role_label} role` : ' with no access'}. The list
              below is read-only; only a Room Collaborator, Content Contributor, or the room owner
              can change who has access.
            </Notice>
          )}

          {canShare && (
            <form onSubmit={send} className="space-y-4">
              <Field
                label="Email Addresses"
                id="invite-emails"
                hint="One role and one expiration date apply to the whole invitation."
              >
                <EmailChips value={emails} onChange={setEmails} id="invite-emails" />
              </Field>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Role" id="invite-role" hint={roles.find((r) => r.id === role)?.description}>
                  <select
                    id="invite-role"
                    className={inputClass}
                    value={role || ''}
                    onChange={(event) => setRole(event.target.value)}
                  >
                    {roles.map((item) => (
                      <option key={item.id} value={item.id} disabled={!item.assignable}>
                        {item.label}
                        {item.assignable ? '' : ' — only the room owner can assign this'}
                      </option>
                    ))}
                  </select>
                </Field>

                <Field
                  label="Access Valid Until"
                  id="invite-expiry"
                  hint="Optional. Leave empty for No Expiration, which ends only when you remove them."
                >
                  <input
                    id="invite-expiry"
                    type="date"
                    className={inputClass}
                    value={expiry}
                    min={new Date().toISOString().slice(0, 10)}
                    onChange={(event) => setExpiry(event.target.value)}
                  />
                </Field>
              </div>

              {problem && (
                <Notice tone="error" glyph="warning">
                  {problem.text}
                </Notice>
              )}
              {confirmation && (
                <Notice tone="ok" glyph="send" title="Invitations sent">
                  {confirmation}
                  {result?.joined_immediately?.length > 0 && (
                    <span className="mt-1 block">
                      Already in the room: {result.joined_immediately.join(', ')}.
                    </span>
                  )}
                </Notice>
              )}

              <Action type="submit" variant="primary" glyph="send" disabled={sending || !emails.length}>
                {sending ? 'Sending…' : 'Invite'}
              </Action>
            </form>
          )}

          {/*
            Who Has Access. Edit changes the date, the role drop-down changes the
            role, the trash icon removes someone.
          */}
          <section aria-labelledby="who-has-access">
            <h3 id="who-has-access" className="font-mono text-sm font-semibold text-foreground">
              Who Has Access
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {data.members.length} {data.members.length === 1 ? 'person' : 'people'}, as of{' '}
              <span className="font-mono">{data.as_of}</span>.
            </p>

            <ul className="mt-3 space-y-2">
              {data.members.length === 0 && (
                <li className="rounded-lg border border-dashed border-border-subtle/50 p-4 text-sm text-muted-foreground">
                  No one else has access yet.
                </li>
              )}
              {data.members.map((member) => (
                <MemberRow
                  key={member.id}
                  member={member}
                  snapshot={data}
                  busyId={busyId}
                  onRoleChange={onRoleChange}
                  onExpirySave={onExpirySave}
                  onExpiryClear={onExpiryClear}
                  onRemove={onRemove}
                />
              ))}
            </ul>
          </section>

          <section aria-labelledby="pending-invitations">
            <h3 id="pending-invitations" className="font-mono text-sm font-semibold text-foreground">
              Pending invitations
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              An invitation expires {data.invitation_ttl_hours} hours after it is sent. Accepting one joins
              the room as a member.
            </p>

            <ul className="mt-3 space-y-2">
              {data.pending_invitations.length === 0 && (
                <li className="rounded-lg border border-dashed border-border-subtle/50 p-4 text-sm text-muted-foreground">
                  No invitations are waiting.
                </li>
              )}
              {data.pending_invitations.map((invitation) => (
                <li
                  key={invitation.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-subtle/25 bg-background/30 p-3"
                >
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-foreground">{invitation.email}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {invitation.role_label} · expires in {invitation.hours_until_expiry}h
                      {invitation.access_valid_until ? ` · access until ${invitation.access_valid_until}` : ' · No Expiration'}
                    </p>
                  </div>
                  <Action
                    glyph="inbox"
                    disabled={busyId === invitation.id}
                    onClick={() => onAccept(invitation)}
                    aria-label={`Accept the invitation for ${invitation.email}`}
                  >
                    Accept
                  </Action>                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </Modal>
  )
}

export default ShareDialog
