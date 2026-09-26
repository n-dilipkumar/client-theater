import { useEffect, useMemo, useState } from 'react'
import { accessApi } from '../lib/api'
import { EmailChips } from './EmailChips'
import {
  Badge,
  Button,
  Field,
  Modal,
  Notice,
  Spinner,
  inputClass,
  useAsync,
} from './ui'

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
  const [editingExpiry, setEditingExpiry] = useState(false)
  const [draftExpiry, setDraftExpiry] = useState(member.access_valid_until || '')
  const [confirmingRemove, setConfirmingRemove] = useState(false)

  const busy = busyId === member.id
  const assignable = snapshot.actor.assignable_roles
  // The owner is the room, not a grant: it is neither reassignable nor
  // removable, and the row says so instead of offering controls that 400.
  const locked = member.owner

  useEffect(() => {
    setDraftExpiry(member.access_valid_until || '')
    setEditingExpiry(false)
    setConfirmingRemove(false)
  }, [member.access_valid_until, member.role, member.id])

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
                <Button
                  icon="pencil"
                  onClick={() => setEditingExpiry(true)}
                  disabled={busy}
                  aria-label={`Change expiry for ${member.principal}`}
                >
                  Edit date
                </Button>
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
  const snapshot = useAsync(() => accessApi.snapshot(room.id, actor), [room.id, actor])
  const [emails, setEmails] = useState([])
  const [role, setRole] = useState(null)
  const [expiry, setExpiry] = useState('')
  const [sending, setSending] = useState(false)
  const [busyId, setBusyId] = useState(null)
  const [problem, setProblem] = useState(null)
  const [confirmation, setConfirmation] = useState(null)
  const [result, setResult] = useState(null)

  const data = snapshot.data
  const canShare = data?.actor?.can_share ?? false
  const roles = data?.roles ?? []
  const assignable = useMemo(
    () => roles.filter((item) => item.assignable).map((item) => item.id),
    [roles],
  )

  // Default to Viewer, and re-default when the actor changes to someone whose
  // allowed roles differ, so the form can never be left on a role this person
  // is not allowed to hand out.
  useEffect(() => {
    if (!data) return
    setRole((current) =>
      current && assignable.includes(current) ? current : assignable[assignable.length - 1] || 'viewer',
    )
    setProblem(null)
    setConfirmation(null)
    setResult(null)
  }, [data, assignable])

  async function send(event) {
    event.preventDefault()
    if (!emails.length) {
      setProblem({ text: 'Add at least one email address.' })
      return
    }
    setSending(true)
    setProblem(null)
    try {
      const response = await accessApi.invite(
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
      accessApi.updateAccess(member.id, { role: next }, { actor, confirm: true }).then(refresh),
    )

  const onExpirySave = (member, value) =>
    act(member.id, () =>
      accessApi
        .updateAccess(
          member.id,
          { access_valid_until: value, set_expiry: true },
          { actor, confirm: true },
        )
        .then(refresh),
    )

  const onExpiryClear = (member) =>
    act(member.id, () =>
      accessApi
        .updateAccess(member.id, { access_valid_until: null, set_expiry: true }, { actor })
        .then(refresh),
    )

  const onRemove = (member) =>
    act(member.id, () => accessApi.removeAccess(member.id, { actor, confirm: true }).then(refresh))

  const onAccept = (invitation) =>
    act(invitation.id, () => accessApi.accept(invitation.id, actor).then(refresh))

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
        <Notice tone="error" icon="warning" title="Could not load access for this room">
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
            <Notice tone="warn" icon="clock" title="Access expiring soon">
              {data.banner}
            </Notice>
          )}

          {!canShare && (
            <Notice tone="info" icon="shield" title="You cannot share this room">
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
                <Notice tone="error" icon="warning">
                  {problem.text}
                </Notice>
              )}
              {confirmation && (
                <Notice tone="ok" icon="send" title="Invitations sent">
                  {confirmation}
                  {result?.joined_immediately?.length > 0 && (
                    <span className="mt-1 block">
                      Already in the room: {result.joined_immediately.join(', ')}.
                    </span>
                  )}
                </Notice>
              )}

              <Button type="submit" variant="primary" icon="send" disabled={sending || !emails.length}>
                {sending ? 'Sending…' : 'Invite'}
              </Button>
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
                  <Button
                    icon="inbox"
                    disabled={busyId === invitation.id}
                    onClick={() => onAccept(invitation)}
                    aria-label={`Accept the invitation for ${invitation.email}`}
                  >
                    Accept
                  </Button>                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </Modal>
  )
}

export default ShareDialog
