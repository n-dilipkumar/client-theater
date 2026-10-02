/**
 * Internal workspace roles and least-privilege integration scopes (WF-077).
 *
 * The page is in the order the researched user flow happens, and it is two
 * halves that deliberately do not touch each other:
 *
 * 1. **The member list.** A role dropdown per member, with the three researched
 *    guard rails visible rather than enforced silently: the owner's role is
 *    fixed, the last admin's role is fixed, and a Guest promoted to anything
 *    other than Collaborator is auto-upgraded to a Full seat. The preview is
 *    computed server-side from the *same* function the write uses, so the
 *    refusal an admin reads before clicking is the refusal they will get.
 *
 * 2. **The integration tokens.** Scopes chosen a la carte, with the surface each
 *    one unlocks listed next to it, because "the dashboard's token-creation UI
 *    shows which endpoints each scope unlocks" is the researched behaviour and a
 *    bare list of scope names is not it. The banner at the top of this section is
 *    the sharpest rule in the whole workflow and is stated in the research's own
 *    words: **there is no implicit hierarchy**, so `documents.write` does not
 *    imply `documents.read` and a write-only token is genuinely write-only.
 *
 * Under them: the OAuth consent screen, the directory SSO connection, and the
 * inference table.
 *
 * **Who am I acting as** is a control, not a constant, because the workflow has
 * two kinds of caller and the page exists to show the difference. A person is
 * named with `X-Workspace-Member` and is held to the role rules; a token is
 * presented as a bearer credential and is held to the scope rules. Switching the
 * caller changes what the server will allow, which is the point.
 */

import { useMemo, useState } from 'react'
import { absoluteTime, relativeTime } from '@/lib/api'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Icon,
  Spinner,
  useAsync,
} from '@/components/ui'
import { rolesApi } from './api'
import {
  GUARD_ICON,
  INFERENCE_ICON,
  LEAST_PRIVILEGE_ICON,
  ROLES_ICON,
  SCOPE_ICON,
  SSO_ICON,
} from './icons'
import {
  Checkbox,
  Fact,
  Notice,
  PermissionChips,
  Quote,
  RoleSelect,
  SectionHead,
} from './primitives'

/** One stat tile. */
function Stat({ label, value, hint, glyph }) {
  return (
    <Card className="card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {label}
          </p>
          <p className="mt-2 font-mono text-[1.75rem] leading-none font-semibold text-foreground">
            {value}
          </p>
          {hint && <p className="mt-1.5 truncate text-xs text-muted-foreground">{hint}</p>}
        </div>
        {glyph && (
          <span className="rounded-sm bg-accent-soft p-2 text-accent">
            <Icon path={glyph} size={20} />
          </span>
        )}
      </div>
    </Card>
  )
}

/** Turn a server refusal into something an admin can act on. */
function refusalText(error) {
  if (!error) return ''
  const missing = Array.isArray(error.missing_scopes) ? error.missing_scopes : []
  if (missing.length) {
    return `The token is missing ${missing.join(', ')}. It needs that scope and nothing else will do.`
  }
  if (error.member_forbidden) {
    return `The caller does not hold ${error.member_forbidden}. A scope cannot stand in for a person: scopes do not override a user's defined permissions.`
  }
  return error.message || String(error)
}

/** The caller control: act as a member, or present a token. */
function CallerPicker({ members, as, onChange }) {
  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-56 flex-1">
          <Field label="Acting as" hint="Sent as X-Workspace-Member. The role rules apply.">
            <select
              aria-label="Acting as member"
              value={as.member}
              onChange={(event) => onChange({ ...as, member: event.target.value })}
              className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
            >
              <option value="">No member (anonymous)</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.name || member.email} — {member.role}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="min-w-56 flex-1">
          <Field
            label="Presenting a token"
            hint="Sent as Authorization: Bearer. The scope rules apply."
          >
            <input
              type="password"
              aria-label="Bearer token"
              placeholder="dsr_…  (shown once, when minted)"
              value={as.token}
              onChange={(event) => onChange({ ...as, token: event.target.value })}
              className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 font-mono text-sm text-foreground placeholder:text-muted-foreground/70"
            />
          </Field>
        </div>
      </div>
    </Card>
  )
}

