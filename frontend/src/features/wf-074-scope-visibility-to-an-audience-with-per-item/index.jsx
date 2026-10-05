import { useCallback, useState } from 'react'

import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  StatCard,
  inputClass,
  useAsync,
} from '@/components/ui'

import {
  ASSUMPTION,
  CAPS,
  DEFAULT_DENY,
  DOMAIN_RULE,
  EMAIL_GATE_NOTE,
  LIMITATION,
  MEMBERSHIP_STEPS,
  NOT_PROOF,
  SCOPE_CONFLICT_NOTE,
  SCOPE_OWNER,
  SCOPE_STATES,
  audienceApi,
  itemTypeLabel,
  listRooms,
  membershipLabel,
  scopeStateLabel,
} from './api'
import { AUDIENCE_ICON, DOCUMENT_ICON, DOWNLOAD_ICON, FOLDER_ICON, VIEW_ICON } from './icons'
import { FlagToggle, Notice, PermissionGrid, ScopeState, Select } from './primitives'

/**
 * WF-074: scope visibility to an audience with per-item permissions.
 *
 * The page has five jobs, in this order, and the order is the design.
 *
 * **Say what the default is before showing any grid.** The guide says "A new group sees nothing
 * until you grant permissions", so an audience with no grants is the shipped state rather than an
 * empty one. A page that opened with a populated permissions grid and no statement of the default
 * would be showing the exceptional case as the ordinary one, so the default is the first thing
 * rendered, above the fold.
 *
 * **Draw the two flags as two columns.** The entry has two independent required booleans because
 * the source's own example is a folder an audience may browse but not download. One "allowed"
 * column would collapse that case into something the research explicitly distinguishes, so the
 * grid has a column per flag and a state badge that names all four outcomes, including the two
 * different denials.
 *
 * **Name both scopes and their different write semantics on the same screen.** The group ACL is
 * delta: an item you omit keeps what it had. The link ACL is a full replace: an item you do not
 * list loses its override, and an empty array clears every one of them. A rep who learned the
 * delta rule and applied it to a link would widen access they meant to close.
 *
 * **Show the two empty link states separately.** A link that was never scoped shows the full room
 * and a link whose scope was cleared shows nothing. They both store zero rows, so the page reads
 * the scope state the server derived rather than counting rows itself.
 *
 * **Say what a match is not.** A membership match records that an address matched a list, a
 * domain or an open group. It does not establish who used the address, and this workflow invites
 * nobody and sends no email.
 *
 * Every state this page can be in is rendered: loading, error, empty. A permissions page that goes
 * blank when the API is down looks like a room that has been locked down, which is the one reading
 * that must never be possible on this screen.
 */

/** The one flag write, shared by the grid and the link panel. */
function useGridToggle() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const toggle = useCallback(async (write, row, flag, value) => {
    setBusy(true)
    setError(null)
    try {
      await write([{ item_id: row.item_id, item_type: row.item_type, [flag]: value }])
      return { ok: true }
    } catch (caught) {
      setError(caught)
      return { ok: false }
    } finally {
      setBusy(false)
    }
  }, [])

  return { busy, error, toggle, clearError: useCallback(() => setError(null), []) }
}

/** The field-keyed messages a 400 carries, so each lands beside the input that caused it. */
function FieldErrors({ error }) {
  if (!error) return null
  const errors = error.errors || { _: error.message }
  return (
    <div className="mt-3">
      <Notice
        tone="destructive"
        title="That change was not saved"
        action={<Button onClick={() => window.location.reload()}>Reload</Button>}
      >
        <ul className="list-disc space-y-0.5 pl-4">
          {Object.entries(errors).map(([field, message]) => (
            <li key={field}>
              <span className="font-mono">{field}</span>: {message}
            </li>
          ))}
        </ul>
      </Notice>
    </div>
  )
}

