/**
 * The page, tested against real API payloads.
 *
 * These tests exist for the parts of the page where a mistake would be
 * *believable* rather than visibly broken: a token list that quietly implied
 * `documents.write` grants read, a guard rail that is enforced on write but not
 * explained on screen, and a credential switcher that looks like it changed the
 * caller's identity when it did not.
 *
 * Every assertion is about something the research fixes, not about markup.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import RolesAndScopes from './RolesAndScopes'

const VOCABULARY = {
  internal_roles: {
    built_in: ['Admin', 'Manager', 'Member', 'Collaborator'],
    seat_upgrade_rule:
      'a `Guest` promoted to a non-`Collaborator` role is auto-upgraded to a Full seat',
  },
  integration_scopes: {
    catalogue: [
      { scope: 'documents.write', label: 'Write documents', unlocks: ['upload a document'] },
      { scope: 'documents.read', label: 'Read documents', unlocks: ['list documents'] },
      { scope: 'members.read', label: 'Read members', unlocks: ['list members'] },
    ],
    no_wildcards: "Don't request `*` or wildcards; they're not supported.",
    no_implicit_hierarchy:
      '`documents.write` does **not** imply `documents.read`. Each scope is independent',
  },
}

const MEMBERS = {
  workspace_id: 'northwind',
  count: 3,
  built_in_roles: ['Admin', 'Manager', 'Member', 'Collaborator'],
  custom_roles: ['Deal Desk'],
  seats: ['full', 'guest'],
  members: [
    {
      id: 'm1',
      name: 'Priya Raman',
      email: 'owner@northwind.example',
      role: 'Owner',
      seat: 'full',
      auth: 'local',
      is_owner: true,
      is_admin: false,
      permissions: ['members.read', 'members.write'],
    },
    {
      id: 'm2',
      name: 'Dana Okafor',
      email: 'dana@northwind.example',
      role: 'Admin',
      seat: 'full',
      auth: 'sso',
      is_owner: false,
      is_admin: true,
      permissions: ['members.read', 'members.write', 'part11.read'],
    },
    {
      id: 'm3',
      name: 'Noor Haddad',
      email: 'noor@northwind.example',
      role: 'Collaborator',
      seat: 'guest',
      auth: 'local',
      is_owner: false,
      is_admin: false,
      permissions: ['members.read'],
    },
  ],
}

const RULES = {
  caller_rule: 'You must be an organization admin, a workspace admin, or hold a role',
  outcomes: [
    { outcome: 'owner_role_immutable', meaning: "The workspace owner's role is fixed." },
    { outcome: 'last_admin_role_immutable', meaning: 'The last admin cannot be demoted.' },
  ],
}

const calls = []
let handlers = {}

/** One route's canned answer. `ok: false` gives a refusal with a status. */
function json(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

beforeEach(() => {
  calls.length = 0
  handlers = {}
  // `fetch(url, options)` - two arguments, and the shared client prefixes `/api`,
  // so the key is the path with that prefix stripped. The query is kept apart
  // because the scope preview route is the one this page calls with one.
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    calls.push({ url, path, method, headers: init.headers || {}, body: init.body })
    const handler = handlers[`${method} ${path}`]
    if (handler) return handler({ path, method, body: init.body })
    const fallback = DEFAULTS[path]
    if (fallback === undefined) throw new Error(`unstubbed request: ${method} ${path}`)
    return json(fallback)
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

const DEFAULTS = {
  '/wf-077/vocabulary': VOCABULARY,
  '/wf-077/summary': { workspaces: 2, workspace_ids: ['northwind', 'contoso'] },
  '/wf-077/workspaces': {
    count: 2,
    workspaces: [
      { workspace_id: 'northwind', plan: 'business', members: 3, admins: 1, tokens: 2, sso_configured: true },
      { workspace_id: 'contoso', plan: 'starter', members: 2, admins: 1, tokens: 0, sso_configured: true },
    ],
  },
  '/wf-077/rules': RULES,
  '/wf-077/inferences': { count: 1, note: 'n', inferences: [] },
  '/wf-077/oauth-flow': { consent_title: 'Authorize Application', token_endpoint: 'x', code_ttl_seconds: 600 },
  '/wf-077/endpoints': {
    endpoints: [
      {
        method: 'GET',
        path: '/api/wf-077/workspaces/{workspace_id}/members',
        name: 'List workspace members',
        scopes: ['members.read'],
        unlocked_by: ['members.read'],
      },
    ],
  },
  '/wf-077/workspaces/northwind/members': MEMBERS,
  '/wf-077/workspaces/northwind/tokens': {
    workspace_id: 'northwind',
    tokens: [
      {
        id: 't1',
        name: 'Ingestion worker (write-only)',
        scopes: ['documents.write'],
        revoked: false,
        prefix: 'dsr_demo',
        minted_at: '2026-10-01T09:00:00Z',
        minted_by: 'dana',
        minted_plan: 'business',
      },
    ],
  },
  '/wf-077/workspaces/northwind/role-definitions': {
    workspace_id: 'northwind',
    roles: [{ id: 'r1', name: 'Deal Desk', permissions: ['members.read'], holders: 1, is_admin: false }],
  },
  '/wf-077/workspaces/northwind/sso': {
    configured: true,
    enabled: true,
    protocol: 'oidc',
    idp_entity_id: 'https://idp',
    domains: ['northwind.example'],
    plan: 'starter',
    entitled: false,
    requires_plan: 'enterprise',
  },
}

/**
 * Render and wait for the workspace-scoped data, which is what almost every
 * assertion here is about.
 *
 * The header appears on the first catalogue response, but the member list, tokens
 * and SSO all arrive after the workspace picker resolves, so a test that stops at
 * the header asserts against a page that is still loading. The explicit timeout is
 * because this page fires nine requests on mount and several are chained.
 */
async function renderPage() {
  const user = userEvent.setup()
  render(<RolesAndScopes />)
  await screen.findByText('Internal roles and integration scopes')
  await screen.findByText('Priya Raman', undefined, { timeout: 10_000 })
  return user
}

/**
 * Render and wait for the *member read* to have failed.
 *
 * Used by the two tests that stub the member route to fail on purpose: waiting for
 * a member row there would wait for something that will never arrive, and the
 * test would time out having proved nothing.
 *
 * It waits for `ErrorNote`, not the page's own "Refused" panel, and the
 * distinction is real rather than cosmetic: a *loader* that fails surfaces as
 * `ErrorNote` next to the section it belongs to, while a refused *action* surfaces
 * in the panel at the top. Two different failure shapes, so two different tests.
 */
async function renderPageExpectingMemberReadFailure() {
  const user = userEvent.setup()
  render(<RolesAndScopes />)
  await screen.findByText('Internal roles and integration scopes')
  await screen.findByText('Could not load data', undefined, { timeout: 10_000 })
  return user
}

describe('the header', () => {
  it('states the no-implicit-hierarchy rule in the research wording', async () => {
    await renderPage()
    expect(
      screen.getByText(/documents\.write.*does \*\*not\*\* imply.*documents\.read/i)
    ).toBeTruthy()
  })

  it('shows the four built-in role names the research fixes', async () => {
    await renderPage()
    // One dropdown per member, so this is deliberately the *first* one.
    const select = screen.getAllByLabelText('Workspace role')[0]
    const values = within(select)
      .getAllByRole('option')
      .map((option) => option.value)
    expect(values).toEqual(expect.arrayContaining(['Admin', 'Manager', 'Member', 'Collaborator']))
    // And the workspace's own custom role is offered beside them.
    expect(values).toContain('Deal Desk')
  })
})

describe('the member list', () => {
  it('names the owner and refuses to let their role move', async () => {
    const user = await renderPage()
    const row = screen.getByText('Priya Raman').closest('li')
    const select = within(row).getByLabelText('Workspace role')
    expect(select.disabled).toBe(true)
    expect(within(row).getByRole('button', { name: /Apply/ }).disabled).toBe(true)
    await user.click(within(row).getByRole('button', { name: /Apply/ }))
    expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(0)
  })

  it('shows the seat auto-upgrade rule in the section heading', async () => {
    await renderPage()
    expect(screen.getByText(/auto-upgraded to a Full seat/i)).toBeTruthy()
  })

  it('marks a guest seat, because that is what the promotion rule keys on', async () => {
    await renderPage()
    expect(screen.getByText('Guest seat')).toBeTruthy()
  })

  it('marks a member who authenticates through the directory', async () => {
    await renderPage()
    expect(screen.getByText('Directory sign-in')).toBeTruthy()
  })

  it('explains the last-admin rail on an admin who is not the owner', async () => {
    await renderPage()
    expect(screen.getByText(/Last-admin guard rail/)).toBeTruthy()
  })

  it('previews a refused change and sends the refusal wording, not a status', async () => {
    const user = await renderPage()
    handlers['POST /wf-077/workspaces/northwind/members/m2/preview-role'] = async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        outcome: 'last_admin_role_immutable',
        accepted: false,
        member_id: 'm2',
        reason: 'this is the last member with admin privileges',
        rule: 'The role of the last member with admin privileges cannot be changed.',
        seat_changed: false,
      }),
    })
    // Index 1 is Dana, the admin who is not the owner. Index 0 is the owner, whose
// dropdown is disabled by the guard rail.
    await user.selectOptions(screen.getAllByLabelText('Workspace role')[1], 'Member')
    await waitFor(() =>
      expect(screen.getByText(/This change is refused\./)).toBeTruthy()
    )
    // Both halves: the plain-language reason and the sourced rule it comes from.
    expect(screen.getByText('this is the last member with admin privileges')).toBeTruthy()
    expect(
      screen.getByText('The role of the last member with admin privileges cannot be changed.')
    ).toBeTruthy()
    // The Apply button is still there: refusing is not the page's decision.
    expect(screen.getAllByRole('button', { name: /Apply/ }).length).toBeGreaterThan(0)
  })

  it('reports the seat upgrade in the preview before it is applied', async () => {
    const user = await renderPage()
    handlers['POST /wf-077/workspaces/northwind/members/m3/preview-role'] = async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        outcome: 'accepted',
        accepted: true,
        member_id: 'm3',
        current_seat: 'guest',
        resulting_seat: 'full',
        seat_changed: true,
        reason: 'Collaborator -> Admin',
      }),
    })
    const selects = screen.getAllByLabelText('Workspace role')
    await user.selectOptions(selects[2], 'Admin')
    await waitFor(() => expect(screen.getByText('This change is allowed.')).toBeTruthy())
    // Scoped to Noor's row: the seat rule is also printed in the section heading,
    // so an unscoped query matches two nodes and the test passes for the wrong one.
    const row = screen.getByText('Noor Haddad').closest('li')
    expect(within(row).getByText(/auto-upgraded to a Full seat/i)).toBeTruthy()
  })
})

