/**
 * WF-077's HTTP surface, in one place.
 *
 * Every call goes through the shared `apiRequest` escape hatch from
 * `src/lib/api.js` and every path is under this feature's own prefix. Nothing
 * here adds a method to the shared `api` object, which is what keeps that file
 * stable while a hundred features each talk to their own routes.
 *
 * The `X-Workspace-Member` header is the half of the workflow that is about
 * people. The API accepts two credentials and this page has to be able to send
 * either one to show the difference:
 *
 *   - **A member** (this header). The role rules apply: who may change whose
 *     role, and the owner's and last admin's roles are fixed.
 *   - **A token** (`Authorization: Bearer`). The scope rules apply, and there is
 *     no implicit hierarchy, so a write-only token cannot read.
 *
 * `callAs` and `token` are therefore threaded through every function rather than
 * baked in, because the point of the page is to show what each credential can and
 * cannot do, and a client that hard-coded one of them could not.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-077'

/** Headers for a request made as `member`, optionally presenting `token`. */
function headersFor(member, token) {
  const headers = {}
  if (member) headers['X-Workspace-Member'] = member
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

function call(path, options = {}) {
  const { member, token, ...rest } = options
  return apiRequest(`${BASE}${path}`, {
    ...rest,
    headers: { ...headersFor(member, token), ...(rest.headers || {}) },
  })
}

export const rolesApi = {
  summary: () => apiRequest(`${BASE}/summary`),
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),
  rules: () => apiRequest(`${BASE}/rules`),
  endpoints: () => apiRequest(`${BASE}/endpoints`),
  scopes: (scopes = '') =>
    apiRequest(`${BASE}/scopes${scopes ? `?scopes=${encodeURIComponent(scopes)}` : ''}`),
  inferences: () => apiRequest(`${BASE}/inferences`),
  oauthFlow: () => apiRequest(`${BASE}/oauth-flow`),

  workspaces: () => apiRequest(`${BASE}/workspaces`),
  workspace: (id) => apiRequest(`${BASE}/workspaces/${encodeURIComponent(id)}`),

  members: (id, options) => call(`/workspaces/${encodeURIComponent(id)}/members`, options),
  member: (id, memberId) => call(`/workspaces/${encodeURIComponent(id)}/members/${memberId}`),
  previewRole: (id, memberId, role, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/members/${memberId}/preview-role`, {
      ...options,
      method: 'POST',
      body: JSON.stringify({ role }),
    }),
  changeRole: (id, memberId, role, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/members/${memberId}/role`, {
      ...options,
      method: 'PATCH',
      body: JSON.stringify({ role }),
    }),
  changeSeat: (id, memberId, seat, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/members/${memberId}/seat`, {
      ...options,
      method: 'PATCH',
      body: JSON.stringify({ seat }),
    }),
  localLogin: (id, memberId, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/members/${memberId}/local-login`, options),

  customRoles: (id, options) => call(`/workspaces/${encodeURIComponent(id)}/role-definitions`, options),
  createCustomRole: (id, payload, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/role-definitions`, {
      ...options,
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  tokens: (id, options) => call(`/workspaces/${encodeURIComponent(id)}/tokens`, options),
  mintToken: (id, payload, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/tokens`, {
      ...options,
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  revokeToken: (id, tokenId, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/tokens/${tokenId}`, { ...options, method: 'DELETE' }),

  authorize: (id, payload, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/oauth/authorize`, {
      ...options,
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  accessToken: (id, payload, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/oauth/access-token`, {
      ...options,
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  sso: (id, options) => call(`/workspaces/${encodeURIComponent(id)}/sso`, options),
  writeSso: (id, payload, options) =>
    call(`/workspaces/${encodeURIComponent(id)}/sso`, {
      ...options,
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  audit: (id, options) => call(`/workspaces/${encodeURIComponent(id)}/audit`, options),
}