function MemberPanel({ group, onChanged }) {
  const [address, setAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const members = useAsync(() => audienceApi.members(group.id), [group.id])

  const add = async () => {
    const list = address
      .split(/[\s,;]+/)
      .map((value) => value.trim())
      .filter(Boolean)
    if (list.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const row = await audienceApi.addMembers(group.id, list)
      setResult(row)
      setAddress('')
      members.refetch()
      onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (memberId) => {
    setBusy(true)
    setError(null)
    try {
      await audienceApi.removeMember(group.id, memberId)
      members.refetch()
      onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-4 rounded-sm border border-border-subtle bg-muted p-4">
      <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Members
      </h3>

      {group.allow_all ? (
        <div className="mt-3">
          <Notice tone="warning" title="This audience allows anyone through the link's gates">
            Anyone who passes the link&apos;s other access gates is treated as a member, and the
            email and domain checks are skipped. The list below is still what a rep has read, but
            it is no longer what admits a viewer.
          </Notice>
        </div>
      ) : null}

      <div className="mt-3">
        <Field
          label="Add member addresses"
          id={`wf074-add-${group.id}`}
          hint={`One per line, or separated by commas. Up to ${CAPS.membersPerCall} in one call. Already-present addresses are skipped, and no invitation email is sent.`}
        >
          <textarea
            id={`wf074-add-${group.id}`}
            rows={3}
            className={inputClass}
            value={address}
            onChange={(event) => setAddress(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-3">
        <Button variant="primary" disabled={busy} onClick={add}>
          {busy ? 'Adding' : 'Add these addresses'}
        </Button>
      </div>

      <FieldErrors error={error} />

      {result && (
        <div className="mt-3">
          <Notice
            tone={result.added_count > 0 ? 'success' : 'info'}
            title={
              result.added_count > 0
                ? `${result.added_count} address(es) added`
                : 'Every address was already a member'
            }
          >
            <p>
              {result.skipped_count > 0
                ? `${result.skipped_count} address(es) skipped because they were already members. `
                : ''}
              {result.invitation_note}
            </p>
            <p className="mt-2 text-xs">{NOT_PROOF}</p>
          </Notice>
        </div>
      )}

      <div className="mt-4">
        {members.loading ? (
          <Spinner label="Loading members" />
        ) : members.error ? (
          <ErrorNote error={members.error} onRetry={members.refetch} />
        ) : (members.data?.members || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            This audience has no explicit members. A viewer is admitted by an email on this list, by
            a domain on the group, or by nothing at all when the group allows everyone.
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {(members.data?.members || []).map((member) => (
              <li key={member.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="min-w-0">
                  <span className="block font-mono text-sm text-foreground">{member.email}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    Added {member.added_at}. No invitation email was sent.
                  </span>
                </span>
                <Button variant="danger" disabled={busy} onClick={() => remove(member.id)}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <p className="mt-4 border-t border-border-subtle pt-3 text-xs text-muted-foreground">
        Removing a member does not reissue the link. The next request from that address is refused,
        because membership is re-evaluated on every view.
      </p>
    </div>
  )
}

function DomainsPanel({ group, onChanged }) {
  const [text, setText] = useState((group.domains || []).join('\n'))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [allowAll, setAllowAll] = useState(group.allow_all === true)

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const domains = text
        .split(/[\s,;]+/)
        .map((value) => value.trim())
        .filter(Boolean)
      await audienceApi.updateGroup(group.id, {
        domains: domains.length > 0 ? domains : null,
        allow_all: allowAll,
      })
      onChanged()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-4 rounded-sm border border-border-subtle bg-muted p-4">
      <h3 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Email domains
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">
        Anyone at one of these domains counts as a member without being listed. Up to{' '}
        {CAPS.domains} domains.
      </p>

      <div className="mt-3">
        <Field label="Domains" id={`wf074-domains-${group.id}`} hint={DOMAIN_RULE}>
          <textarea
            id={`wf074-domains-${group.id}`}
            rows={3}
            className={inputClass}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-3">
        <FlagToggle
          id={`wf074-allow-all-${group.id}`}
          label="Allow anyone through the link's other gates"
          hint="When on, the email and domain checks are skipped and every viewer counts as a member."
          checked={allowAll}
          onChange={setAllowAll}
          disabled={busy}
        />
      </div>

      <div className="mt-3">
        <Button variant="primary" disabled={busy} onClick={save}>
          {busy ? 'Saving' : 'Save the audience'}
        </Button>
      </div>

      <FieldErrors error={error} />
    </div>
  )
}

function GroupPanel({ group, onChanged }) {
  const grid = useAsync(() => audienceApi.groupPermissions(group.id), [group.id])
  const { busy, error, toggle, clearError } = useGridToggle()

  const write = useCallback(
    async (entries) => {
      await audienceApi.setGroupPermissions(group.id, entries)
      grid.refetch()
      onChanged()
    },
    [group.id, grid, onChanged],
  )

  const rows = grid.data?.items || []
  const granted = grid.data?.granted ?? 0
  const dangling = grid.data?.dangling || []

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">{group.name}</h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{group.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={group.allow_all ? 'warning' : 'neutral'}>
            {group.allow_all ? 'Open to anyone' : 'Members only'}
          </Badge>
          <Badge tone={granted > 0 ? 'insert' : 'delete'}>
            {granted > 0 ? `${granted} of ${rows.length} granted` : 'Nothing granted'}
          </Badge>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-3">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <Icon path={VIEW_ICON} size={14} />
          {group.member_count} member(s)
        </span>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <Icon path={DOCUMENT_ICON} size={14} />
          {group.link_count} link(s)
        </span>
      </div>

      {granted === 0 && (
        <div className="mt-3">
          <Notice tone="warning" title="This audience sees nothing yet">
            <p>{DEFAULT_DENY}</p>
          </Notice>
        </div>
      )}

      {error && (
        <div className="mt-3">
          <Notice
            tone="destructive"
            title="That flag was not saved"
            action={<Button onClick={clearError}>Dismiss</Button>}
          >
            {error.message}
          </Notice>
        </div>
      )}

      <div className="mt-4">
        {grid.loading ? (
          <Spinner label="Loading the permissions grid" />
        ) : grid.error ? (
          <ErrorNote error={grid.error} onRetry={grid.refetch} />
        ) : (
          <PermissionGrid
            rows={rows}
            itemTypeLabels={{
              dataroom_document: 'Document',
              dataroom_folder: 'Folder',
            }}
            onToggle={busy ? undefined : (row, flag, value) => toggle(write, row, flag, value)}
          />
        )}
      </div>

      <p className="mt-3 text-xs text-muted-foreground">
        {grid.data?.semantics_text || 'An item this audience is not granted is invisible to it.'}{' '}
        Folders above a granted item are opened so the tree stays navigable, with view only.
      </p>

      {dangling.length > 0 && (
        <div className="mt-3">
          <Notice tone="warning" title="Some grants name an item this room does not have">
            <ul className="list-disc space-y-0.5 pl-4">
              {dangling.map((row) => (
                <li key={row.entry_key}>
                  <span className="font-mono text-xs">{row.item_id}</span>: {row.why}
                </li>
              ))}
            </ul>
          </Notice>
        </div>
      )}

      {grid.data?.no_items_note && (
        <div className="mt-3">
          <Notice tone="info" title="There is nothing to grant in this room">
            <p>{grid.data.no_items_note}</p>
          </Notice>
        </div>
      )}

      <DomainsPanel group={group} onChanged={onChanged} />
      <MemberPanel group={group} onChanged={onChanged} />
    </Card>
  )
}

function LinkPanel({ link, onChanged }) {
  const [email, setEmail] = useState('')
  const grid = useAsync(() => audienceApi.linkPermissions(link.id), [link.id])
  const view = useAsync(() => audienceApi.view(link.id, email || undefined), [link.id, email])
  const { busy, error, toggle, clearError } = useGridToggle()

  const groupScoped = link.audience_type === 'group'

  const write = useCallback(
    async (entries) => {
      await audienceApi.setLinkPermissions(link.id, entries)
      grid.refetch()
      view.refetch()
      onChanged()
    },
    [link.id, grid, view, onChanged],
  )

  const clearScope = async () => {
    await write([])
    grid.refetch()
    view.refetch()
  }

  const rows = grid.data?.items || []

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-base font-semibold text-foreground">{link.name}</h3>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{link.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={groupScoped ? 'update' : 'neutral'}>{link.audience_label}</Badge>
          <Badge tone="neutral">{scopeStateLabel(link.scope_state)}</Badge>
        </div>
      </div>

      <div className="mt-3">
        <ScopeState link={link} />
      </div>

      {groupScoped && (
        <div className="mt-3">
          <Notice tone="warning" title="This link's permissions belong to its group">
            <p>{SCOPE_CONFLICT_NOTE}</p>
          </Notice>
        </div>
      )}

      {error && (
        <div className="mt-3">
          <Notice
            tone="destructive"
            title="That permission was not saved"
            action={<Button onClick={clearError}>Dismiss</Button>}
          >
            {error.wayOut || error.message}
          </Notice>
        </div>
      )}

      <div className="mt-4">
        {grid.loading ? (
          <Spinner label="Loading the link permissions" />
        ) : grid.error ? (
          <ErrorNote error={grid.error} onRetry={grid.refetch} />
        ) : (
          <>
            <PermissionGrid
              rows={rows}
              itemTypeLabels={{ dataroom_document: 'Document', dataroom_folder: 'Folder' }}
              onToggle={
                groupScoped || busy ? undefined : (row, flag, value) => toggle(write, row, flag, value)
              }
              readOnlyReason={
                groupScoped ? 'Set these on the group instead.' : 'The link is being written.'
              }
            />

            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                variant="danger"
                disabled={groupScoped || busy}
                onClick={clearScope}
              >
                Clear every override
              </Button>
              <p className="self-center text-xs text-muted-foreground">
                An item this link does not list is hidden. Clearing every override hides the whole
                room, which is not the same as never having scoped it.
              </p>
            </div>

            {(grid.data?.revoked || []).length > 0 && (
              <div className="mt-3">
                <Notice tone="info" title="Overrides this link no longer carries">
                  <ul className="list-disc space-y-0.5 pl-4">
                    {grid.data.revoked.map((row) => (
                      <li key={`${row.entry_key}-${row.revoked_at}`}>
                        <span className="font-mono text-xs">{row.item_id}</span>: revoked at{' '}
                        {row.revoked_at}. The row is kept so the change is readable later.
                      </li>
                    ))}
                  </ul>
                </Notice>
              </div>
            )}
          </>
        )}
      </div>

      <div className="mt-5 rounded-sm border border-border-subtle bg-muted p-4">
        <h4 className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          What a viewer sees
        </h4>
        <p className="mt-1 text-sm text-muted-foreground">
          The resolved permission set filters the room before any bytes are sent. Enter an address
          to resolve the membership and see what that address would be shown.
        </p>

        <div className="mt-3">
          <Field
            label="Viewer email address"
            id={`wf074-view-email-${link.id}`}
            hint="Matched in a fixed order: an explicit member email, then a domain on the group, then an open group."
          >
            <input
              id={`wf074-view-email-${link.id}`}
              type="email"
              className={inputClass}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
        </div>

        <div className="mt-3">
          {view.loading ? (
            <Spinner label="Resolving the view" />
          ) : view.error ? (
            <ErrorNote error={view.error} onRetry={view.refetch} />
          ) : (
            <ViewerResult
              view={view.data}
              itemTypeLabels={{ dataroom_document: 'Document', dataroom_folder: 'Folder' }}
            />
          )}
        </div>
      </div>
    </Card>
  )
}

/**
 * What one address would be shown.
 *
 * A refusal is rendered as a refusal with its reason, not as an empty list: "this address is not
 * in the audience" and "this audience has been granted nothing" both produce zero rows, and only
 * the membership step tells them apart.
 */
function ViewerResult({ view, itemTypeLabels }) {
  if (!view) return null
  const viewRows = view.items || []

  if (view.admitted === false) {
    return (
      <Notice tone="warning" title="This address would not be admitted">
        <p>
          {membershipLabel(view.membership_step)}. {EMAIL_GATE_NOTE}
        </p>
        <p className="mt-2 text-xs">{NOT_PROOF}</p>
      </Notice>
    )
  }

  const hidden = view.hidden_by_reason || {}
  const hiddenTotal = view.hidden_count ?? 0

  return (
    <div>
      <Notice tone="info" title="Admitted">
        <p>
          Matched by {membershipLabel(view.membership_step)}
          {view.membership?.matched_domain ? ` at ${view.membership.matched_domain}` : ''}.
        </p>
        <p className="mt-2 text-xs">{NOT_PROOF}</p>
      </Notice>

      <div className="mt-3">
        <p className="text-sm text-foreground">
          {view.item_count} item(s) visible, {hiddenTotal} hidden. The hidden items are counted and
          never returned.
        </p>
        {hiddenTotal > 0 && (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
            {Object.entries(hidden).map(([reason, count]) => (
              <li key={reason}>
                {count} hidden because {reason === 'can_view_is_false' ? 'a rep revoked it' : 'no grant exists'}
              </li>
            ))}
          </ul>
        )}
      </div>

      {viewRows.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">
          Nothing is visible on this link. {view.scope_state === 'cleared' ? 'Its scope was cleared.' : ''}
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-border-subtle">
          {viewRows.map((row) => (
            <li key={row.item_id} className="flex flex-wrap items-start justify-between gap-2 py-2">
              <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">{row.name}</span>
                <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                  {itemTypeLabels[row.item_type] || row.item_type} /{' '}
                  {row.can_download ? 'view and download' : row.can_view ? 'view only' : 'hidden'}
                </span>
                {row.auto_opened && (
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {row.auto_opened_meaning}
                  </span>
                )}
              </span>
              {row.download_blocked_by_link && (
                <Badge tone="warning">Download off for this link</Badge>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function NewAudienceForm({ roomId, onCreated }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      await audienceApi.createGroup(roomId, { name: name.trim() || 'Untitled audience' })
      setName('')
      onCreated()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Create an audience</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        A named audience, its member addresses and its email domains, granted access item by item.
        It starts seeing nothing.
      </p>
      <div className="mt-4">
        <Field label="Audience name" id="wf074-new-name" hint="For example, Co-investors.">
          <input
            id="wf074-new-name"
            className={inputClass}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
      </div>
      <div className="mt-4">
        <Button variant="primary" disabled={busy} onClick={create}>
          {busy ? 'Creating' : 'Create the audience'}
        </Button>
      </div>
      <FieldErrors error={error} />
    </Card>
  )
}

function VocabularyPanel({ vocabulary }) {
  const membership = vocabulary?.membership
  const caps = vocabulary?.caps || {}
  const gating = vocabulary?.link_gating
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        What this workflow enforces
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Fetched from the server that validates it, so the grid cannot drift from the rules.
      </p>

      <div className="mt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Membership, matched in this order
        </p>
        <ol className="mt-2 space-y-1.5">
          {(membership?.steps || MEMBERSHIP_STEPS).map((step, index) => (
            <li key={step.id} className="text-sm text-muted-foreground">
              <span className="font-mono text-xs text-foreground">{index + 1}.</span>{' '}
              {step.label}
            </li>
          ))}
        </ol>
        {membership?.allow_all && (
          <p className="mt-2 text-xs text-muted-foreground">{membership.allow_all}</p>
        )}
      </div>

      <div className="mt-4 border-t border-border-subtle pt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          The three size caps
        </p>
        <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
          <li>
            <span className="font-mono text-foreground">{caps.domains ?? CAPS.domains}</span>{' '}
            domains on a group
          </li>
          <li>
            <span className="font-mono text-foreground">
              {caps.members_per_call ?? CAPS.membersPerCall}
            </span>{' '}
            member addresses per call
          </li>
          <li>
            <span className="font-mono text-foreground">
              {caps.permissions_per_call ?? CAPS.permissionsPerCall}
            </span>{' '}
            permission entries per call
          </li>
        </ul>
      </div>

      <div className="mt-4 border-t border-border-subtle pt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Email gating on a group link
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {gating?.note || EMAIL_GATE_NOTE}
        </p>
      </div>

      <div className="mt-4 border-t border-border-subtle pt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          The two scopes write differently
        </p>
        <ul className="mt-2 space-y-2">
          {(vocabulary?.scopes || []).map((scope) => (
            <li key={scope.id} className="text-sm text-muted-foreground">
              <span className="font-mono text-foreground">{scope.id}</span> ({scope.semantics}):{' '}
              {scope.description}
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-4 border-t border-border-subtle pt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          The four link scope states
        </p>
        <ul className="mt-2 space-y-1.5">
          {(vocabulary?.link_scope_states || SCOPE_STATES).map((state) => (
            <li key={state.id} className="text-sm text-muted-foreground">
              <span className="font-mono text-xs text-foreground">{state.id}</span>: {state.meaning}
            </li>
          ))}
        </ul>
      </div>
    </Card>
  )
}

function DecisionPanel({ decisions }) {
  const rows = decisions?.decisions || []
  if (rows.length === 0) return null
  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        Decisions this workflow derived
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        The specification asks an implementer to derive what the evidence does not contain and to
        record the derivation. Each one below names the alternative it rejected.
      </p>
      <ul className="mt-4 space-y-3">
        {rows.map((row) => (
          <li key={row.id} className="rounded-sm border border-border-subtle p-3">
            <p className="font-mono text-xs text-muted-foreground">{row.id}</p>
            <p className="mt-1 text-sm font-medium text-foreground">{row.question}</p>
            {row.rejected_because && (
              <p className="mt-1 text-sm text-muted-foreground">{row.rejected_because}</p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function AudiencePage() {
  const [roomId, setRoomId] = useState('')
  const [openLink, setOpenLink] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => audienceApi.vocabulary(), [])
  const board = useAsync(() => audienceApi.summary(roomId), [roomId])
  const groups = useAsync(() => audienceApi.groups(roomId), [roomId])
  const links = useAsync(() => audienceApi.links(roomId), [roomId])
  const decisions = useAsync(() => audienceApi.decisions(), [])

  const onChanged = useCallback(() => {
    // The grid state lives on the server, so the page refetches rather than patching local
    // state. A permissions panel that shows a checkbox in a position the store is not in is the
    // one failure that must not be possible on this screen.
    board.refetch()
    groups.refetch()
    links.refetch()
  }, [board, groups, links])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading audience permissions" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (groups.error) return <ErrorNote error={groups.error} onRetry={groups.refetch} />

  const groupRows = groups.data?.groups || []
  const linkRows = links.data?.links || []

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Audience permissions
        </h1>
        <p className="mt-2 max-w-3xl text-[15px] text-muted-foreground">
          What a named audience can see in a data room, with a separate view and download flag on
          every document and folder. An item without an entry is invisible to that audience, and
          the folders above a granted item are opened so the tree stays navigable. Changes to
          members or permissions apply to the existing link immediately, with no re-sharing.
        </p>
      </header>

      {/* The default, the limitation and the not-proof, before any grid. A permissions page that
          opens with green ticks and no caveat is the reading this workflow exists to prevent. */}
      <Notice tone="warning" title="The default is deny">
        <p>{DEFAULT_DENY}</p>
        <p className="mt-2">{LIMITATION}</p>
        <p className="mt-2">{NOT_PROOF}</p>
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Audiences"
          value={board.data?.groups ?? 0}
          hint={`${board.data?.members ?? 0} member rows`}
          icon="audit"
        />
        <StatCard
          label="Granted nothing"
          value={board.data?.groups_with_no_permissions ?? 0}
          hint="the shipped default"
          icon="search"
        />
        <StatCard
          label="Links"
          value={board.data?.links ?? 0}
          hint={`${board.data?.group_links ?? 0} group-scoped`}
          icon="database"
        />
        <StatCard
          label="Items in the room"
          value={board.data?.items ?? 0}
          hint={`${board.data?.permissions ?? 0} grants across them`}
          icon="schema"
        />
      </div>

      {board.data?.groups_with_no_permissions > 0 && (
        <Notice tone="info" title="Some audiences have been granted nothing">
          {board.data.groups_with_no_permissions} audience(s) see nothing yet:{' '}
          {board.data.groups_with_no_permissions_names?.join(', ')}. That is the state a new
          audience ships in, not a fault.
        </Notice>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Room" id="wf074-room" hint="Leave empty to see every audience.">
          <Select id="wf074-room" value={roomId} onChange={setRoomId}>
            <option value="">Every room</option>
            {(rooms.data?.records || rooms.data || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {roomId && <NewAudienceForm roomId={roomId} onCreated={onChanged} />}

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">Audiences</h2>
        {groups.loading ? (
          <Spinner label="Loading audiences" />
        ) : groupRows.length === 0 ? (
          <EmptyState
            title="No audiences yet"
            description="Create a named audience, add its members or its email domains, then grant it access item by item. It sees nothing until you do."
            action={roomId ? <Button onClick={() => document.getElementById('wf074-new-name')?.focus()}>Name the first one</Button> : undefined}
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {groupRows.map((group) => (
              <GroupPanel key={group.id} group={group} onChanged={onChanged} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">Links</h2>
        {links.loading ? (
          <Spinner label="Loading links" />
        ) : linkRows.length === 0 ? (
          <EmptyState
            title="No links yet"
            description="A group link takes its visibility from its group. A general link carries its own per-item permissions, or shows the whole room when it was never scoped."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {linkRows.map((link) => (
              <div key={link.id} className="space-y-2">
                <Button onClick={() => setOpenLink(openLink === link.id ? '' : link.id)}>
                  {openLink === link.id ? 'Hide' : 'Show'} {link.name}
                </Button>
                {openLink === link.id && <LinkPanel link={link} onChanged={onChanged} />}
              </div>
            ))}
          </div>
        )}
      </section>

      <VocabularyPanel vocabulary={vocabulary.data} />

      <Notice tone="info" title="Who owns the access control list">
        <p>{SCOPE_OWNER}</p>
      </Notice>

      <Notice tone="warning" title="What is assumed rather than sourced">
        <p>{ASSUMPTION}</p>
      </Notice>

      <DecisionPanel decisions={decisions.data} />

      <p className="text-xs text-muted-foreground">
        <Icon path={DOWNLOAD_ICON} size={13} className="mr-1 inline" />
        A link&apos;s own download switch gates the per-item flag, so an item granted can download is
        still view-only on a link that does not allow downloads. Both facts are reported separately
        rather than collapsed into one word. {itemTypeLabel('dataroom_folder')} rows above a granted
        item are opened with view only, never with download.
      </p>
    </div>
  )
}

export default {
  id: 'wf-074-scope-visibility-to-an-audience-with-per-item',
  label: 'Audience permissions',
  // The glyph is not in the shared PATHS map, so `iconPath` carries it and `icon` falls back to
  // the shared mark. `components/ui.jsx` is not edited.
  icon: 'audit',
  iconPath: AUDIENCE_ICON,
  order: 740,
  Component: AudiencePage,
}

export { DOCUMENT_ICON, FOLDER_ICON }
