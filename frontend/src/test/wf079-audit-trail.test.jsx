import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuditTrailExport from '@/features/wf-079-export-a-tamper-evident-audit-trail-wi/AuditTrailExport'
import descriptor from '@/features/wf-079-export-a-tamper-evident-audit-trail-wi/index.jsx'

/**
 * Tests for the WF-079 page.
 *
 * Four things a compliance page can get wrong that the backend tests cannot see:
 *
 * * **It renders the gate instead of pretending the gate is not there.** The switcher
 *   has to produce a real 403, because a page that hides the refusal teaches a
 *   reviewer that the export is open.
 * * **It flattens three different facts into one blank.** An observed address, a
 *   sandbox-masked one and one never captured are three states, and the third is the
 *   one that would otherwise be invisible.
 * * **It shows a digest without showing how to check it.** The recipe is the point.
 * * **It cannot complete the report flow.** Request, generate, download - if the ids
 *   are not threaded through, the page looks finished and is not usable.
 */

const BASE = '/wf-079'

const VOCABULARY = {
  vocabulary: {
    workspace_code_floor: 1000,
    codes: [
      { code: 1, name: 'document_created', category: 'lifecycle', origin: 'pandadoc' },
      { code: 43, name: 'document_declined', category: 'lifecycle', origin: 'pandadoc' },
      { code: 47, name: 'verification_kba_passed', category: 'verification', origin: 'pandadoc' },
      { code: 51, name: 'verification_kba_failed', category: 'verification', origin: 'pandadoc' },
    ],
    reserved_gaps: [{ from: 2, to: 5, reason: 'Reserved.' }],
  },
  reports: {
    report_types: ['user_activity', 'document_status', 'sms_activity', 'fax_usage'],
    date_format: 'MM/DD/YYYY',
    max_range_months: 12,
    max_lookback_years: 10,
    delivery: 'one email per requested report type, carrying a link rather than the file',
    report_type_notes: { fax_usage: 'No data source.' },
    error_codes: { date_range_too_long: 'too long' },
  },
  access: {
    reader_role: 'instance_admin',
    reader_label: 'Instance administrator',
    roles: [
      { id: 'viewer', label: 'Viewer', may_read: false },
      { id: 'content_contributor', label: 'Content Contributor', may_read: false },
      { id: 'room_collaborator', label: 'Room Collaborator', may_read: false },
      { id: 'instance_admin', label: 'Instance administrator', may_read: true },
    ],
    sandbox: { env: 'DSR_AUDIT_EXPORT_SANDBOX', masked_ip: 'hidden' },
    denied: { error: 'administrator_required', detail: 'no', remediation: "Pass role='instance_admin'." },
  },
  ip_status: {
    observed: 'an address was attributed to this audit row',
    hidden: 'a sandbox key was presented',
    not_captured: 'no address was ever attributed to this row',
  },
}

const INFERENCES = {
  action_codes: [{ claim: 'Codes 55-58 share one name', basis: 'no source names them' }],
  export: [{ claim: 'Only the socket peer is trusted', basis: 'a header is set by the caller' }],
  reports: [{ claim: 'Generation is a route rather than a job', basis: 'no scheduler here' }],
}

const ROOMS = {
  count: 1,
  records: [
    { id: 'room_1', collection: 'room', data: { name: 'Northwind Traders' } },
    { id: 'room_2', collection: 'room', data: { name: 'Contoso Health' } },
  ],
}

const INTEGRITY = {
  algorithm: 'sha-256',
  domain_separator: 'wf-079-audit-chain-v1',
  hashed_fields: ['seq', 'date_created', 'action_code', 'actor', 'ip_address', 'reason'],
  canonical_form: 'json.dumps({...}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)',
  chain_step: 'sha256("wf-079-audit-chain-v1\\n" + chain(k-1) + "\\n" + digest(k))',
  order: 'entries ascending by seq, the audit log insertion sequence',
  hashed_values: 'the values as exported. A sandbox-masked ip_address is hashed as "hidden"',
  document_hash: 'f'.repeat(64),
  document_hash_basis: {
    does_not_cover: 'document bytes. The cited vendor hashes the signed PDF',
    completeness: 'not proven by any hash over a list: an event never recorded leaves no trace',
  },
  entries: 2,
  head: 'a'.repeat(64),
  verified: true,
}

