import { useCallback, useMemo, useState } from 'react'

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
  CODE_TTL_RULE,
  DEPROVISION_RULE,
  FLOWS,
  IDENTIFIERS,
  NO_MANUAL_OVERRIDE,
  PROVIDERS,
  REDIRECT_URI_RULE,
  REFUSAL_REMEDIES,
  ROLES,
  SCIM_OPERATIONS,
  TENANT_RULE,
  federationApi,
  listRooms,
  roleLabel,
} from './api'
import {
  AccessBadge,
  AssertionBadge,
  EventBadge,
  Notice,
  Select,
  SessionState,
} from './primitives'

/**
 * WF-084: federate staff SSO and auto-provision via SCIM.
 *
 * The page has four jobs, in this order, and the order is the design.
 *
 * **Say what the assertion checks before showing that anything passed.** The whole
 * specification turns on one rule: the returned profile's organization id is asserted
 * against the expected tenant, and an email domain never is. A security page that opens with
 * green ticks and no caveat is the failure this workflow's own research warns against, so the
 * rule is the first thing rendered, above the fold, and the server sends the same sentence
 * with every response.
 *
 * **Show a group-to-rule mapping as the only thing that grants access.** The research says
 * directory groups "create groups that inform access rules", and access is "a function of
 * directory state rather than manual admin action". A page with a per-person access control
 * would draw a shape the specification rules out, so the access table has a role column and
 * no edit affordance on a person.
 *
 * **Show a deprovision as a removal.** "Deprovisioning is a process of removing a user from
 * an app", and the issue is explicit that it must "remove access, not merely mark the user
 * inactive in a way a session check ignores". So a revoked session is shown as revoked, with
 * the reason, rather than hidden from the list.
 *
 * **Render every state this page can be in.** Loading, error, empty. A governance page that
 * goes blank when the API is down looks like the directory stopped syncing, which is the one
 * reading that must never be possible.
 */

const CALLBACK_EXAMPLE = 'https://app.example/wf-084/callback'

/** One sign-in, run end to end, with the refusal explained when it is declined. */
function useSignIn() {
  const [busy, setBusy] = useState(false)
  const [granted, setGranted] = useState(null)
  const [refusal, setRefusal] = useState(null)

  const run = useCallback(async (payload) => {
    setBusy(true)
    setRefusal(null)
    setGranted(null)
    try {
      const session = await federationApi.callback(payload)
      setGranted(session)
      return { ok: true, session }
    } catch (error) {
      // The server names the reason and both organization ids, so the page can explain the
      // refusal instead of restating a status code. That is the whole reason this call does
      // not go through the shared client's throwaway error.
      setRefusal({
        ...(REFUSAL_REMEDIES[error.reason] || {
          title: 'That sign-in was not accepted',
          detail: String(error.message || error),
        }),
        reason: error.reason,
        expected: error.expectedOrganizationId,
        actual: error.actualOrganizationId,
        status: error.status,
      })
      return { ok: false }
    } finally {
      setBusy(false)
    }
  }, [])

  const reset = useCallback(() => {
    setGranted(null)
    setRefusal(null)
  }, [])

  return { busy, granted, refusal, run, reset }
}