function MemberRow({ member, options, onPreview, onApply, busy, preview, login }) {
  const fixed = member.is_owner
  return (
    <li className="border-b border-border-subtle/15 py-3 last:border-0">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-medium text-foreground">
              {member.name || member.email}
            </span>
            {member.is_owner && <Badge tone="insert">Owner</Badge>}
            {member.is_admin && !member.is_owner && <Badge tone="insert">Admin</Badge>}
            {member.seat === 'guest' && <Badge tone="warning">Guest seat</Badge>}
            {member.auth === 'sso' && <Badge tone="update">Directory sign-in</Badge>}
          </div>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{member.email}</p>
          <div className="mt-2">
            <PermissionChips permissions={member.permissions} />
          </div>
          {login && !login.local_credentials_allowed && (
            <p className="mt-2 text-xs text-destructive">
              Local sign-in refused: {login.reason}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <RoleSelect
            id={`role-${member.id}`}
            value={member.role}
            disabled={fixed || busy}
            options={options}
            onChange={(next) => onPreview(member, next)}
          />
          <Button
            variant="secondary"
            disabled={fixed || busy}
            onClick={() => onApply(member)}
            icon="check"
          >
            Apply
          </Button>
        </div>
      </div>

      {preview && preview.member_id === member.id && (
        <div className="mt-3">
          {preview.accepted ? (
            <Notice tone="info" title="This change is allowed.">
              {preview.reason}
              {preview.seat_changed &&
                ' A Guest promoted to a non-Collaborator role is auto-upgraded to a Full seat.'}
            </Notice>
          ) : (
            <Notice tone="bad" title="This change is refused.">
              <span>{preview.reason}</span>
              {preview.rule && <span className="block pt-1 italic">{preview.rule}</span>}
            </Notice>
          )}
        </div>
      )}

      {!fixed && member.is_admin && (
        <p className="mt-2 text-xs text-muted-foreground">
          Last-admin guard rail: this is checked on every change, and it counts every permission
          rather than the role name.
        </p>
      )}
    </li>
  )
}

function RolesAndScopes() {
  const [workspace, setWorkspace] = useState('')
  const [as, setAs] = useState({ member: '', token: '' })
  const [preview, setPreview] = useState(null)
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState(null)
  const [flash, setFlash] = useState('')
  const [tokenName, setTokenName] = useState('')
  const [chosen, setChosen] = useState(['documents.write'])
  const [minted, setMinted] = useState(null)
  const [consent, setConsent] = useState({ client_id: '', client_name: '', scopes: ['links.write'] })
  const [consentResult, setConsentResult] = useState(null)
  const [sso, setSso] = useState({ protocol: 'oidc', idp_entity_id: '', sso_url: '', domains: '' })

  const catalogue = useAsync(() => rolesApi.vocabulary(), [])
  const summary = useAsync(() => rolesApi.summary(), [])
const workspacesState = useAsync(() => rolesApi.workspaces(), [])

  // Resolved *before* the hooks that depend on it, and as a string id rather
  // than a row. Two silent bugs lived here:
  //
  //   - the hooks below read the raw `workspace` state, which is '' until
  //     somebody touches the picker, so no workspace-scoped request was ever
  //     made and the page rendered an empty member list with no error shown;
  //   - `workspaces[0]` is a row, and interpolating it produced
  //     `/workspaces/[object Object]/members`.
  //
  // `/summary` counts workspaces and `/workspaces` lists them, so the list route
  // is the only one that can seed the picker.
  const workspaces = workspacesState.data?.workspaces || []
  const active = workspace || workspaces[0]?.workspace_id || ''

  const catalogueRoutes = useAsync(() => rolesApi.endpoints(), [])
  const rules = useAsync(() => rolesApi.rules(), [])
  const inferences = useAsync(() => rolesApi.inferences(), [])
  const oauthFlow = useAsync(() => rolesApi.oauthFlow(), [])

  const listState = useAsync(
    () => (active ? rolesApi.workspace(active) : Promise.resolve(null)),
    [active]
  )
  const membersState = useAsync(
    () =>
      active
        ? rolesApi.members(active, { member: as.member, token: as.token })
        : Promise.resolve(null),
    [active, as.member, as.token]
  )
  const tokensState = useAsync(
    () =>
      active
        ? rolesApi.tokens(active, { member: as.member, token: as.token })
        : Promise.resolve(null),
    [active, as.member, as.token]
  )
  const rolesState = useAsync(
    () =>
      active
        ? rolesApi.customRoles(active, { member: as.member, token: as.token })
        : Promise.resolve(null),
    [active, as.member, as.token]
  )
  const ssoState = useAsync(
    () =>
      active ? rolesApi.sso(active, { member: as.member, token: as.token }) : Promise.resolve(null),
    [active, as.member, as.token]
  )
  const chosenKey = chosen.join(',')
  const scopePreview = useAsync(() => rolesApi.scopes(chosenKey), [chosenKey])
  const members = membersState.data?.members || []
  const customRoles = (membersState.data?.custom_roles || []).concat(
    (rolesState.data?.roles || []).map((role) => role.name)
  )
  const tokens = tokensState.data?.tokens || []
  const builtIns = catalogue.data?.internal_roles.built_in || []
  const seatUpgrade = catalogue.data?.internal_roles.seat_upgrade_rule || ''

  // Derived during render rather than stored in state, so the dropdown cannot
  // show a role the server has since removed.
  const roleNames = [...new Set([...builtIns, ...customRoles])].join('|')
  const roleOptions = useMemo(
    () => roleNames.split('|').filter(Boolean).map((name) => ({ value: name, label: name })),
    [roleNames]
  )

  function refresh() {
    membersState.refetch()
    tokensState.refetch()
    rolesState.refetch()
    ssoState.refetch()
    listState.refetch()
  }

  async function run(action, successMessage) {
    setBusy(true)
    setRefusal(null)
    setFlash('')
    try {
      const result = await action()
      setFlash(successMessage)
      refresh()
      return result
    } catch (error) {
      setRefusal(error)
      return null
    } finally {
      setBusy(false)
    }
  }

  async function onPreview(member, role) {
    setPending({ member, role })
    setBusy(true)
    setRefusal(null)
    try {
      const decision = await rolesApi.previewRole(active, member.id, role, {
        member: as.member,
        token: as.token,
      })
      setPreview(decision)
    } catch (error) {
      setRefusal(error)
      setPreview(null)
    } finally {
      setBusy(false)
    }
  }

  async function onApply() {
    if (!pending) return
    const { member, role } = pending
    const result = await run(
      () =>
        rolesApi.changeRole(active, member.id, role, {
          member: as.member,
          token: as.token,
        }),
      `${member.email || member.name} is now ${role}.`
    )
    if (result) {
      setPending(null)
      setPreview(null)
    }
  }

  async function onMint(event) {
    event.preventDefault()
    const result = await run(
      () =>
        rolesApi.mintToken(
          active,
          { name: tokenName, scopes: chosen },
          { member: as.member, token: as.token }
        ),
      `Token "${tokenName}" minted. Its secret is shown once and never again.`
    )
    if (result) {
      setMinted(result)
      setTokenName('')
    }
  }

  async function onAuthorize(event) {
    event.preventDefault()
    const result = await run(
      () =>
        rolesApi.authorize(
          active,
          { client_id: consent.client_id, client_name: consent.client_name, scopes: consent.scopes },
          { member: as.member, token: as.token }
        ),
      `Consent granted. Code expires at ${absoluteTime(result.expires_at)}.`
    )
    if (result) setConsentResult(result)
  }

  async function onWriteSso(event) {
    event.preventDefault()
    await run(
      () =>
        rolesApi.writeSso(
          active,
          {
            protocol: sso.protocol,
            idp_entity_id: sso.idp_entity_id,
            sso_url: sso.sso_url,
            domains: sso.domains,
          },
          { member: as.member, token: as.token }
        ),
      'Directory SSO connection saved.'
    )
  }

  if (catalogue.error) {
    return <ErrorNote error={catalogue.error} onRetry={catalogue.refetch} />
  }
  if (catalogue.loading || summary.loading || workspacesState.loading) {
    return <Spinner label="Loading roles and scopes" />
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      {/* ---- header ---------------------------------------------------- */}
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-sm bg-accent-soft p-2 text-accent">
            <Icon path={ROLES_ICON} size={22} />
          </span>
          <div className="min-w-0">
            <h1 className="font-display text-xl font-semibold text-foreground">
              Internal roles and integration scopes
            </h1>
            <p className="text-[13px] text-muted-foreground">
              Who may administer this workspace, and what each integration is allowed to reach.
            </p>
          </div>
        </div>
      </header>

      <Quote source="Papermark">
        {catalogue.data.integration_scopes.no_implicit_hierarchy}
      </Quote>

      <CallerPicker members={members} as={as} onChange={setAs} />

      {refusal && (
        <div className="rounded-sm border border-destructive/40 bg-destructive/10 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Refused
          </p>
          <p className="mt-1 text-[13px] text-foreground">{refusalText(refusal)}</p>
          {refusal.status && (
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              HTTP {refusal.status}
              {refusal.code ? ` · ${refusal.code}` : ''}
            </p>
          )}
        </div>
      )}
      {flash && <Notice tone="info" title="Done.">{flash}</Notice>}

      {/* ---- stats ----------------------------------------------------- */}
      {listState.data && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Members" value={listState.data.members} glyph={ROLES_ICON} />
          <Stat
            label="Admins"
            value={listState.data.admins}
            hint="excluding the owner"
            glyph={GUARD_ICON}
          />
          <Stat label="Guest seats" value={listState.data.guests} glyph={ROLES_ICON} />
          <Stat label="API tokens" value={listState.data.tokens} glyph={SCOPE_ICON} />
        </div>
      )}

      {/* ---- workspace picker ------------------------------------------ */}
      <Card>
        <Field
          label="Workspace"
          hint={listState.data ? `Plan: ${listState.data.plan}` : 'Every workspace with members.'}
        >
          <select
            aria-label="Workspace"
            value={active}
            onChange={(event) => {
              setWorkspace(event.target.value)
              setPreview(null)
              setPending(null)
            }}
            className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground sm:max-w-sm"
          >
            {workspaces.map((row) => (
              <option key={row.workspace_id} value={row.workspace_id}>
                {row.workspace_id} — {row.plan} — {row.members} members
              </option>
            ))}
          </select>
        </Field>
      </Card>

      {/* ---- the member list ------------------------------------------- */}
      <Card>
        <SectionHead
          title="Workspace members"
          glyph={ROLES_ICON}
          rule={`${rules.data?.caller_rule || ''} ${seatUpgrade}`}
        />

        {membersState.error && (
          <ErrorNote error={membersState.error} onRetry={membersState.refetch} />
        )}
        {membersState.loading && <Spinner label="Loading members" />}

        {!membersState.loading && members.length === 0 && (
          <EmptyState
            title="No members, or the caller may not read them"
            description="An integration token needs the members.read scope. A person needs a role holding members.read. Presenting neither is refused rather than treated as permitted."
          />
        )}

        {members.length > 0 && (
          <ul>
            {members.map((member) => (
              <MemberRow
                key={member.id}
                member={member}
                options={roleOptions}
                busy={busy}
                preview={preview}
                login={member.login}
                onPreview={onPreview}
                onApply={onApply}
              />
            ))}
          </ul>
        )}

        {rules.data?.outcomes && (
          <div className="mt-4">
            <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
              Every refusal this list can produce
            </p>
            <ul className="mt-2 space-y-1">
              {rules.data.outcomes.map((row) => (
                <li key={row.outcome} className="text-xs text-muted-foreground">
                  <span className="font-mono text-foreground">{row.outcome}</span> — {row.meaning}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      {/* ---- custom roles ---------------------------------------------- */}
      {rolesState.data && (
        <Card>
          <SectionHead
            title="Workspace-defined roles"
            glyph={GUARD_ICON}
            rule={`The role field accepts either a built-in role name or the name of a custom role. A custom role grants exactly the permissions it declares.`}
          />
          {rolesState.data.roles.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              This workspace has defined no custom roles, so every member holds a built-in one.
            </p>
          ) : (
            <ul className="space-y-2">
              {rolesState.data.roles.map((role) => (
                <li key={role.id} className="flex flex-wrap items-center gap-3">
                  <span className="min-w-40 text-[13px] font-medium text-foreground">
                    {role.name}
                  </span>
                  <div className="min-w-0 flex-1">
                    <PermissionChips permissions={role.permissions} />
                  </div>
                  <Badge tone={role.is_admin ? 'insert' : 'neutral'}>
                    {role.is_admin ? 'Counts as admin' : `${role.holders} holders`}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {/* ---- tokens ---------------------------------------------------- */}
      <Card>
        <SectionHead
          title="Integration tokens"
          glyph={LEAST_PRIVILEGE_ICON}
          rule={catalogue.data.integration_scopes.no_wildcards}
        />

        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
              Minted tokens
            </p>
            {tokensState.error && <ErrorNote error={tokensState.error} onRetry={tokensState.refetch} />}
            {tokens.length === 0 && !tokensState.loading && (
              <p className="mt-2 text-[13px] text-muted-foreground">
                No tokens, or this caller may not read them.
              </p>
            )}
            <ul className="mt-2 space-y-2">
              {tokens.map((token) => (
                <li key={token.id} className="rounded-sm border border-border-subtle p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-medium text-foreground">{token.name}</span>
                    {token.revoked && <Badge tone="delete">Revoked</Badge>}
                    <span className="font-mono text-[11px] text-muted-foreground">
                      {token.prefix}…
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {token.scopes.map((scope) => (
                      <Badge key={scope} tone="update">
                        {scope}
                      </Badge>
                    ))}
                  </div>
                  <p className="mt-1.5 text-[11px] text-muted-foreground">
                    Minted {relativeTime(token.minted_at)} by {token.minted_by || 'unknown'} on plan{' '}
                    {token.minted_plan || 'unknown'}
                  </p>
                  {!token.revoked && (
                    <Button
                      className="mt-2"
                      variant="secondary"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () =>
                            rolesApi.revokeToken(active, token.id, {
                              member: as.member,
                              token: as.token,
                            }),
                          `Token "${token.name}" revoked.`
                        )
                      }
                    >
                      Revoke
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
              Mint a least-privilege token
            </p>
            <form className="mt-2 flex flex-col gap-3" onSubmit={onMint}>
              <Field label="Token name" hint="So somebody can recognise it in six months.">
                <input
                  aria-label="Token name"
                  required
                  value={tokenName}
                  onChange={(event) => setTokenName(event.target.value)}
                  className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
                />
              </Field>

              <div>
                <p className="text-[13px] font-medium text-foreground">Scopes, chosen à la carte</p>
                <p className="text-xs text-muted-foreground">
                  Each scope is independent. Ticking documents.write does not tick documents.read.
                </p>
                <div className="mt-1">
                  {(catalogue.data.integration_scopes.catalogue || []).map((entry) => (
                    <Checkbox
                      key={entry.scope}
                      label={entry.scope}
                      hint={(entry.unlocks || []).join('; ')}
                      checked={chosen.includes(entry.scope)}
                      onChange={(on) =>
                        setChosen((previous) =>
                          on
                            ? [...previous, entry.scope]
                            : previous.filter((scope) => scope !== entry.scope)
                        )
                      }
                    />
                  ))}
                </div>
              </div>

              {scopePreview.data?.preview?.surfaces && (
                <div className="rounded-sm border border-border-subtle p-3">
                  <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
                    This set unlocks
                  </p>
                  <ul className="mt-1 space-y-0.5">
                    {Object.entries(scopePreview.data.preview.surfaces).map(([scope, targets]) => (
                      <li key={scope} className="text-xs text-foreground">
                        <span className="font-mono">{scope}</span> — {targets.join('; ')}
                      </li>
                    ))}
                    {(scopePreview.data.preview.unlocks || []).map((entry) => (
                      <li key={entry.path} className="text-xs text-foreground">
                        <span className="font-mono">
                          {entry.method} {entry.path}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {scopePreview.data.preview.warning && (
                    <Notice tone="bad" title="Not a scope this product issues.">
                      {scopePreview.data.preview.warning}
                    </Notice>
                  )}
                </div>
              )}

              <Button type="submit" variant="primary" disabled={busy || chosen.length === 0}>
                Mint token
              </Button>
            </form>

            {minted && (
              <Notice tone="warn" title="Shown once. Copy it now.">
                <span className="mt-1 block break-all font-mono">{minted.secret}</span>
              </Notice>
            )}
          </div>
        </div>
      </Card>

      {/* ---- the consent screen ---------------------------------------- */}
      {oauthFlow.data && (
        <Card>
          <SectionHead
            title="Authorize an application"
            glyph={SCOPE_ICON}
            rule={`${oauthFlow.data.consent_title} · code to token exchange at ${oauthFlow.data.token_endpoint} · single use, ${oauthFlow.data.code_ttl_seconds}s`}
          />
          <form className="flex flex-col gap-3" onSubmit={onAuthorize}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Client id">
                <input
                  aria-label="Client id"
                  required
                  value={consent.client_id}
                  onChange={(event) => setConsent({ ...consent, client_id: event.target.value })}
                  className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 font-mono text-sm text-foreground"
                />
              </Field>
              <Field label="Application name">
                <input
                  aria-label="Application name"
                  value={consent.client_name}
                  onChange={(event) => setConsent({ ...consent, client_name: event.target.value })}
                  className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
                />
              </Field>
            </div>
            <Button type="submit" variant="secondary" disabled={busy || !consent.client_id}>
              Show the consent screen
            </Button>
          </form>

          {consentResult?.consent && (
            <div className="mt-4 rounded-sm border border-accent/30 bg-accent-soft/30 p-4">
              <p className="font-display text-[15px] font-semibold text-foreground">
                {consentResult.consent.title}
              </p>
              <p className="text-[13px] text-muted-foreground">
                {consentResult.consent.client_name} is asking for{' '}
                {consentResult.consent.requested_scopes.join(', ')}.
              </p>
              <p className="mt-2 text-xs text-muted-foreground">It would be able to:</p>
              <ul className="mt-1 space-y-0.5">
                {Object.entries(consentResult.consent.surfaces || {}).map(([scope, targets]) => (
                  <li key={scope} className="text-xs text-foreground">
                    <span className="font-mono">{scope}</span> — {targets.join('; ')}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs italic text-muted-foreground">
                {consentResult.consent.grants_nothing_else}
              </p>
            </div>
          )}
        </Card>
      )}

      {/* ---- directory SSO --------------------------------------------- */}
      {ssoState.data && (
        <Card>
          <SectionHead
            title="Directory SSO"
            glyph={SSO_ICON}
            rule="Staff authenticate through the company IdP rather than local credentials."
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Fact label="Configured">
              {ssoState.data.configured ? 'Yes' : 'Not yet'}
            </Fact>
            <Fact label="Enabled">{ssoState.data.enabled ? 'Yes' : 'No'}</Fact>
            <Fact label="Protocol">{ssoState.data.protocol || '—'}</Fact>
            <Fact label="IdP entity id">{ssoState.data.idp_entity_id || '—'}</Fact>
            <Fact label="Domains">{(ssoState.data.domains || []).join(', ') || 'every member'}</Fact>
            <Fact label="Plan">
              {ssoState.data.plan}
              {ssoState.data.entitled ? '' : ` — does not include ${ssoState.data.requires_plan}`}
            </Fact>
          </div>

          {!ssoState.data.entitled && ssoState.data.configured && (
            <div className="mt-3">
              <Notice tone="info" title="This connection survives a downgrade.">
                Reading it keeps working. Creating or changing one is refused on create and update,
                which is the asymmetry the research states.
              </Notice>
            </div>
          )}

          <form className="mt-4 flex flex-col gap-3" onSubmit={onWriteSso}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Protocol">
                <select
                  aria-label="SSO protocol"
                  value={sso.protocol}
                  onChange={(event) => setSso({ ...sso, protocol: event.target.value })}
                  className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
                >
                  <option value="oidc">oidc</option>
                  <option value="saml">saml</option>
                </select>
              </Field>
              <Field label="IdP entity id">
                <input
                  aria-label="IdP entity id"
                  required
                  value={sso.idp_entity_id}
                  onChange={(event) => setSso({ ...sso, idp_entity_id: event.target.value })}
                  className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
                />
              </Field>
              <Field label="SSO URL" hint="Required for saml.">
                <input
                  aria-label="SSO URL"
                  value={sso.sso_url}
                  onChange={(event) => setSso({ ...sso, sso_url: event.target.value })}
                  className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
                />
              </Field>
              <Field label="Domains" hint="Comma separated. Empty reaches every member.">
                <input
                  aria-label="SSO domains"
                  value={sso.domains}
                  onChange={(event) => setSso({ ...sso, domains: event.target.value })}
                  className="min-h-11 w-full rounded-sm border border-border-subtle bg-surface px-3 text-sm text-foreground"
                />
              </Field>
            </div>
            <Button type="submit" variant="secondary" disabled={busy}>
              Wire directory SSO
            </Button>
          </form>
        </Card>
      )}

      {/* ---- inferences ------------------------------------------------- */}
      {inferences.data && (
        <Card>
          <SectionHead
            title="What this build inferred"
            glyph={INFERENCE_ICON}
            rule={inferences.data.note}
          />
          <ul className="space-y-3">
            {inferences.data.inferences.map((entry) => (
              <li key={entry.id} className="border-b border-border-subtle/15 pb-3 last:border-0">
                <p className="text-[13px] font-medium text-foreground">{entry.topic}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{entry.why}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  <span className="font-mono">change it:</span> {entry.change_it} ·{' '}
                  <span className="font-mono">blast radius:</span> {entry.blast_radius}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* ---- the declaration ------------------------------------------- */}
      {catalogueRoutes.data && (
        <Card>
          <SectionHead
            title="What each scope unlocks on this API"
            glyph={SCOPE_ICON}
            rule={catalogueRoutes.data.endpoints.length + ' endpoints, each declaring its own scope.'}
          />
          <ul className="space-y-1">
            {catalogueRoutes.data.endpoints.map((entry) => (
              <li key={`${entry.method} ${entry.path}`} className="flex flex-wrap gap-2 text-xs">
                <span className="font-mono text-foreground">
                  {entry.method} {entry.path}
                </span>
                <span className="text-muted-foreground">
                  needs {entry.scopes.join(' + ')} · reachable by{' '}
                  {(entry.unlocked_by || []).join(', ') || 'nothing else'}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

export default RolesAndScopes