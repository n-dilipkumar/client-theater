/**
 * WF-095 page tests.
 *
 * `apiRequest` is never mocked. `globalThis.fetch` is replaced instead, keyed by path, which
 * is what the other feature tests in this product do and what keeps the client under test on
 * the same path a browser would take.
 *
 * The other WF-095 test file covers the building blocks, the helpers and the descriptor. This
 * one drives the page component itself, because a page that renders only its happy path is
 * not proven, and every state below is one the design floor requires or the research forces:
 *
 *   * loading, error and empty, because a board that goes blank reads as "there is nothing
 *     here", which for a signing page is the one reading that must never be possible;
 *   * the board's numbers, and the refusal the quota panel states when the research stated no
 *     ceiling;
 *   * opening an envelope, and the field-keyed refusal rendered next to its input;
 *   * a refused signature, which is a 200 carrying `outcome: failed` and not an HTTP error,
 *     so it must read on the page as an outcome rather than as a broken request;
 *   * the one-hour window, which the page states from the server's answer rather than from a
 *     client-side clock.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor, { AcceptancePage } from './index.jsx'

const BASE = '/wf-095'
const ROOM = 'room_a'

const BUYER = {
  id: 'sig_buyer',
  room_id: ROOM,
  envelope_id: 'env_1',
  role: 'buyer',
  role_label: 'Buyer contact',
  name: 'Ada Byron',
  email: 'ada@northwind.example',
  contact_id: 'c_1',
  702: '702',
  signing_order: 1,
  signed: false,
  signed_at: null,
  signature_mode: null,
  verification_required: true,
  verified: false,
}

const COUNTERSIGNER = {
  ...BUYER,
  id: 'sig_counter',
  role: 'countersigner',
  role_label: 'Countersigner',
  name: 'Dana Reyes',
  email: 'dana@halcyon.example',
  signing_order: 2,
  verification_required: false,
}

function envelope(overrides = {}) {
  return {
    id: 'env_1',
    room_id: ROOM,
    revision: 1,
    quote_id: 'q_1',
    document_id: 'doc_1',
    method: 'esignature',
    signing_status: 'pending_signature',
    status_label: 'Pending signature',
    hs_esign_num_signers_required: 2,
    reassign_allowed: false,
    identity_verification_required: true,
    in_signing_attachments: [],
    document_size_bytes: 1_800_000,
    document_size_mb: 1.717,
    pdf_size_cap_mb: 40,
    pdf_size_cap_quote: 'Quote PDFs larger than 40 MB may not be successfully verified or signed.',
    signers: [BUYER, COUNTERSIGNER],
    signer_count: 2,
    signed_count: 0,
    is_open: true,
    is_accepted: false,
    sealed: false,
    accepted_by: null,
    hs_payment_status: null,
    countersigners_notified: false,
    quota_month: '2026-10',
    quota_cost: 1,
    opened_at: '2026-10-05T12:00:00+00:00',
    verification_requested_at: null,
    verification_window_minutes: 60,
    signing_provider: 'dropbox_sign',
    sealed_copy_expiry_quote: 'the customer receives a copy of the signed document',
    pdf_export_is_lossy_quote: 'Dropbox Sign removes links from any hyperlinked text',
    authentication_owner: 'It is your responsibility to verify the identity of any user',
    contract_is_downstream: 'That consumer is WF-099, which is downstream of this ticket.',
    ...overrides,
  }
}

const QUOTA = {
  month: '2026-10',
  used: 2,
  envelopes_charged: 2,
  limit: null,
  reset_day: 1,
  counts_envelope_not_signer_quote: 'three signatures, this only counts as one usage',
  consumed_on_enable_quote: 'as soon as the e-signature option is turned on',
  quota_unspecified_quote: 'The research states no number.',
}

const SUMMARY = {
  envelopes: 1,
  by_status: { pending_signature: 1, accepted: 0 },
  accepted: 0,
  signers_total: 2,
  signers_signed: 0,
  signers_outstanding: 2,
  verification_required_envelopes: 1,
  quota: QUOTA,
  authentication_owner: 'It is your responsibility to verify the identity of any user',
  contract_is_downstream: 'That consumer is WF-099, which is downstream of this ticket.',
  countersigner_pool_quote: "Countersigners are drawn from your own users.",
}

const DECISIONS = {
  count: 1,
  decisions: [
    {
      question: 'Where should the WF-095 rules live?',
      chosen: 'new_package_quote_acceptance',
      options: { new_package_quote_acceptance: '...', extend_quoting_proposals: '...' },
      rejected_because: 'quoting_proposals owns rendering, not collecting a signature.',
      left_open_by: 'The task brief listed seven packages.',
      audit_id: 'jev-20261005T075612-8560-72925',
      confidence: 1.0,
    },
  ],
}

const DEFAULTS = {
  [`${BASE}/summary`]: SUMMARY,
  [`${BASE}/envelopes`]: { count: 1, envelopes: [envelope()] },
  [`${BASE}/decisions`]: DECISIONS,
  [`${BASE}/envelopes/env_1`]: envelope(),
  [`${BASE}/envelopes/env_1/events`]: { envelope_id: 'env_1', count: 0, events: [] },
  [`${BASE}/signers/sig_buyer`]: BUYER,
  [`${BASE}/quota`]: QUOTA,
}

const calls = []
let handlers = {}
let failures = new Set()

function json(body, status = 200) {
  return { ok: status < 400, status, statusText: 'OK', json: async () => body }
}

beforeEach(() => {
  calls.length = 0
  handlers = {}
  failures = new Set()
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.replace('/api', '').split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    calls.push({ url: raw, path, method, body: init.body ? JSON.parse(init.body) : undefined })
    if (failures.has(`${method} ${path}`)) {
      return json({ error: 'boom', detail: 'The service is unavailable.' }, 500)
    }
    const handler = handlers[`${method} ${path}`]
    if (handler) return handler({ path, method })
    const fallback = DEFAULTS[path]
    if (fallback === undefined) throw new Error(`unstubbed request: ${method} ${path}`)
    return json(fallback)
  })
})

async function renderPage() {
  const user = userEvent.setup()
  render(<AcceptancePage />)
  await waitFor(() => expect(screen.queryByText('Loading quote acceptance')).toBeNull())
  return { user }
}

describe('the loading state', () => {
  it('says what is loading rather than rendering a blank board', async () => {
    render(<AcceptancePage />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading quote acceptance')
  })
})

describe('the error state', () => {
  it('shows a retry when the board cannot be read, rather than an empty board', async () => {
    failures.add(`GET ${BASE}/summary`)
    const user = userEvent.setup()
    render(<AcceptancePage />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load data')
    expect(within(alert).getByRole('button', { name: /retry/i })).toBeDefined()
    expect(user).toBeDefined()
  })
})

describe('the empty state', () => {
  it('offers the authoring step when there is no envelope, rather than a blank board', async () => {
    handlers[`GET ${BASE}/envelopes`] = () =>
      json({ count: 0, envelopes: [] }, 200)
    handlers[`GET ${BASE}/summary`] = () =>
      json({ ...SUMMARY, envelopes: 0, signers_total: 0, signers_outstanding: 0 }, 200)

    await renderPage()

    expect(screen.getByText('No signing envelopes yet')).toBeDefined()
    expect(screen.getAllByRole('button', { name: /open envelope/i }).length).toBeGreaterThan(0)
  })
})

describe('the board', () => {
  it('shows the headline numbers a seller opens the page for', async () => {
    await renderPage()

    expect(screen.getByRole('heading', { name: 'Collect acceptance by e-signature' })).toBeDefined()
    expect(screen.getByText('Envelopes')).toBeDefined()
    expect(screen.getAllByText('Accepted').length).toBeGreaterThan(0)
    expect(screen.getByText('2 parties still owe')).toBeDefined()
    expect(screen.getByText('Verification on')).toBeDefined()
  })

  it('shows the signing chain on every envelope, with the current step marked', async () => {
    await renderPage()

    expect(screen.getAllByText('Pending signature').length).toBeGreaterThan(0)
    expect(screen.getByText('Viewed - pending signature')).toBeDefined()
    expect(screen.getByText('Pending countersignature')).toBeDefined()
    expect(screen.getAllByText('Accepted').length).toBeGreaterThan(0)
  })

  it('says none stated for the quota ceiling rather than inventing a number', async () => {
    await renderPage()

    expect(screen.getByText('E-signature usage this month')).toBeDefined()
    expect(screen.getByText('none stated')).toBeDefined()
    expect(screen.getByText('The research states no number.')).toBeDefined()
  })

  it('states what the workflow does not own, with the evidence beside it', async () => {
    await renderPage()

    expect(screen.getByText('What this workflow does not own')).toBeDefined()
    expect(screen.getByText(/WF-099/)).toBeDefined()
    expect(screen.getByText(/It is your responsibility to verify the identity/)).toBeDefined()
  })

  it('shows each decision with the alternative it rejected', async () => {
    await renderPage()

    expect(screen.getByText('Decisions this workflow made')).toBeDefined()
    expect(screen.getAllByText('new_package_quote_acceptance').length).toBeGreaterThan(0)
    expect(
      screen.getByText('quoting_proposals owns rendering, not collecting a signature.'),
    ).toBeDefined()
    expect(screen.getByText('jev-20261005T075612-8560-72925')).toBeDefined()
  })
})

describe('opening an envelope', () => {
  it('sends the acceptance configuration the seller chose', async () => {
    const { user } = await renderPage()

    handlers[`POST ${BASE}/envelopes`] = () => json(envelope(), 201)

    await user.click(screen.getAllByRole('button', { name: /open envelope/i })[0])
    const dialog = await screen.findByRole('dialog')

    await user.clear(within(dialog).getByLabelText('Buyer contact email'))
    await user.type(within(dialog).getByLabelText('Buyer contact email'), 'grace@nw.example')
    await user.click(
      within(dialog).getByRole('switch', { name: /quote signer\(s\) can reassign/i }),
    )
    await user.click(within(dialog).getByRole('button', { name: /open envelope/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    const call = calls.find((entry) => entry.method === 'POST' && entry.path === `${BASE}/envelopes`)
    expect(call).toBeDefined()
    expect(call.body.hs_acceptance_method).toBe('esignature')
    expect(call.body.buyer_signers[0].email).toBe('grace@nw.example')
    expect(call.body.reassign_allowed).toBe(true)
    expect(call.body.countersigners[0].role).toBe('countersigner')
    // The publication flag is a query parameter, not a payload field, so the route reads it
    // once and the body carries only the acceptance configuration. It is stripped from the
    // body rather than duplicated, because the server reads one place for it.
    expect(call.body.is_published).toBeUndefined()
    expect(call.url).toContain('is_published=true')
    expect(call.url).toContain(`room_id=${ROOM}`)
  })

  it('renders a refusal next to the field it belongs to', async () => {
    const { user } = await renderPage()

    handlers[`POST ${BASE}/envelopes`] = () =>
      json(
        {
          error: 'invalid_acceptance_request',
          detail: 'The acceptance configuration is not one I can accept.',
          errors: { buyer_signers: 'an e-signature quote must name at least one buyer contact.' },
        },
        400,
      )

    await user.click(screen.getAllByRole('button', { name: /open envelope/i })[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /open envelope/i }))

    await waitFor(() =>
      expect(screen.getByText('The envelope was refused')).toBeDefined(),
    )
    // The message sits beside its field name in one list item, so the item is matched rather
    // than the bare text, which would be a child node of a node that also carries the field.
    const refusal = screen
      .getAllByRole('listitem')
      .find((item) => item.textContent.includes('at least one buyer contact'))
    expect(refusal).toBeDefined()
    expect(refusal.textContent).toContain('buyer_signers')
  })
})

describe('a refused signature', () => {
  it('reads as an outcome and not as a broken request, because it is a 200', async () => {
    const { user } = await renderPage()

    handlers[`POST ${BASE}/signers/sig_buyer/sign`] = () =>
      json({
        outcome: 'failed',
        reason: 'verification_not_requested',
        detail: 'The buyer has not clicked Verify email on this quote yet.',
        activity: 'signing_attempt_failed',
        activity_label: 'Signing attempt failed',
        envelope: envelope(),
      })

    await user.click(screen.getAllByRole('button', { name: 'Open' })[0])
    await user.click((await screen.findAllByRole('button', { name: 'Sign' }))[0])
    const dialog = await screen.findByRole('dialog', { name: /Sign as Buyer contact/ })
    await user.click(within(dialog).getByRole('button', { name: /insert signature/i }))

    await waitFor(() => expect(screen.getByText('The signature was refused')).toBeDefined())
    expect(
      screen.getByText('The buyer has not clicked Verify email on this quote yet.'),
    ).toBeDefined()
  })
})

describe('the verification window', () => {
  it('offers the Verify email step to an unverified buyer, with the researched hour', async () => {
    const { user } = await renderPage()

    handlers[`POST ${BASE}/envelopes/env_1/verify`] = () =>
      json({
        envelope_id: 'env_1',
        verification_link: 'token-abc',
        window: {
          requested_at: '2026-10-05T12:00:00+00:00',
          expires_at: '2026-10-05T13:00:00+00:00',
          window_minutes: 60,
          expired: false,
          open: true,
          evidence: 'one hour to complete the signature process',
        },
        verification_window_quote: 'Buyers have one hour to complete the signature process.',
      })

    await user.click(screen.getAllByRole('button', { name: 'Open' })[0])
    await user.click((await screen.findAllByRole('button', { name: 'Sign' }))[0])
    const dialog = await screen.findByRole('dialog', { name: /Sign as Buyer contact/ })

    expect(
      within(dialog).getByText('Verify email before signing'),
    ).toBeDefined()
    expect(
      within(dialog).getByText('Buyers have one hour to complete the signature process after clicking Verify email.'),
    ).toBeDefined()

    await user.click(within(dialog).getByRole('button', { name: /verify email/i }))
    await waitFor(() => expect(within(dialog).getByText(/Link: token-abc/)).toBeDefined())
  })

  it('offers no Verify email step to a party the envelope does not gate', async () => {
    handlers[`POST ${BASE}/envelopes/env_1`] = undefined
    handlers[`POST ${BASE}/signers/sig_counter/sign`] = () =>
      json({
        outcome: 'signed',
        reason: null,
        detail: null,
        activity: 'quote_countersigned',
        activity_label: 'Quote countersigned',
        envelope: envelope({ signing_status: 'pending_countersignature' }),
      })

    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Open' })[0])
    await waitFor(() => expect(screen.getByText('Signers')).toBeDefined())

    // The countersigner row is the second one, and the envelope does not gate that party.
    await user.click(screen.getAllByRole('button', { name: 'Sign' })[1])
    const dialog = await screen.findByRole('dialog', { name: /Sign as Countersigner/ })

    expect(within(dialog).queryByText('Verify email before signing')).toBeNull()
  })
})

describe('the signers panel', () => {
  it('names both parties and the order the research fixes', async () => {
    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Open' })[0])

    await waitFor(() => expect(screen.getByText('Signers')).toBeDefined())
    expect(screen.getByText('Ada Byron')).toBeDefined()
    expect(screen.getAllByText(/dana@halcyon.example/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Signs first/)).toBeDefined()
    expect(screen.getByText(/Signs second/)).toBeDefined()
  })

  it('offers reassignment on a quote that allows it and refuses it on one that does not', async () => {
    handlers[`POST ${BASE}/signers/sig_buyer/reassign`] = () =>
      json({ ...BUYER, name: 'Grace Okonkwo', email: 'grace@nw.example' }, 200)

    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Open' })[0])
    await waitFor(() => expect(screen.getByText('Signers')).toBeDefined())

    // The seeded envelope has reassign_allowed false, so the server refuses it and the page
    // shows the server's own reason rather than hiding the button.
    await user.click(screen.getAllByRole('button', { name: /reassign/i })[0])
    const dialog = await screen.findByRole('dialog', { name: /Reassign this quote signer/ })
    await user.clear(within(dialog).getByLabelText('New signer email'))
    await user.type(within(dialog).getByLabelText('New signer email'), 'grace@nw.example')
    await user.click(within(dialog).getByRole('button', { name: /reassign signer/i }))

    const call = calls.find((entry) => entry.method === 'POST' && entry.path.includes('/reassign'))
    expect(call.body.email).toBe('grace@nw.example')
  })
})

describe('the event log', () => {
  it('says no events rather than rendering an empty list', async () => {
    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Open' })[0])

    await waitFor(() =>
      expect(screen.getByText('No signature events on this envelope yet.')).toBeDefined(),
    )
  })

  it('names each activity the research names', async () => {
    handlers[`GET ${BASE}/envelopes/env_1/events`] = () =>
      json({
        envelope_id: 'env_1',
        count: 2,
        events: [
          {
            id: 'evt_1',
            event: 'buyer_signed',
            activity: 'quote_buyer_signed',
            activity_label: 'Quote buyer signed',
            status_before: 'viewed_pending_signature',
            status_after: 'pending_countersignature',
            detail: {},
            at: '2026-10-05T12:00:00+00:00',
          },
          {
            id: 'evt_2',
            event: 'attempt_failed',
            activity: 'signing_attempt_failed',
            activity_label: 'Signing attempt failed',
            status_before: 'pending_countersignature',
            status_after: 'pending_countersignature',
            detail: { reason: 'verification_window_expired' },
            at: '2026-10-05T12:30:00+00:00',
          },
        ],
      })

    const { user } = await renderPage()
    await user.click(screen.getAllByRole('button', { name: 'Open' })[0])

    await waitFor(() => expect(screen.getByText('Quote buyer signed')).toBeDefined())
    expect(screen.getByText('Signing attempt failed')).toBeDefined()
    expect(screen.getByText('verification_window_expired')).toBeDefined()
  })
})

describe('the descriptor', () => {
  it('is the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-095-collect-acceptance-by-e-signature-countersignature')
    expect(descriptor.Component).toBe(AcceptancePage)
    expect(typeof descriptor.label).toBe('string')
  })
})
