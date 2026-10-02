import { useMemo, useState } from 'react'

import { absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  inputClass,
  useAsync,
} from '@/components/ui'

import { accessApi, listRecords } from './api'
import { STATUS_TONE } from './icons'
import { Checkbox, CopyField, Notice, RadioGroup } from './primitives'

/**
 * Access settings: the seller's side of WF-015.
 *
 * This is the project's stand-in for the vendor's Share -> Access Settings
 * pop-up, which the research says is the only way to reach these controls. The
 * three tiers, the identification options, the domain allowlist, the template
 * default, who has been through the gate, and the verification outbox are all here.
 *
 * The rule that Domain Security is only selectable once Email Verification is on is
 * *shown* rather than silently enforced: the checkbox is disabled and says why. A
 * control that greys out without an explanation reads as a bug, and the server
 * rejects the combination anyway - this form just refuses to describe a state that
 * cannot exist.
 */

const TIERS = [
  {
    value: 'open',
    label: 'Anyone with the link',
    description: 'No identity is collected. The room is open to anonymous viewers.',
  },
  {
    value: 'identify',
    label: 'Collect name or email',
    description:
      'Identification only. The buyer fills in their details for tracking, "without needing them to login to their email to verify".',
  },
  {
    value: 'verify_email',
    label: 'Verify by email',
    description:
      'The buyer receives an email with a link and cannot see the room until they click it. Unlocks Domain Security.',
  },
]

/** Mirror of the server's normalisation, for the live preview under the input. */
function domainList(value) {
  return value
    .split(/[,\n]/)
    .map((part) => part.trim().toLowerCase().replace(/^@/, '').replace(/\.$/, ''))
    .filter(Boolean)
}

/** Policy fields the form owns. Anything else the seller added is untouched. */
const OWNED = [
  'mode',
  'collect_name',
  'collect_email',
  'domain_security',
  'allowed_domains',
  'inherit',
  'template_id',
  'subject_kind',
  'subject_id',
]

function draftFrom(policy, level) {
  const base = policy && level !== 'default' ? policy : { mode: 'open' }
  return {
    mode: base.mode || 'open',
    collect_name: Boolean(base.collect_name),
    collect_email: Boolean(base.collect_email),
    domain_security: Boolean(base.domain_security),
    // The operator types a comma-separated string; the server normalises it.
    allowed_domains: (base.allowed_domains || []).join(', '),
    inherit: level === 'template',
  }
}