const ENTRY_OBSERVED = {
  id: 2,
  seq: 2,
  actor: 'dana',
  date_created: '2026-10-02T09:00:00.000+00:00',
  reason: 'Uploaded for the enterprise evaluation.',
  ip_address: '192.0.2.10',
  ip_status: 'observed',
  user_agent: 'curl/8',
  user: { id: 'dana', email: null, email_status: 'unresolved' },
  action: { code: 1, name: 'document_created', category: 'lifecycle', origin: 'pandadoc' },
  verification: null,
  room_id: 'room_1',
  record_id: 'document_1',
  collection: 'document',
  digest: 'b'.repeat(64),
  chain: 'a'.repeat(64),
}

const ENTRY_UNCAPTURED = {
  ...ENTRY_OBSERVED,
  id: 3,
  seq: 3,
  actor: 'system',
  ip_address: null,
  ip_status: 'not_captured',
  user_agent: null,
  reason: 'No request was ever seen for this write.',
  // A rejection, under the code a source names individually: 51 is
  // "recipient verification with kba failed".
  action: { code: 51, name: 'verification_kba_failed', category: 'verification', origin: 'pandadoc' },
  verification: { outcome: 'fail', method: 'kba', code: 51, unallocable: false },
  digest: 'c'.repeat(64),
}

function trail(entries = [ENTRY_OBSERVED, ENTRY_UNCAPTURED], overrides = {}) {
  return {
    room_id: 'room_1',
    record_id: null,
    count: entries.length,
    total: entries.length,
    limit: 25,
    offset: 0,
    has_more: false,
    sandbox: false,
    entries,
    integrity: INTEGRITY,
    note: 'the head covers every entry matching this filter',
    ...overrides,
  }
}

const SUMMARY = {
  room_id: 'room_1',
  scoped_rows: 2,
  audit_log_rows: 4000,
  addresses_not_captured: 1,
  addresses_masked: 0,
  verification_pass: 1,
  verification_fail: 1,
  unclassified: 0,
  read_role: 'instance_admin',
  sandbox: false,
}

const ok = (body, status = 200) => ({ ok: true, status, statusText: 'OK', json: async () => body })

const refused = (status, body) => ({
  ok: false,
  status,
  statusText: 'Error',
  json: async () => body,
})

function stubApi(overrides = {}) {
  const routes = {
    // The core room list, which the page fetches before any of its own routes.
    // Keyed whole-segment: it must not swallow /wf-079/... paths.
    '/records/room': ROOMS,
    [`${BASE}/vocabulary`]: VOCABULARY,
    [`${BASE}/inferences`]: INFERENCES,
    [`${BASE}/rooms/room_1/audit-trail`]: trail(),
    [`${BASE}/rooms/room_1/audit-trail/summary`]: SUMMARY,
    [`${BASE}/rooms/room_1/audit-trail/verify`]: { room_id: 'room_1', anchors: 0, intact: true, checks: [] },
    [`${BASE}/rooms/room_1/audit-trail/anchors`]: { pinned: true, entries: 2, anchor: {} },
    [`${BASE}/reports`]: { count: 0, reports: [] },
    ...overrides,
  }
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  const matches = (path, candidate) =>
    path === candidate || path.startsWith(`${candidate}/`) || path.startsWith(`${candidate}?`)

  const calls = []
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const query = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
    const method = (init.method || 'GET').toUpperCase()
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ path, method, query, body })

    const key = keys.find((candidate) => matches(path, candidate))
    if (!key) throw new Error(`unstubbed request: ${path}`)

    const value = routes[key]
    if (typeof value !== 'function') return ok(value, method === 'POST' ? 201 : 200)
    const answer = value({ path, method, query, body, calls })
    return answer && typeof answer.ok === 'boolean' ? answer : ok(answer, 200)
  })
  return { calls }
}

const lastCallTo = (calls, path) => calls.filter((call) => call.path === path).at(-1)

async function renderPage(overrides = {}) {
  const { calls } = stubApi(overrides)
  const user = userEvent.setup()
  render(<AuditTrailExport />)
  await screen.findByText('Uploaded for the enterprise evaluation.')
  return { user, calls }
}

beforeEach(() => {
  globalThis.fetch = vi.fn()
})