describe('the caller control', () => {
  it('sends the acting member as the header the server reads', async () => {
    const user = await renderPage()
    await user.selectOptions(screen.getByLabelText('Acting as member'), 'm2')
    await waitFor(() => {
      const last = calls[calls.length - 1]
      expect(last.headers['X-Workspace-Member']).toBe('m2')
    })
  })

  it('shows the vendor sentence verbatim when a read is refused', async () => {
    // The researched requirement is the wording a client matches on, so this
    // asserts the sentence rather than a status code.
    handlers['GET /wf-077/workspaces/northwind/members'] = async () =>
      json(
        {
          error: 'forbidden',
          code: 'forbidden',
          status: 403,
          detail:
            "The token is valid, but doesn't have the scope the endpoint requires *or* isn't authorized to act on the team you're addressing.",
          missing_scopes: ['members.read'],
          member_forbidden: '',
        },
        403
      )
    await renderPageExpectingMemberReadFailure()
    expect(
      screen.getByText(/doesn't have the scope the endpoint requires/)
    ).toBeTruthy()
  })

  it('cannot tell the two 403 causes apart, because the shared client drops the body', async () => {
    // Recorded rather than worked around. `apiRequest` in src/lib/api.js builds
    // its Error from `body.detail || body.error` and copies only `.status`, so
    // `missing_scopes` and `member_forbidden` - the two fields that would
    // distinguish a missing scope from a missing permission - never reach the
    // page. Both causes render the same sentence, which is what the research
    // says the vendor does too, so nothing is *wrong* on screen; the page is
    // simply not able to be more precise than the server was.
    const detail = 'the same sentence either way'
    handlers['GET /wf-077/workspaces/northwind/members'] = async () =>
      json({ code: 'forbidden', status: 403, detail, missing_scopes: ['members.read'] }, 403)
    await renderPageExpectingMemberReadFailure()
    expect(screen.getByText(detail)).toBeTruthy()
  })
})

describe('the token surface', () => {
  it('shows a write-only token as write-only, with no read scope on it', async () => {
    await renderPage()
    const card = screen.getByText('Ingestion worker (write-only)').closest('li')
    expect(within(card).getByText('documents.write')).toBeTruthy()
    expect(within(card).queryByText('documents.read')).toBeNull()
  })

  it('states the no-wildcard rule above the minting form', async () => {
    await renderPage()
    expect(screen.getAllByText(/wildcards; they're not supported/i).length).toBeGreaterThan(0)
  })

  it('offers every catalogue scope as its own independent choice', async () => {
    await renderPage()
    expect(screen.getByLabelText('Token name')).toBeTruthy()
    for (const entry of VOCABULARY.integration_scopes.catalogue) {
      expect(screen.getByRole('checkbox', { name: new RegExp(entry.scope) })).toBeTruthy()
    }
  })

  it('does not tick documents.read when documents.write is ticked', async () => {
    const user = await renderPage()
    const write = screen.getByRole('checkbox', { name: /documents\.write/ })
    expect(write.checked).toBe(true)
    expect(screen.getByRole('checkbox', { name: /documents\.read/ }).checked).toBe(false)
    await user.click(screen.getByRole('checkbox', { name: /documents\.read/ }))
    expect(screen.getByRole('checkbox', { name: /documents\.read/ }).checked).toBe(true)
  })

  it('sends exactly the ticked scopes, and nothing inferred', async () => {
    const user = await renderPage()
    handlers['POST /wf-077/workspaces/northwind/tokens'] = async () =>
      json({ secret: 'dsr_once', shown_once: true })
    await user.type(screen.getByLabelText('Token name'), 'worker')
    await user.click(screen.getByRole('button', { name: 'Mint token' }))
    await waitFor(() =>
      expect(
        calls.some((entry) => entry.method === 'POST' && entry.path.endsWith('/tokens'))
      ).toBe(true)
    )
    const mint = calls.find((entry) => entry.method === 'POST' && entry.path.endsWith('/tokens'))
    // documents.write was ticked by default; documents.read was not, and the
    // payload must not acquire it on the way out.
    expect(JSON.parse(mint.body)).toEqual({ name: 'worker', scopes: ['documents.write'] })
  })

  it('shows a minted secret once, as a warning rather than as a success', async () => {
    const user = await renderPage()
    handlers['POST /wf-077/workspaces/northwind/tokens'] = async () => ({
      ok: true,
      status: 200,
      json: async () => ({ secret: 'dsr_shown_once', shown_once: true }),
    })
    await user.type(screen.getByLabelText('Token name'), 'worker')
    await user.click(screen.getByRole('button', { name: 'Mint token' }))
    await waitFor(() => expect(screen.getByText('dsr_shown_once')).toBeTruthy())
    expect(screen.getByText(/Shown once/)).toBeTruthy()
  })

  it('reports a refused mint with the status and the server sentence', async () => {
    const user = await renderPage()
    handlers['POST /wf-077/workspaces/northwind/tokens'] = async () =>
      json(
        {
          code: 'forbidden_plan_feature',
          status: 403,
          detail: "plan 'starter' does not include 'tokens'",
        },
        403
      )
    await user.type(screen.getByLabelText('Token name'), 'worker')
    await user.click(screen.getByRole('button', { name: 'Mint token' }))
    await waitFor(() => expect(screen.getByText(/does not include 'tokens'/)).toBeTruthy())
    // The status rides on the error because the shared client keeps it.
    expect(screen.getByText('HTTP 403')).toBeTruthy()
    // The byte-stable `code` does NOT: `apiRequest` in src/lib/api.js builds the
    // Error from `body.detail || body.error` and carries only `.status`, so a
    // page cannot show the code the research calls "safe to switch on". Recorded
    // rather than worked around, because the fix belongs in the shared client.
    expect(screen.queryByText(/forbidden_plan_feature/)).toBeNull()
  })
})

describe('directory SSO', () => {
  it('shows that a downgraded workspace keeps the connection it already wired', async () => {
    await renderPage()
    expect(screen.getByText(/survives a downgrade/)).toBeTruthy()
    expect(screen.getByText(/does not include enterprise/)).toBeTruthy()
  })

  it('says reading keeps working while writing is refused', async () => {
    await renderPage()
    expect(screen.getByText(/Reading it keeps working/)).toBeTruthy()
  })
})

describe('the refusal table and the declaration', () => {
  it('lists every refusal the member list can produce, by name', async () => {
    await renderPage()
    expect(screen.getByText('owner_role_immutable')).toBeTruthy()
    expect(screen.getByText('last_admin_role_immutable')).toBeTruthy()
  })

  it('names the scope each endpoint requires and what else reaches it', async () => {
    await renderPage()
    // The row renders method and path as one text node, so the match is a substring
// rather than an exact string.
    expect(
      screen.getByText(/\/api\/wf-077\/workspaces\/\{workspace_id\}\/members/)
    ).toBeTruthy()
    expect(screen.getByText(/needs members\.read/)).toBeTruthy()
    // And it says the coarse grant is not a way in.
    expect(screen.getByText(/reachable by members\.read/)).toBeTruthy()
  })
})

describe('registration', () => {
  it('never calls a route outside its own prefix', async () => {
    await renderPage()
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    for (const call of calls) {
      expect(call.path.startsWith('/wf-077/')).toBe(true)
    }
  })

  it('reads the vocabulary from the server rather than compiling it into the page', async () => {
    await renderPage()
    expect(calls.some((call) => call.path === '/wf-077/vocabulary')).toBe(true)
  })
})