export default function AccessSettings() {
  const rooms = useAsync(() => listRecords('room', { limit: 100 }), [])
  const templates = useAsync(() => listRecords('room_template', { limit: 100 }), [])

  const [chosenRoomId, setChosenRoomId] = useState('')
  // Which room is on screen is derived, not stored: an explicit choice wins,
  // otherwise the first room. The old effect that set the state on arrival
  // forced an extra render in which nothing was selected yet.
  const roomId = chosenRoomId || rooms.data?.records?.[0]?.id || ''

  const access = useAsync(
    () => (roomId ? accessApi.roomAccess(roomId) : Promise.resolve(null)),
    [roomId],
  )
  const sessions = useAsync(
    () =>
      roomId
        ? accessApi.sessions(roomId, { include_refused: true, include_bots: true })
        : Promise.resolve(null),
    [roomId],
  )
  const outbox = useAsync(() => (roomId ? accessApi.outbox(roomId) : Promise.resolve(null)), [roomId])

  // The editable copy of the policy is derived from what the server sent, not
// mirrored into state by an effect.
//
// `draftSeed` records which loaded payload the current draft was seeded from.
// When `access.data` is a different object -- a different room, or the same one
// refetched after a save -- the seed no longer matches, so the form shows a
// freshly derived draft immediately. That is what the old
// `useEffect(() => setDraft(draftFrom(access.data.policy, ...)), [access.data])`
// did, minus the render pass in which the previous room's edits were still on
// screen. Nothing calls setState during render to arrange it.
const [draftState, setDraftState] = useState({ seed: null, draft: null })
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [saved, setSaved] = useState(false)
  const [fieldErrors, setFieldErrors] = useState({})

  const policySeed = access.data || null
  const draft =
    draftState.seed === policySeed
      ? draftState.draft
      : policySeed
        ? draftFrom(policySeed.policy, policySeed.level)
        : null

  // An edit keeps the seed it was made against, so the next render still shows
  // the operator's own text.
  const setDraft = (next) =>
    setDraftState({
      seed: policySeed,
      draft: typeof next === 'function' ? next(draft) : next,
    })

  const room = useMemo(
    () => rooms.data?.records?.find((item) => item.id === roomId),
    [rooms.data, roomId],
  )
  const template = useMemo(
    () =>
      templates.data?.records?.find(
        (item) => item.id === (room?.data?.template_id || access.data?.policy?.template_id),
      ),
    [templates.data, room, access.data],
  )
  const parsedDomains = useMemo(() => domainList(draft?.allowed_domains || ''), [draft])
  const inherited = access.data?.level === 'template'
  const domainLocked = draft?.mode !== 'verify_email'
  const canInherit = Boolean(room?.data?.template_id)

  /**
   * How many of the listed attempts the API would have hidden.
   *
   * This page asks for bots and refusals explicitly, because a seller who cannot
   * see what is being excluded cannot tell a working exclusion from a broken one.
   * The count is what makes opting in honest rather than merely inclusive: the
   * default view the API serves leaves these out, and the label says by how many.
   */
  const hidden = useMemo(() => {
    const list = sessions.data?.sessions || []
    return list.filter(
      (session) => session.data.likely_bot || session.data.status === 'refused',
    ).length
  }, [sessions.data])

  async function save(event) {
    event.preventDefault()
    setSaving(true)
    setSaveError(null)
    setFieldErrors({})
    setSaved(false)
    try {
      // Unowned keys ride along untouched: a team's own field on a policy must
      // survive a save from this form, with no migration and no coordination.
      const current = access.data?.policy || {}
      const extra = Object.fromEntries(
        Object.entries(current).filter(([key]) => !OWNED.includes(key)),
      )
      await accessApi.setRoomAccess(roomId, {
        ...extra,
        mode: draft.mode,
        collect_name: draft.collect_name,
        collect_email: draft.collect_email,
        domain_security: domainLocked ? false : draft.domain_security,
        allowed_domains: domainLocked ? [] : parsedDomains,
        inherit: draft.inherit && canInherit,
      })
      // The draft is dropped here rather than on the next `access.data`: until
      // the refetch lands it is still the operator's text, and letting it go
      // early would blank the form mid-save.
      setDraftState({ seed: null, draft: null })
      setSaved(true)
      access.refetch()
      sessions.refetch()
    } catch (error) {
      setSaveError(error)
      if (error.errors) setFieldErrors(error.errors)
    } finally {
      setSaving(false)
    }
  }

  async function clearPolicy() {
    setSaveError(null)
    try {
      await accessApi.clearRoomAccess(roomId)
      access.refetch()
    } catch (error) {
      setSaveError(error)
    }
  }

  async function saveTemplate(policy) {
    setSaveError(null)
    setFieldErrors({})
    setSaved(false)
    try {
      await accessApi.setTemplateAccess(template.id, policy)
      setSaved(true)
      access.refetch()
    } catch (error) {
      setSaveError(error)
      if (error.errors) setFieldErrors(error.errors)
    }
  }

  if (rooms.loading) return <Spinner label="Loading rooms" />
  if (rooms.error) return <ErrorNote error={rooms.error} onRetry={rooms.refetch} />

  const noRooms = (rooms.data?.records || []).length === 0

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-mono text-2xl font-semibold">Access and identity</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Decide who has to prove who they are before they can open a room. Every change
          here is a record, so it lands in the audit log like everything else.
        </p>
      </header>

      {noRooms ? (
        <EmptyState
          title="No rooms to protect yet"
          description="Create a sales room first, then come back and set what it takes to open it."
          action={
            <a href="#/rooms">
              <Button icon="rooms">Go to sales rooms</Button>
            </a>
          }
        />
      ) : (
        <>
          <Card>
            <Field label="Room" id="access-room" hint="The room whose gate you are configuring.">
              <select
                id="access-room"
                className={inputClass}
                value={roomId}
                onChange={(event) => setChosenRoomId(event.target.value)}
              >
                {rooms.data.records.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.data.name || item.id}
                  </option>
                ))}
              </select>
            </Field>
          </Card>

          {access.loading || !draft ? (
            <Spinner label="Loading access settings" />
          ) : (
            <form onSubmit={save} className="space-y-5">
              <Card>
                <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                  <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
                    Who can open this room
                  </h2>
                  <Badge tone={inherited ? 'restore' : 'insert'}>
                    {inherited
                      ? 'inherited from template'
                      : access.data.level === 'default'
                        ? 'no policy set'
                        : 'own policy'}
                  </Badge>
                </div>

                {access.data.errors && (
                  <Notice tone="warn" title="This policy no longer validates">
                    {Object.entries(access.data.errors).map(([field, message]) => (
                      <p key={field}>
                        <span className="font-mono text-xs">{field}</span>: {message}
                      </p>
                    ))}
                    <p className="mt-2">
                      The room is being served as open until it is corrected, so a bad rule cannot
                      lock you out of your own room.
                    </p>
                  </Notice>
                )}

                <div className="mt-4">
                  <RadioGroup
                    name="access-tier"
                    legend="Assurance tier"
                    options={TIERS}
                    value={draft.mode}
                    onChange={(mode) =>
                      setDraft({
                        ...draft,
                        mode,
                        // The rules the research states, applied as the seller switches
                        // tier, so the form cannot describe an impossible combination.
                        collect_email: mode === 'verify_email' ? true : draft.collect_email,
                        domain_security: mode === 'verify_email' ? draft.domain_security : false,
                      })
                    }
                  />
                </div>
              </Card>

              <Card>
                <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
                  Identification
                </h2>
                <div className="mt-2 space-y-1">
                  <Checkbox
                    id="collect-name"
                    label="Ask for a name"
                    description="Shown to you alongside the buyer's activity."
                    checked={draft.collect_name}
                    onChange={(value) => setDraft({ ...draft, collect_name: value })}
                  />
                  <Checkbox
                    id="collect-email"
                    label="Ask for an email address"
                    description={
                      draft.mode === 'verify_email'
                        ? 'Required. The verification link is sent here.'
                        : 'Collected for tracking only; the buyer is not asked to verify it.'
                    }
                    checked={draft.collect_email}
                    disabled={draft.mode === 'verify_email'}
                    onChange={(value) => setDraft({ ...draft, collect_email: value })}
                  />
                  {fieldErrors.collect_email && (
                    <p className="text-xs text-destructive">{fieldErrors.collect_email}</p>
                  )}
                </div>
              </Card>

              <Card>
                <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
                  Domain security
                </h2>
                <div className="mt-2 space-y-1">
                  <Checkbox
                    id="domain-security"
                    label="Restrict to approved email domains"
                    description={
                      domainLocked
                        ? 'Requires Verify by email, because the domain is read from the address the buyer verifies.'
                        : 'A buyer from any other domain is refused and no email is sent to them.'
                    }
                    checked={domainLocked ? false : draft.domain_security}
                    disabled={domainLocked}
                    onChange={(value) => setDraft({ ...draft, domain_security: value })}
                  />
                  {fieldErrors.domain_security && (
                    <p className="text-xs text-destructive">{fieldErrors.domain_security}</p>
                  )}

                  {draft.mode === 'verify_email' && (
                    <Field
                      label="Approved domains"
                      id="allowed-domains"
                      hint="Comma-separated. You do not need to type the @ symbol."
                    >
                      <input
                        id="allowed-domains"
                        className={inputClass}
                        value={draft.allowed_domains}
                        disabled={domainLocked}
                        onChange={(event) =>
                          setDraft({ ...draft, allowed_domains: event.target.value })
                        }
                        placeholder="northwind.example, contoso.example"
                      />
                    </Field>
                  )}

                  {parsedDomains.length > 0 && (
                    <div className="mt-2">
                      <p className="mb-1.5 text-xs font-medium text-muted-foreground">
                        {parsedDomains.length} domain{parsedDomains.length === 1 ? '' : 's'} will be
                        allowed
                      </p>
                      <ul className="flex flex-wrap gap-1.5">
                        {parsedDomains.map((domain) => (
                          <li
                            key={domain}
                            className="rounded-md border border-border-subtle/40 bg-muted px-2 py-0.5 font-mono text-xs text-foreground"
                          >
                            {domain}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {fieldErrors.allowed_domains && (
                    <p className="text-xs text-destructive">{fieldErrors.allowed_domains}</p>
                  )}
                </div>
              </Card>

              <Card>
                <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
                  Template default
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Rooms created from a template can inherit its access policy, so the control is
                  enforced by policy rather than by remembering to set it on every page.
                </p>
                <div className="mt-2 space-y-1">
                  {canInherit ? (
                    <p className="text-sm text-foreground">
                      This room was created from{' '}
                      <span className="font-mono text-xs">
                        {template?.data?.name || room.data.template_id}
                      </span>
                      .
                    </p>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      This room was not created from a template, so there is nothing to inherit.
                    </p>
                  )}
                  <Checkbox
                    id="inherit"
                    label="Follow the template's policy"
                    description={
                      canInherit
                        ? "Any change to the template is picked up here on the next read. Uncheck to keep an independent policy for this room."
                        : 'Needs a template. Create the room from a template to enable it.'
                    }
                    checked={draft.inherit}
                    disabled={!canInherit}
                    onChange={(value) => setDraft({ ...draft, inherit: value })}
                  />
                  {inherited && (
                    <Notice tone="info">
                      You are looking at the template's rule. Save without changing anything to give
                      this room its own copy.
                    </Notice>
                  )}

                  {canInherit && template && (
                    <div className="mt-3 rounded-lg border border-border-subtle/30 p-3">
                      <p className="text-xs font-medium text-muted-foreground">
                        Change the template default instead
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        This reaches every room created from it, without a write to any of them.
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <Button
                          onClick={() =>
                            saveTemplate({
                              mode: 'identify',
                              collect_name: true,
                              collect_email: true,
                            })
                          }
                        >
                          Identification only
                        </Button>
                        <Button
                          onClick={() =>
                            saveTemplate({ mode: 'verify_email', collect_name: true, collect_email: true })
                          }
                        >
                          Verify by email
                        </Button>
                        <Button onClick={() => saveTemplate({ mode: 'open' })}>Open</Button>
                      </div>
                    </div>
                  )}
                </div>
              </Card>

              {saveError && !Object.keys(fieldErrors).length && <ErrorNote error={saveError} />}
              {saved && (
                <Notice tone="good" title="Saved">
                  The change is recorded in the audit log.
                </Notice>
              )}

              <div className="flex flex-wrap gap-2">
                <Button type="submit" variant="primary" disabled={saving}>
                  {saving ? 'Saving…' : 'Save access settings'}
                </Button>
                {access.data.level === 'room' && (
                  <Button icon="trash" onClick={clearPolicy} disabled={saving}>
                    Remove this room's own policy
                  </Button>
                )}
              </div>
            </form>
          )}
        </>
      )}

      {roomId && (
        <>
          <Card>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
                Who has opened it
              </h2>
              <a
                href={`#/view/${roomId}`}
                className="flex min-h-11 items-center gap-2 text-sm text-accent underline underline-offset-2"
              >
                Open the buyer view
              </a>
            </div>
            {sessions.loading ? (
              <Spinner label="Loading sessions" />
            ) : sessions.error ? (
              <ErrorNote error={sessions.error} onRetry={sessions.refetch} />
            ) : (sessions.data?.sessions || []).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nobody has tried to open this room yet.
              </p>
            ) : (
              <>
                <dl className="mb-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                  {Object.entries(sessions.data.by_status || {}).map(([status, count]) => (
                    <div key={status}>
                      <dt className="text-muted-foreground">{status.replace(/_/g, ' ')}</dt>
                      <dd className="font-mono text-lg text-foreground">{count}</dd>
                    </div>
                  ))}
                </dl>
                {hidden > 0 && (
                  <p className="mb-3 text-xs text-muted-foreground">
                    {hidden} of these {sessions.data.count} would be hidden in the default view.
                    Bots and refused attempts are shown here so you can see what is being
                    excluded: link scanners and mail previewers open every link they are
                    given, and counting them as viewers is how analytics fill up with people
                    who were never there.
                  </p>
                )}
                <ul className="divide-y divide-border-subtle/15">
                  {sessions.data.sessions.map((session) => (
                    <li
                      key={session.id}
                      className="flex flex-wrap items-center gap-3 py-2.5 text-sm"
                    >
                      <Badge tone={STATUS_TONE[session.data.status] || 'neutral'}>
                        {session.data.status.replace(/_/g, ' ')}
                      </Badge>
                      <span className="min-w-0 flex-1 truncate text-foreground">
                        {session.data.name || session.data.email || 'anonymous'}
                      </span>
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {session.data.email_domain || '—'}
                      </span>
                      {session.data.likely_bot && <Badge tone="neutral">bot</Badge>}
                      {session.data.refusal_reason && (
                        <span className="text-xs text-destructive">
                          {session.data.refusal_reason.replace(/_/g, ' ')}
                        </span>
                      )}
                      <span
                        className="text-xs text-muted-foreground"
                        title={absoluteTime(session.updated_at)}
                      >
                        {relativeTime(session.updated_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Card>

          <Card>
            <h2 className="font-mono text-sm font-semibold tracking-wide uppercase">
              Verification outbox
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              This project ships no mail server, so the verification message lands here and you
              hand the buyer the link. Plugging in a real transport replaces this and the buyer
              receives the email instead.
            </p>
            {outbox.loading ? (
              <Spinner label="Loading outbox" />
            ) : outbox.error ? (
              <ErrorNote error={outbox.error} onRetry={outbox.refetch} />
            ) : (outbox.data?.messages || []).length === 0 ? (
              <p className="mt-3 text-sm text-muted-foreground">No verification emails queued.</p>
            ) : (
              <ul className="mt-3 space-y-3">
                {outbox.data.messages.map((message) => (
                  <li key={message.id} className="rounded-lg border border-border-subtle/30 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span className="font-mono text-foreground">{message.data.to}</span>
                      <Badge tone={message.data.status === 'sent' ? 'insert' : 'restore'}>
                        {message.data.status}
                      </Badge>
                    </div>
                    <div className="mt-2">
                      <CopyField
                        value={message.data.open_link || message.data.link}
                        label="Give the buyer this link"
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