describe('the descriptor', () => {
  it('exports the id the backend feature claims', () => {
    expect(descriptor.id).toBe('wf-079-export-a-tamper-evident-audit-trail-wi')
  })

  it('points at the component the host will render', () => {
    expect(descriptor.Component).toBe(AuditTrailExport)
  })

  it('uses a glyph from the shared set rather than editing it', () => {
    expect(descriptor.icon).toBe('audit')
    expect(descriptor.iconPath).toBeUndefined()
  })
})

describe('the administrator gate', () => {
  it('offers every role from the vocabulary, marking which may read', async () => {
    await renderPage()
    const select = screen.getByLabelText('Caller role')
    const options = within(select).getAllByRole('option')
    expect(options.map((option) => option.textContent)).toHaveLength(4)
    expect(options.some((option) => option.textContent.includes('may read'))).toBe(true)
  })

  it('renders a real refusal rather than hiding it', async () => {
    stubApi({
      // A function, not an object: the stub hands object values back as 200s, so an
      // object refusal would arrive as data and the page would map over an envelope.
      [`${BASE}/rooms/room_1/audit-trail`]: () =>
        refused(403, {
          error: 'administrator_required',
          detail: 'The audit trail export is accessible to authorized workspace administrators only.',
          remediation: "Pass role='instance_admin'.",
          required_role: 'instance_admin',
        }),
    })
    render(<AuditTrailExport />)
    expect(
      await screen.findByText(/accessible to authorized workspace administrators only/),
    ).toBeInTheDocument()
  })

  it('surfaces the remediation the server sent', async () => {
    stubApi({
      [`${BASE}/rooms/room_1/audit-trail`]: () =>
        refused(403, {
          error: 'administrator_required',
          detail: 'Refused.',
        }),
    })
    render(<AuditTrailExport />)
    expect(await screen.findByText(/Pass role='instance_admin'/)).toBeInTheDocument()
  })

  it('sends the chosen role on the read', async () => {
    const { user, calls } = await renderPage()
    await user.selectOptions(screen.getByLabelText('Caller role'), 'viewer')
    await waitFor(() => {
      expect(lastCallTo(calls, `${BASE}/rooms/room_1/audit-trail`).query).toContain('role=viewer')
    })
  })
})

describe('the three address states', () => {
  it('shows an observed address', async () => {
    await renderPage()
    expect(await screen.findByText('192.0.2.10')).toBeInTheDocument()
  })

  it('says not captured rather than showing a blank', async () => {
    await renderPage()
    expect(await screen.findByText('not captured')).toBeInTheDocument()
  })

  it('shows hidden when a sandbox key is presented', async () => {
    stubApi({
      [`${BASE}/rooms/room_1/audit-trail`]: trail([
        { ...ENTRY_OBSERVED, ip_address: 'hidden', ip_status: 'hidden' },
        ENTRY_UNCAPTURED,
      ]),
    })
    render(<AuditTrailExport />)
    expect(await screen.findByText('hidden')).toBeInTheDocument()
  })

  it('sends the sandbox flag rather than masking client-side', async () => {
    const { user, calls } = await renderPage()
    await user.click(screen.getByLabelText('Present a sandbox key'))
    await waitFor(() => {
      expect(lastCallTo(calls, `${BASE}/rooms/room_1/audit-trail`).query).toContain('sandbox=true')
    })
  })
})

describe('the trail', () => {
  it('renders the integer code and its name, because reviews query by code', async () => {
    await renderPage()
    expect(await screen.findByText('document_created')).toBeInTheDocument()
    expect(await screen.findByText('verification_kba_failed')).toBeInTheDocument()
  })

  it('offers the code filter from the vocabulary, not a hard-coded list', async () => {
    await renderPage()
    const select = screen.getByLabelText('Action code')
    const labels = within(select).getAllByRole('option').map((option) => option.textContent)
    expect(labels[0]).toBe('Every code')
    expect(labels.some((label) => label.includes('document_declined'))).toBe(true)
  })

  it('sends the code filter the operator chose', async () => {
    const { user, calls } = await renderPage()
    await user.selectOptions(screen.getByLabelText('Action code'), '43')
    await waitFor(() => {
      expect(lastCallTo(calls, `${BASE}/rooms/room_1/audit-trail`).query).toContain('action=43')
    })
  })

  it('shows the scope against the size of the whole log', async () => {
    await renderPage()
    // A reviewer seeing two rows must be able to tell scoped from broken.
    expect(await screen.findByText('of 4000 in the whole log')).toBeInTheDocument()
  })

  it('reports that an address was never captured', async () => {
    await renderPage()
    expect(await screen.findByText('Addresses not captured')).toBeInTheDocument()
  })

  it('shows rejections beside passes', async () => {
    await renderPage()
    expect(await screen.findByText('Checks rejected')).toBeInTheDocument()
    expect(await screen.findByText('Checks passed')).toBeInTheDocument()
  })

  it('says when a filter matched nothing', async () => {
    stubApi({ [`${BASE}/rooms/room_1/audit-trail`]: trail([]) })
    render(<AuditTrailExport />)
    expect(await screen.findByText('Nothing in this scope')).toBeInTheDocument()
  })
})

