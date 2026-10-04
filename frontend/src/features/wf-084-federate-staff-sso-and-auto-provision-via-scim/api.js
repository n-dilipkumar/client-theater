/**
 * WF-084's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared
 * `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding rather than a
 * preference. `apiRequest` reads the error body to build a message and then throws it away,
 * so a failure survives as a status plus one string. This workflow has two responses whose
 * body is the whole point:
 *
 *   - a 403 from the callback carries `reason`, `expected_organization_id` and
 *     `actual_organization_id`, which is what lets a login surface say *why* a profile was
 *     refused rather than showing a generic sign-in failure;
 *   - a 401 carries the same shape plus the expired-code policy, because a dead code and a
 *     foreign tenant are different problems with different remedies.
 *
 * The shared client would need three more fields on one function, which is a shared file and
 * a platform decision. Until then the calls that need the body read it themselves. Recorded
 * as promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-084'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path, options) {
  return apiRequest(`${BASE}${path}`, options)
}

/** As `apiRequest`, but keeps the parsed error body on the thrown error. */
async function requestWithBody(path, options) {
  const response = await fetch(`/api${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    // The refusal fields. A caller that cannot name the reason a sign-in was declined cannot
    // tell the user what to do about it, so these reach the component.
    error.reason = body?.reason || null
    error.expectedOrganizationId = body?.expected_organization_id || null
    error.actualOrganizationId = body?.actual_organization_id || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload, headers) {
  return requestWithBody(path, { method, body: JSON.stringify(payload), headers })
}

const encode = encodeURIComponent

export const federationApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the page cannot drift from the rules that validate it:
   * both protocols, the three identifiers and the job each one does, both flows, the
   * ten-minute code bound, the three SCIM operations, the four supported providers and both
   * delivery methods all come from one place on the server.
   */
  vocabulary: () => call('/vocabulary'),

  /**
   * Every judgement call this workflow made, with the alternative it rejected. Served rather
   * than buried in a docstring so a reviewer reads the decision instead of the code.
   */
  decisions: () => call('/decisions'),

  // -- tenants and their connections ---------------------------------------- //

  organizations: (roomId) => call(`/organizations${query({ room_id: roomId })}`),

  createOrganization: (payload) => send('/organizations', 'POST', payload),

  organization: (organizationId) => call(`/organizations/${encode(organizationId)}`),

  createConnection: (organizationId, payload) =>
    send(`/organizations/${encode(organizationId)}/connections`, 'POST', payload),

  connections: (organizationId) => call(`/connections${query({ organization_id: organizationId })}`),

  connection: (connectionId) => call(`/connections/${encode(connectionId)}`),

  // -- signing in ------------------------------------------------------------ //

  /**
   * Build the authorization URL. No session exists when this resolves - the tenant is
   * asserted at the callback - so the caller has nothing to store yet.
   */
  authorize: (payload) => send('/sso/authorize', 'POST', payload),

  /**
   * The callback. Both entry points reach it: a staff-initiated sign-in passes an
   * `authorization_id`, and an IdP-initiated one passes the tenant and the redirect URI the
   * customer's SAML settings put in `RelayState`.
   *
   * A refusal throws with `reason`, `expectedOrganizationId` and `actualOrganizationId` on
   * the error, so the page can name what was wrong rather than showing a bare failure.
   */
  callback: (payload) => send('/sso/callback', 'POST', payload),

  sessions: (organizationId, includeRevoked = true) =>
    call(`/sessions${query({ organization_id: organizationId, include_revoked: includeRevoked })}`),

  session: (sessionId) => call(`/sessions/${encode(sessionId)}`),

  // -- Directory Sync -------------------------------------------------------- //

  directories: (organizationId) => call(`/directories${query({ organization_id: organizationId })}`),

  /**
   * Turn on Directory Sync. The response carries the webhook token **once**; reading the
   * directory afterwards never carries it again.
   */
  createDirectory: (organizationId, payload) =>
    send(`/organizations/${encode(organizationId)}/directories`, 'POST', payload),

  directory: (directoryId) => call(`/directories/${encode(directoryId)}`),

  directoryUsers: (directoryId, includeDeprovisioned = false) =>
    call(`/directories/${encode(directoryId)}/users${query({ include_deprovisioned: includeDeprovisioned })}`),

  directoryGroups: (directoryId) => call(`/directories/${encode(directoryId)}/groups`),

  /**
   * One SCIM change, as a directory provider would post it.
   *
   * The token goes in a header rather than the body. A credential in a body is one an access
   * log captures, and this is the endpoint a stranger would otherwise post to.
   */
  sendEvent: (directoryId, payload, webhookToken) =>
    send(`/directories/${encode(directoryId)}/events`, 'POST', payload, {
      'X-WF084-Webhook-Token': webhookToken,
    }),

  /**
   * The Events API's view of the same changes, for a tenant that pulls rather than posts.
   * Reaches the same records as the webhook rather than a lesser copy of them.
   */
  eventLog: (directoryId, since) =>
    call(`/directories/${encode(directoryId)}/event-log${query({ since })}`),

  // -- access ---------------------------------------------------------------- //

  /** What each directory user is granted, as a function of the groups they are in. */
  access: (directoryId) => call(`/directories/${encode(directoryId)}/access`),

  /**
   * Map one directory group onto one app role. Mapping a group rather than a person is what
   * makes access a function of directory state.
   */
  setAccess: (groupId, role) => send(`/groups/${encode(groupId)}/access`, 'PUT', { role }),

  accessRules: (directoryId) => call(`/access-rules${query({ directory_id: directoryId })}`),
}

/** Every room, so the page can offer one to hang a tenant on. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The one sentence this workflow exists to enforce.
 *
 * The specification says it twice and once in bold: the app must "always validate the
 * returned profile's organization ID", and validating with an email domain "is unsafe as
 * organizations might allow email addresses from outside their corporate domain (e.g. for
 * guest users)". Exported rather than written into the page because a test imports it, and a
 * test that imports a literal checks nothing. The server sends the same sentence with every
 * response, so the page and the API cannot disagree.
 */
export const TENANT_RULE =
  'A session is granted only after the returned profile\'s organization id matches the expected tenant. An email domain is never that check.'

/** The ten-minute bound, quoted from the research rather than paraphrased. */
export const CODE_TTL_RULE = 'An authorization code is valid for ten minutes. After that it is rejected and the user starts a new sign-in. The code is never retried.'

/** Deprovisioning, quoted from the research. It is a removal, not a label. */
export const DEPROVISION_RULE = 'Deprovisioning is a process of removing a user from an app.'

/**
 * Why there is no manual override.
 *
 * The specification motivates the whole workflow with the risk of manual entry being
 * "error-prone and can lead to security vulnerabilities where users get unauthorized access
 * to resources". An override that survived the next directory change would cause exactly
 * the harm the workflow exists to prevent.
 */
export const NO_MANUAL_OVERRIDE =
  'This workflow stores no manual override. The directory is the source of truth, so an access state set by hand here would survive the next directory change and defeat the workflow it appears to satisfy.'

/**
 * The three identifiers and the job each one does.
 *
 * The specification's evidence draws the line: a `connection` is "for SAML or OIDC" and a
 * `provider` "is used for OAuth connections". Writing them here as well as on the server
 * lets the board render before the vocabulary request resolves, so the loading and error
 * states have a defined value rather than a blank card. A test asserts the two agree.
 */
export const IDENTIFIERS = [
  {
    param: 'organization',
    label: 'Organization',
    job: 'identifies the tenant whose users may sign in',
  },
  {
    param: 'connection',
    label: 'Connection',
    job: 'selects one SAML or OIDC connection inside that tenant',
  },
  {
    param: 'provider',
    label: 'Provider',
    job: 'names an OAuth provider; the vendor restricts it to OAuth connections',
  },
]

/** Both entry points into the one callback. */
export const FLOWS = [
  {
    id: 'staff_initiated',
    label: 'Staff initiated',
    detail: 'Staff open the login surface and this app redirects them out.',
  },
  {
    id: 'idp_initiated',
    label: 'IdP initiated',
    detail:
      'The IdP sends the user straight to the callback, carrying the redirect URI as a RelayState in the tenant\'s own SAML settings.',
  },
]

/**
 * The three SCIM operations, with the research's own wording for each.
 *
 * Quoted rather than paraphrased because these are the operations a compliance reader
 * matches against a vendor's documentation.
 */
export const SCIM_OPERATIONS = [
  { id: 'create', label: 'Provisioning an identity for a user (account creation)' },
  { id: 'update', label: "When a user's attribute has changed (account update)" },
  { id: 'delete', label: 'Deprovisioning a user from your app (account deletion)' },
]

/** The four directory providers the research names as supported. */
export const PROVIDERS = [
  { id: 'okta', label: 'Okta' },
  { id: 'microsoft_ad', label: 'Microsoft AD' },
  { id: 'workday', label: 'Workday' },
  { id: 'google_workspace', label: 'Google Workspace' },
]

/** The roles a group mapping can grant. */
export const ROLES = [
  { id: 'admin', label: 'Admin' },
  { id: 'member', label: 'Member' },
  { id: 'auditor', label: 'Auditor' },
]

/**
 * Why a redirect URI is checked against what the tenant registered.
 *
 * An authorization URL carrying an attacker's redirect URI would send the user's
 * authorization code to the attacker, so the list is the tenant's and never the caller's.
 */
export const REDIRECT_URI_RULE =
  'A multi-tenant app normally has one redirect URI. A single-tenant app may have several. A callback only accepts one the tenant registered.'

/** The label for an identifier id, for a screen that has only the id. */
export function identifierLabel(id) {
  return IDENTIFIERS.find((entry) => entry.param === id)?.label || id
}

/** The label for a role id, for a screen that has only the id. */
export function roleLabel(id) {
  if (!id) return 'No access'
  return ROLES.find((entry) => entry.id === id)?.label || id
}

/** The label for a provider id, for a screen that has only the id. */
export function providerLabel(id) {
  return PROVIDERS.find((entry) => entry.id === id)?.label || id
}

/**
 * The sentence for a refusal reason, so the page explains rather than restates a code.
 *
 * The four reasons are the ones the server can return. Each has a different remedy, which is
 * why the server names them at all: a user whose code expired signs in again, and a user
 * whose profile carries the wrong tenant does not.
 */
export const REFUSAL_REMEDIES = {
  tenant_mismatch: {
    title: 'This profile belongs to a different organization',
    detail:
      'The organization id in the profile is not the tenant this room expects. An email domain is not the check, because an organization can allow an address from outside its corporate domain.',
  },
  tenant_absent: {
    title: 'This profile carries no organization id',
    detail:
      'Without an organization id there is nothing to assert the tenant against. The address is not a substitute, which is exactly what the specification warns against.',
  },
  tenant_not_a_member: {
    title: 'This organization is not set up in this room',
    detail:
      'The id matched, but no tenant record here names it. An administrator has to connect the organization before anybody can sign in to it.',
  },
  authorization_code_expired: {
    title: 'That authorization code has expired',
    detail:
      'An authorization code is valid for ten minutes and is never retried. Start a new sign-in.',
  },
}