function TenantPanel({ tenant, organizations, connections }) {
  const [organizationId, setOrganizationId] = useState('')
  const [redirectUri, setRedirectUri] = useState(CALLBACK_EXAMPLE)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await federationApi.createOrganization({
        organization_id: organizationId,
        name: organizationId,
        redirect_uris: [redirectUri],
        single_tenant: redirectUri.length === 0,
      })
      setResult(created)
    } catch (err) {
      setError(err.errors || { organization_id: String(err.message || err) })
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const mine = organizations.filter(
    (row) => row.organization_id === tenant.organization_id,
  )

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        Tenants and their connections
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        A tenant is what a profile&apos;s organization id is asserted against. It is recorded
        here by an administrator rather than created on first sight, because an id the
        assertion merely compares would let any provider the app trusts mint a session for a
        tenant nobody set up.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field
          label="Organization id"
          id="wf084-org-id"
          hint="The value a profile carries. Never an email domain."
        >
          <input
            id="wf084-org-id"
            className={inputClass}
            value={organizationId}
            placeholder="org_northwind"
            onChange={(event) => setOrganizationId(event.target.value)}
          />
        </Field>
        <Field label="Redirect URI" id="wf084-redirect" hint={REDIRECT_URI_RULE}>
          <input
            id="wf084-redirect"
            className={inputClass}
            value={redirectUri}
            onChange={(event) => setRedirectUri(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-3">
        <Button variant="primary" disabled={busy || !organizationId} onClick={create}>
          {busy ? 'Saving' : 'Record this tenant'}
        </Button>
      </div>

      {error && (
        <div className="mt-3">
          <Notice tone="destructive" title="That tenant was not accepted">
            <ul className="list-disc space-y-0.5 pl-4">
              {Object.entries(error).map(([field, message]) => (
                <li key={field}>
                  <span className="font-mono">{field}</span>: {message}
                </li>
              ))}
            </ul>
          </Notice>
        </div>
      )}

      {result && (
        <div className="mt-3">
          <Notice tone="success" title="The tenant is recorded">
            <p>
              <span className="font-mono">{result.organization_id}</span> now has{' '}
              {result.max_redirect_uris} redirect URI slot(s) and {mine.length} connection(s)
              recorded against it.
            </p>
          </Notice>
        </div>
      )}

      <div className="mt-5 border-t border-border-subtle pt-4">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          The three identifiers, and the job each one does
        </p>
        <ul className="mt-2 space-y-1.5">
          {IDENTIFIERS.map((entry) => (
            <li key={entry.param} className="text-sm text-muted-foreground">
              <span className="font-mono text-xs text-foreground">{entry.param}</span>:{' '}
              {entry.job}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted-foreground">
          The research keeps these apart: a connection is for SAML or OIDC, and a provider is
          for OAuth. A sign-in names exactly one of them.
        </p>
        {connections.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {connections.map((connection) => (
              <li key={connection.id} className="text-sm text-muted-foreground">
                <span className="font-mono text-xs text-foreground">{connection.name}</span>{' '}
                <Badge tone="neutral">{connection.protocol}</Badge>{' '}
                <span className="font-mono text-xs">{connection.redirect_uri}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

function SignInPanel({ tenant }) {
  const signIn = useSignIn()
  const [email, setEmail] = useState('')
  const [profileTenant, setProfileTenant] = useState(tenant.organization_id)

  const submit = async () => {
    await signIn.run({
      organization: tenant.organization_id,
      profile: {
        organization_id: profileTenant,
        emails: [{ address: email || 'staff@northwind.example' }],
      },
    })
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        Sign in, and what the callback refuses
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Run a sign-in against the callback. Change the organization id the profile carries and
        watch the session not be granted: the assertion compares one field, and the email
        domain below never enters the decision.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Field
          label="Profile organization id"
          id="wf084-profile-org"
          hint="Change this to org_globex to see the refusal."
        >
          <input
            id="wf084-profile-org"
            className={inputClass}
            value={profileTenant}
            onChange={(event) => setProfileTenant(event.target.value)}
          />
        </Field>
        <Field
          label="Profile email"
          id="wf084-profile-email"
          hint="Kept on the tenant's own domain throughout, so the address never explains a refusal."
        >
          <input
            id="wf084-profile-email"
            className={inputClass}
            value={email}
            placeholder="staff@northwind.example"
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={signIn.busy || !profileTenant}
          onClick={submit}
        >
          {signIn.busy ? 'Checking' : 'Run this sign-in'}
        </Button>
        {(signIn.granted || signIn.refusal) && (
          <Button onClick={signIn.reset}>Clear</Button>
        )}
      </div>

      {signIn.refusal && (
        <div className="mt-3">
          <Notice tone="destructive" title={signIn.refusal.title}>
            <p>{signIn.refusal.detail}</p>
            <div className="mt-2 space-y-0.5 font-mono text-xs">
              <p>reason: {signIn.refusal.reason || 'unreported'}</p>
              <p>status: {signIn.refusal.status}</p>
              <p>expected: {signIn.refusal.expected || 'none supplied'}</p>
              <p>returned: {signIn.refusal.actual || 'none returned'}</p>
            </div>
          </Notice>
        </div>
      )}

      {signIn.granted && (
        <div className="mt-3">
          <Notice tone="success" title="The tenant was asserted and a session was granted">
            <p className="flex flex-wrap items-center gap-2">
              <AssertionBadge asserted />
              <span className="font-mono text-xs">{signIn.granted.organization_id}</span>
            </p>
            <p className="mt-2 font-mono text-xs">
              {signIn.granted.email} · flow {signIn.granted.flow} · code bound checked:{' '}
              {String(signIn.granted.code_bound_checked)}
            </p>
          </Notice>
        </div>
      )}

      <div className="mt-4 border-t border-border-subtle pt-3">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Both entry points, one callback
        </p>
        <ul className="mt-2 space-y-1.5">
          {FLOWS.map((flow) => (
            <li key={flow.id} className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{flow.label}</span>: {flow.detail}
            </li>
          ))}
        </ul>
      </div>
    </Card>
  )
}

function DirectoryPanel({ directory, onChanged }) {
  const [role, setRole] = useState('member')
  const [busyGroup, setBusyGroup] = useState('')
  const [error, setError] = useState(null)

  const users = useAsync(() => federationApi.directoryUsers(directory.id), [directory.id])
  const groups = useAsync(() => federationApi.directoryGroups(directory.id), [directory.id])
  const access = useAsync(() => federationApi.access(directory.id), [directory.id])

  const mapRole = async (groupId) => {
    setBusyGroup(groupId)
    setError(null)
    try {
      await federationApi.setAccess(groupId, role)
      await Promise.all([groups.refetch(), access.refetch(), onChanged()])
    } catch (err) {
      setError(err.errors || { role: String(err.message || err) })
    } finally {
      setBusyGroup('')
    }
  }

  const groupRows = groups.data?.groups || []
  const accessRows = access.data?.users || []
  const unmapped = access.data?.unmapped_groups || []

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-foreground">
            {directory.name}
          </h2>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{directory.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="update">{directory.provider_label}</Badge>
          <Badge tone="neutral">{directory.chosen_delivery_method}</Badge>
        </div>
      </div>

      <p className="mt-2 text-sm text-muted-foreground">{directory.directory_is_source_of_truth}</p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
              Directory users
            </p>
            <Button icon="refresh" onClick={users.refetch}>
              Refresh
            </Button>
          </div>
          {users.loading ? (
            <Spinner label="Loading directory users" />
          ) : users.error ? (
            <ErrorNote error={users.error} onRetry={users.refetch} />
          ) : accessRows.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">
              The directory has named nobody yet. A joiner appears here the first time the
              provider posts a create.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-border-subtle">
              {accessRows.map((user) => (
                <li key={user.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-foreground">
                      {user.name || user.external_id}
                    </span>
                    <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                      {user.external_id}
                      {user.groups.length > 0 ? ` · ${user.groups.join(', ')}` : ' · no groups'}
                    </span>
                  </span>
                  <AccessBadge role={user.access.role} />
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Groups, and what each one grants
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            A directory group is an input to an access rule, not a label. Mapping a group
            rather than a person is what makes access a function of directory state.
          </p>

          <div className="mt-3">
            <Field label="Role to grant" id="wf084-role" hint={NO_MANUAL_OVERRIDE}>
              <Select id="wf084-role" value={role} onChange={setRole}>
                {ROLES.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          {groups.loading ? (
            <Spinner label="Loading directory groups" />
          ) : groupRows.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">
              No groups yet. The provider posts them, and each one's members are derived from
              the users rather than copied in.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-border-subtle">
              {groupRows.map((group) => {
                const rule = (access.data?.rules || []).find(
                  (entry) => entry.group_id === group.id,
                )
                return (
                  <li key={group.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-foreground">{group.name}</span>
                      <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                        {group.external_id} · {group.member_count} active member(s)
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <AccessBadge role={rule?.role} />
                      <Button
                        disabled={busyGroup === group.id}
                        onClick={() => mapRole(group.id)}
                      >
                        {busyGroup === group.id ? 'Saving' : `Grant ${roleLabel(role)}`}
                      </Button>
                    </span>
                  </li>
                )
              })}
            </ul>
          )}

          {error && (
            <div className="mt-3">
              <Notice tone="destructive" title="That mapping was not accepted">
                {String(error.role || error.message || error)}
              </Notice>
            </div>
          )}
        </div>
      </div>

      {unmapped.length > 0 && (
        <div className="mt-4">
          <Notice tone="warning" title="Some users are in the directory and in no rule">
            <p className="font-mono text-xs">{unmapped.join(', ')}</p>
            <p className="mt-1">
              These groups are real directory groups that no rule maps, so everybody in them is
              granted nothing. Map the group above, or add the user in the directory itself.
            </p>
          </Notice>
        </div>
      )}
    </Card>
  )
}

function EventLogPanel({ directory }) {
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const pull = async () => {
    setBusy(true)
    setError(null)
    try {
      setResult(await federationApi.eventLog(directory.id))
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        The Events API, for a tenant that polls
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        The research offers two delivery paths and chooses neither: updates can arrive as
        webhooks or be read from an Events API. Both reconcile through the same code, so a
        polling tenant reaches the same records rather than a lesser copy of them.
      </p>

      <div className="mt-3">
        <Button icon="refresh" disabled={busy} onClick={pull}>
          {busy ? 'Reading' : 'Read the event log'}
        </Button>
      </div>

      {error && (
        <div className="mt-3">
          <Notice tone="destructive" title="The event log could not be read">
            {error}
          </Notice>
        </div>
      )}

      {result && (
        <div className="mt-3">
          <Notice
            tone={result.count > 0 ? 'info' : 'neutral'}
            title={`${result.count} event(s) applied, ${result.skipped} already applied`}
          >
            <p>
              Delivered by <span className="font-mono">{result.delivery}</span>. The cursor is{' '}
              <span className="font-mono">{result.cursor || 'none'}</span>, and re-reading a
              window applies nothing twice: a deprovision cannot be applied twice, and a
              joiner cannot be lost.
            </p>
          </Notice>
        </div>
      )}
    </Card>
  )
}

function SessionPanel({ sessions }) {
  if (sessions.length === 0) {
    return (
      <EmptyState
        title="No sessions yet"
        description="A session appears here once a profile's organization id has been asserted against the expected tenant. A refused sign-in writes nothing at all."
      />
    )
  }

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">Sessions</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        A revoked session is shown rather than hidden. Deprovisioning removes a user from the
        app, and a log that dropped the removed sessions would make the removal look like it
        never happened.
      </p>
      <ul className="mt-4 divide-y divide-border-subtle">
        {sessions.map((session) => (
          <li key={session.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span className="min-w-0">
              <span className="block font-mono text-sm text-foreground">{session.email || 'no email'}</span>
              <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                {session.organization_id} · {session.flow} · {session.granted_at}
              </span>
            </span>
            <span className="flex flex-wrap items-center gap-2">
              <AssertionBadge asserted />
              <SessionState live={session.live} revoked={session.revoked} />
            </span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function VocabularyPanel({ vocabulary, decisions }) {
  const protocols = vocabulary?.protocols || []
  const operations = vocabulary?.scim_operations || []
  const hosted = vocabulary?.replaces_hosted_surface || []
  const decisionRows = decisions?.decisions || []

  return (
    <Card>
      <h2 className="font-display text-lg font-semibold text-foreground">
        What this workflow enforces
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        Fetched from the server that validates it, so the page cannot drift from the rules.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Protocols
          </p>
          <ul className="mt-2 space-y-1">
            {protocols.map((row) => (
              <li key={row.id} className="text-sm text-muted-foreground">
                <span className="font-mono text-xs text-foreground">{row.id}</span>:{' '}
                {row.description}
              </li>
            ))}
          </ul>

          <p className="mt-4 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            The three SCIM operations
          </p>
          <ul className="mt-2 space-y-1">
            {operations.map((row) => (
              <li key={row.id} className="flex items-start gap-2 text-sm text-muted-foreground">
                <EventBadge operation={row.id} />
                <span>{row.label}</span>
              </li>
            ))}
          </ul>

          <p className="mt-4 text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Directory providers
          </p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {PROVIDERS.map((provider) => (
              <li key={provider.id}>
                <Badge tone="neutral">{provider.label}</Badge>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            The hosted surfaces this build does not ship
          </p>
          <ul className="mt-2 space-y-2">
            {hosted.map((row) => (
              <li key={row.surface} className="rounded-sm border border-border-subtle p-3">
                <p className="text-sm font-medium text-foreground">{row.surface}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Owned by the {row.owner}. This build offers: {row.this_build_offers}
                </p>
              </li>
            ))}
          </ul>

          <div className="mt-4">
            <JsonView
              value={{
                authorization_code_ttl_minutes: vocabulary?.authorization_code_ttl_minutes,
                redirect_uri: vocabulary?.redirect_uri,
                directory_groups: vocabulary?.directory_groups,
              }}
            />
          </div>
        </div>
      </div>

      {decisionRows.length > 0 && (
        <div className="mt-5 border-t border-border-subtle pt-4">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            Decisions this workflow derived, and what each one rejected
          </p>
          <ul className="mt-2 space-y-2">
            {decisionRows.map((row) => (
              <li key={row.id} className="rounded-sm border border-border-subtle p-3">
                <p className="font-mono text-xs text-muted-foreground">{row.id}</p>
                <p className="mt-1 text-sm font-medium text-foreground">{row.question}</p>
                {row.rejected_because && (
                  <p className="mt-1 text-sm text-muted-foreground">{row.rejected_because}</p>
                )}
                {row.jev_audit_id && (
                  <p className="mt-1 font-mono text-xs text-accent">
                    Jev {row.jev_verdict} at {row.jev_confidence} · audit {row.jev_audit_id}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

function FederationPage() {
  const [roomId, setRoomId] = useState('')

  const rooms = useAsync(() => listRooms(), [])
  const vocabulary = useAsync(() => federationApi.vocabulary(), [])
  const board = useAsync(() => federationApi.summary(roomId), [roomId])
  const tenants = useAsync(() => federationApi.organizations(roomId), [roomId])
  const connections = useAsync(() => federationApi.connections(), [])
  const directories = useAsync(() => federationApi.directories(), [])
  const sessions = useAsync(() => federationApi.sessions(), [])
  const decisions = useAsync(() => federationApi.decisions(), [])

  // Memoised rather than a bare `|| []`, so its identity is stable across renders. A fresh
  // empty array on every render would make the `useMemo` below recompute on every render,
  // which is the defect `react-hooks/exhaustive-deps` warns about here.
  const organizationRows = useMemo(() => tenants.data?.organizations || [], [tenants.data])
  const [selected, setSelected] = useState('')

  const tenant = useMemo(() => {
    const found = organizationRows.find((row) => row.organization_id === selected)
    return found || organizationRows[0] || null
  }, [organizationRows, selected])

  const onChanged = useCallback(() => {
    // The directory state lives on the server, so the page refetches rather than patching
    // local state. A governance panel showing an access role the store does not hold is the
    // one failure that must not be possible.
    board.refetch()
    directories.refetch()
    sessions.refetch()
  }, [board, directories, sessions])

  if (vocabulary.loading || rooms.loading) return <Spinner label="Loading staff SSO" />
  if (vocabulary.error) return <ErrorNote error={vocabulary.error} onRetry={vocabulary.refetch} />
  if (tenants.error) return <ErrorNote error={tenants.error} onRetry={tenants.refetch} />

  const directoryRows = directories.data?.directories || []
  const sessionRows = sessions.data?.sessions || []
  const vocab = vocabulary.data

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-semibold text-foreground">Staff SSO</h1>
        <p className="mt-2 max-w-3xl text-[15px] text-muted-foreground">
          Connect a company&apos;s identity provider over SAML or OIDC, then assert the returned
          profile&apos;s organization id against the expected tenant before any session exists.
          Turn on Directory Sync and the directory provider becomes the source of truth for
          staff: joiners are provisioned, role changes update the account, leavers are
          deprovisioned and lose their sessions, and directory groups map to access rules.
        </p>
      </header>

      {/* The rule first, above the fold. A green tick with no caveat is the reading this
          workflow's own research forbids. */}
      <Notice tone="warning" title="What the tenant assertion checks, and what it never checks">
        <p>{TENANT_RULE}</p>
        <p className="mt-2">{CODE_TTL_RULE}</p>
        <p className="mt-2">{DEPROVISION_RULE}</p>
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Tenants"
          value={board.data?.organizations ?? 0}
          hint={`${board.data?.connections ?? 0} connection(s)`}
          icon="audit"
        />
        <StatCard
          label="Directories"
          value={board.data?.directories ?? 0}
          hint="webhook delivery"
          icon="database"
        />
        <StatCard
          label="Live sessions"
          value={board.data?.live_sessions ?? 0}
          hint={`${board.data?.revoked_sessions ?? 0} revoked`}
          icon="schema"
        />
        <StatCard
          label="Deprovisioned"
          value={board.data?.deprovisioned_users ?? 0}
          hint="removed from the app"
          icon="trash"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Room" id="wf084-room" hint="Leave empty to see every tenant.">
          <Select id="wf084-room" value={roomId} onChange={setRoomId}>
            <option value="">Every room</option>
            {(rooms.data?.records || rooms.data || []).map((room) => (
              <option key={room.id} value={room.id}>
                {room.data?.name || room.id}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Tenant" id="wf084-tenant" hint="The tenant the rest of the page reports on.">
          <Select id="wf084-tenant" value={tenant?.organization_id || ''} onChange={setSelected}>
            {organizationRows.length === 0 && <option value="">No tenant recorded</option>}
            {organizationRows.map((row) => (
              <option key={row.organization_id} value={row.organization_id}>
                {row.name || row.organization_id}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {tenant && (
        <>
          <TenantPanel
            tenant={tenant}
            organizations={organizationRows}
            connections={(connections.data?.connections || []).filter(
              (row) => row.organization_id === tenant.organization_id,
            )}
          />
          <SignInPanel tenant={tenant} />
        </>
      )}

      {!tenant && (
        <EmptyState
          title="No tenant recorded yet"
          description="Record a tenant above. A profile's organization id is asserted against it, and a profile from a tenant nobody recorded is refused."
        />
      )}

      <section className="space-y-4">
        <h2 className="font-display text-lg font-semibold text-foreground">
          Directory Sync, and what the directory says
        </h2>
        {directories.loading ? (
          <Spinner label="Loading directories" />
        ) : directories.error ? (
          <ErrorNote error={directories.error} onRetry={directories.refetch} />
        ) : directoryRows.length === 0 ? (
          <EmptyState
            title="No directory connected"
            description="Turn on Directory Sync for a tenant and its provider becomes the source of truth for staff. The webhook token is returned once, when the directory is created."
          />
        ) : (
          directoryRows.map((directory) => (
            <div key={directory.id} className="space-y-4">
              <DirectoryPanel directory={directory} onChanged={onChanged} />
              <EventLogPanel directory={directory} />
            </div>
          ))
        )}
      </section>

      {sessions.loading ? (
        <Spinner label="Loading sessions" />
      ) : sessions.error ? (
        <ErrorNote error={sessions.error} onRetry={sessions.refetch} />
      ) : (
        <SessionPanel sessions={sessionRows} />
      )}

      <VocabularyPanel vocabulary={vocab} decisions={decisions.data} />

      <Notice tone="info" title="Why there is no manual access override">
        <p>{NO_MANUAL_OVERRIDE}</p>
      </Notice>
    </div>
  )
}

export default {
  id: 'wf-084-federate-staff-sso-and-auto-provision-via-scim',
  label: 'Staff SSO',
  // The glyph is not in the shared PATHS map, so `iconPath` carries it and `icon` falls
  // back to the shared mark. `components/ui.jsx` is not edited.
  icon: 'audit',
  iconPath: 'M12 2l8 4v6c0 4.9-3.4 8.5-8 9.5-4.6-1-8-4.6-8-9.5V6l8-4zM9 12l2 2 4-4',
  order: 840,
  Component: FederationPage,
}

export { SCIM_OPERATIONS }