describe('the integrity panel', () => {
  it('shows the chain head', async () => {
    await renderPage()
    expect(await screen.findByText(INTEGRITY.head)).toBeInTheDocument()
  })

  it('shows how to recompute the digest, not only the answer', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByText('How to recompute this yourself'))
    expect(await screen.findByText(/sort_keys=True/)).toBeInTheDocument()
    expect(screen.getByText(/ascending by seq/)).toBeInTheDocument()
  })

  it('says the digest hashes the value as exported', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByText('How to recompute this yourself'))
    expect(await screen.findByText(/sandbox-masked ip_address is hashed as/)).toBeInTheDocument()
  })

  it('reproduces the vendor substitution rather than implying a PDF', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByText('How to recompute this yourself'))
    expect(await screen.findByText(/hashes the signed PDF/)).toBeInTheDocument()
  })

  it('says a hash cannot prove the trail is complete', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByText('How to recompute this yourself'))
    expect(await screen.findByText(/leaves no trace/)).toBeInTheDocument()
  })

  it('offers a pin control, because a fresh export only agrees with itself', async () => {
    const { user, calls } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Pin this head' }))
    await waitFor(() => {
      const post = calls.find((call) => call.path.endsWith('/audit-trail/anchors') && call.method === 'POST')
      expect(post).toBeTruthy()
    })
  })

  it('reports that verifying against zero anchors compared nothing', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: 'Verify against anchors' }))
    // "Intact: true" over an empty list would claim a verification that never ran.
    expect(await screen.findByText(/No anchors pinned yet/)).toBeInTheDocument()
  })

  it('reports a checked anchor as intact', async () => {
    stubApi({
      [`${BASE}/rooms/room_1/audit-trail/verify`]: {
        room_id: 'room_1',
        anchors: 1,
        intact: true,
        checks: [
          {
            anchor_id: 'a1',
            head_at_anchor: 'a'.repeat(64),
            head_now: 'a'.repeat(64),
            first_divergent: null,
          },
        ],
      },
    })
    render(<AuditTrailExport />)
    await screen.findByText('Uploaded for the enterprise evaluation.')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Verify against anchors' }))
    expect(await screen.findByText(/1 anchor\(s\) checked. All intact./)).toBeInTheDocument()
  })

  it('names the first divergent entry when an anchor fails', async () => {
    stubApi({
      [`${BASE}/rooms/room_1/audit-trail/verify`]: {
        room_id: 'room_1',
        anchors: 1,
        intact: false,
        checks: [
          {
            anchor_id: 'a1',
            head_at_anchor: 'a'.repeat(64),
            head_now: 'd'.repeat(64),
            first_divergent: { seq: 7, reason: "this entry's chain no longer matches" },
          },
        ],
      },
    })
    render(<AuditTrailExport />)
    await screen.findByText('Uploaded for the enterprise evaluation.')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Verify against anchors' }))
    expect(await screen.findByText(/DIVERGENCE FOUND/)).toBeInTheDocument()
    expect(screen.getByText(/first divergent seq 7/)).toBeInTheDocument()
  })
})

describe('the CSV report flow', () => {
  const pending = {
    id: 'report_1',
    report_type: 'user_activity',
    state: 'pending',
    start_date: '09/02/2026',
    end_date: '10/02/2026',
    email: 'compliance@example.com',
    row_count: undefined,
    note: null,
    delivery: null,
  }

  const ready = {
    ...pending,
    state: 'ready',
    row_count: 2,
    sha256: 'e'.repeat(64),
    note: undefined,
    delivery: {
      to: 'compliance@example.com',
      token: 'tok-123',
      url: '/api/wf-079/reports/report_1/download?token=tok-123',
      carries: 'a download link, not the file',
    },
  }

  it('states the researched date bounds on the form', async () => {
    await renderPage()
    expect(await screen.findByText(/at most twelve months/)).toBeInTheDocument()
    expect(screen.getByText(/ten years back/)).toBeInTheDocument()
  })

  it('uses MM/DD/YYYY text fields, because the API rejects ISO', async () => {
    await renderPage()
    const start = await screen.findByLabelText('Start date')
    // A `type="date"` control would hand back YYYY-MM-DD, which this API answers
    // invalid_date_format to. The attribute is absent because text is the default.
    expect(start).not.toHaveAttribute('type', 'date')
    expect(start.value).toMatch(/^\d{2}\/\d{2}\/\d{4}$/)
  })

  it('sends the requested types as a list, so one-per-type is expressible', async () => {
    const { user, calls } = await renderPage({
      [`${BASE}/reports`]: ({ method }) =>
        method === 'POST' ? ok({ accepted: true, requested: 2, reports: [] }, 202) : ok({ count: 0, reports: [] }),
    })
    await user.click(screen.getByLabelText('document_status'))
    await user.click(screen.getByRole('button', { name: 'Request reports' }))
    await waitFor(() => {
      const post = calls.find((call) => call.path === `${BASE}/reports` && call.method === 'POST')
      expect(post).toBeTruthy()
      expect(post.body.report_type).toEqual(['user_activity', 'document_status'])
    })
  })

  it('shows the requested range and row count for a report', async () => {
    await renderPage({ [`${BASE}/reports`]: { count: 1, reports: [ready] } })
    expect(await screen.findByText(/09\/02\/2026 to 10\/02\/2026/)).toBeInTheDocument()
    expect(screen.getByText(/2 row\(s\)/)).toBeInTheDocument()
  })

  it('says a type with no data source has none, rather than hiding it', async () => {
    await renderPage({
      [`${BASE}/reports`]: {
        count: 1,
        reports: [{ ...pending, report_type: 'fax_usage', note: 'No data source. Exported empty.' }],
      },
    })
    expect(await screen.findByText(/No data source/)).toBeInTheDocument()
  })

  it('generates a pending report on request', async () => {
    const { user, calls } = await renderPage({
      [`${BASE}/reports`]: { count: 1, reports: [pending] },
    })
    await user.click(screen.getByRole('button', { name: 'Generate' }))
    await waitFor(() => {
      const post = calls.find((call) => call.path === `${BASE}/reports/report_1/generate`)
      expect(post.method).toBe('POST')
    })
  })

  it('links to the download the delivery record issued, with its token', async () => {
    await renderPage({ [`${BASE}/reports`]: { count: 1, reports: [ready] } })
    const link = await screen.findByRole('link', { name: 'Download CSV' })
    expect(link.getAttribute('href')).toContain('token=tok-123')
    expect(link.getAttribute('href')).toContain('role=instance_admin')
  })

  it('says the notification carries a link rather than the file', async () => {
    await renderPage({ [`${BASE}/reports`]: { count: 1, reports: [ready] } })
    expect(await screen.findByText(/a download link, not the file/)).toBeInTheDocument()
  })

  it('shows the recorded digest so a recipient can check the file', async () => {
    await renderPage({ [`${BASE}/reports`]: { count: 1, reports: [ready] } })
    // Both the integrity panel and a report row mention a digest, and the row
    // renders its range and count in one paragraph, so match the row by its digest
    // line rather than by the range.
    const digest = await screen.findByText(
      (_content, node) =>
        node?.children?.length === 0 &&
        node.textContent?.includes('09/02/2026 to 10/02/2026') &&
        node.textContent.includes('sha256'),
    )
    expect(digest).toBeInTheDocument()
    expect(digest.textContent).toMatch(/2 row\(s\)/)
  })
})

describe('the inferences', () => {
  it('publishes what the build had to decide rather than describing it in prose', async () => {
    await renderPage()
    await userEvent.setup().click(screen.getByText('What this build had to decide, and why'))
    expect(await screen.findByText('Codes 55-58 share one name')).toBeInTheDocument()
    expect(screen.getByText('Only the socket peer is trusted')).toBeInTheDocument()
    expect(screen.getByText('Generation is a route rather than a job')).toBeInTheDocument()
  })

  it('gives every inference a basis to disagree with', async () => {
    await renderPage()
    await userEvent.setup().click(screen.getByText('What this build had to decide, and why'))
    expect(await screen.findByText('no source names them')).toBeInTheDocument()
  })
})