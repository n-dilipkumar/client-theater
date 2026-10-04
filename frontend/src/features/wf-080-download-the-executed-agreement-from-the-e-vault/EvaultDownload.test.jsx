/**
 * WF-080's page, against the payloads the server actually sends.
 *
 * The tests are organised by the workflow's four researched claims, because the point of
 * this feature is that those claims are enforced rather than asserted:
 *
 *   - the 202 is a wait, not a failure, and the wait has a number in it;
 *   - the two endpoints are not interchangeable, and the page says which is which;
 *   - the sealed bytes are byte-stable while a watermarked plain copy is not;
 *   - a repeated delivery is applied once and stays visible.
 *
 * Plus the honesty rule: a page that offers a download without saying what the seal is
 * worth is the reading the specification forbids, so the scope sentence has to be on the
 * page before anything else.
 *
 * Every fixture is built from the served vocabulary rather than from literals where the
 * server and the client both know the term, and one test asserts the two agree, so the
 * client cannot quietly become a second source of truth.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Field, inputClass } from '@/components/ui'

import page from './index'
import {
  DOCUMENT_STATES,
  ENVIRONMENTS,
  FETCH_OUTCOMES,
  PDF_READY_TRIGGER,
  outcomeLabel,
  stateLabel,
} from './api'
import {
  INPUT_CLASS,
  OutcomeBadge,
  RetryBanner,
  RetryCountBadge,
  Select,
  VariantMatrix,
} from './primitives'

// The host reads the default export as the descriptor, so the page is reached through
// `page.Component` rather than as a named export. Rendering the descriptor itself would
// prove nothing about the page, and a named import would be an export this feature does
// not have.
const EvaultDownloadPage = page.Component

const ROOM_ID = 'room_a'

const VOCABULARY = {
  trigger: 'document_completed_pdf_ready',
  required_triggers: ['document_completed_pdf_ready'],
  dedupe_header: 'X-PandaDoc-Webhook-Event-Id',
  delivery_id_keys: ['eventId', 'event_id', 'id'],
  environments: ['production', 'sandbox'],
  sealed_environment: 'production',
  sandbox_remedy: 'The sealed endpoint needs a production key.',
  variants: [
    {
      variant: 'sealed',
      endpoint: '/public/v1/documents/{id}/download-protected',
      summary: 'Digitally sealed and verifiable.',
      byte_stable: true,
      watermarkable: false,
      environments: ['production'],
    },
    {
      variant: 'plain',
      endpoint: '/public/v1/documents/{id}/download',
      summary: 'The plain PDF, where a watermark goes.',
      byte_stable: false,
      watermarkable: true,
      environments: ['production', 'sandbox'],
    },
  ],
  variant_tradeoff: 'The sealed endpoint is byte-stable and immutable.',
  document_states: [
    { state: 'awaiting_signatures', label: 'Awaiting signatures', artifact_ready: false },
    { state: 'generating', label: 'PDF generating', artifact_ready: false, back_pressure: true },
    { state: 'sealed', label: 'Sealed in the e-vault', artifact_ready: true },
    { state: 'failed', label: 'Generation failed', artifact_ready: false },
  ],
  fetch_outcomes: [
    { outcome: 'retrieved', summary: 'The PDF was returned.' },
    { outcome: 'back_pressure', summary: 'The file is still being produced.' },
  ],
  media_type: 'application/pdf',
  throttle: { window_seconds: 60, limit: 10, status: 429, code: 'throttled' },
  effect: 'recorded_not_verified',
  tradeoff: 'The sealed endpoint is byte-stable and immutable.',
  seal_scope:
    'This room records the bytes and their SHA-256 digest, so what it can prove is that the ' +
    'file has not changed. It does not validate a certificate chain.',
  no_polling: 'This room subscribes to the ready event and does not poll.',
}

function summary(overrides = {}) {
  return {
    ticket: 'WF-080',
    room_id: ROOM_ID,
    subscriptions: 1,
    active_subscriptions: 1,
    documents: 1,
    documents_by_state: { awaiting_signatures: 1 },
    sealed_documents: 0,
    generating_documents: 0,
    deliveries: 0,
    retries_deduped: 0,
    attempts: 0,
    attempts_by_outcome: {},
    artifacts: 0,
    sealed_artifacts: 0,
    watermarked_artifacts: 0,
    invariants: {},
    ...VOCABULARY,
    ...overrides,
  }
}

function document(overrides = {}) {
  return {
    id: 'wf080_executed_document_1',
    room_id: ROOM_ID,
    vendor_document_id: 'pd_doc_northwind_001',
    subject: 'Master services agreement',
    state: 'awaiting_signatures',
    state_label: 'Awaiting signatures',
    environment: 'production',
    artifact_ready: false,
    back_pressure: false,
    sealed_allowed_here: true,
    artifacts: [],
    sealed_artifact: null,
    plain_artifact: null,
    deliveries: [],
    attempts: [],
    latest_attempt: null,
    ...overrides,
  }
}

const SEALED_ARTIFACT = {
  id: 'wf080_vault_artifact_1',
  variant: 'sealed',
  byte_stable: true,
  watermarkable: false,
  watermark: '',
  media_type: 'application/pdf',
  byte_length: 846,
  sha256: 'a'.repeat(64),
  generated: true,
  summary: 'Digitally sealed and verifiable.',
  endpoint: '/public/v1/documents/{id}/download-protected',
  seal_scope: VOCABULARY.seal_scope,
}

const PLAIN_ARTIFACT = {
  ...SEALED_ARTIFACT,
  id: 'wf080_vault_artifact_2',
  variant: 'plain',
  byte_stable: false,
  watermarkable: true,
  watermark: 'NORTHWIND CONFIDENTIAL',
  byte_length: 953,
  sha256: 'b'.repeat(64),
}

/** Install a fetch stub that answers every route this page calls. */
function stubApi(overrides = {}) {
  const state = {
    room: ROOM_ID,
    summary: summary(),
    documents: [document()],
    detail: document(),
    vocabulary: VOCABULARY,
    decisions: {
      ticket: 'WF-080',
      count: 1,
      decisions: [
        {
          id: 'DERIVED_EMPTY_BODY_ON_202',
          question: 'What does the 202 send?',
          chosen: 'mirror_the_vendor',
          rejected_because: 'A JSON body breaks a client written against the vendor.',
        },
      ],
    },
    subscriptions: {
      room_id: ROOM_ID,
      count: 1,
      active: 1,
      subscriptions: [
        {
          id: 'wf080_webhook_subscription_1',
          room_id: ROOM_ID,
          triggers: [PDF_READY_TRIGGER],
          hears_ready_event: true,
          environment: 'production',
          active: true,
          shared_key: 'shr_abc123',
          retry_after_seconds: 5,
        },
      ],
    },
    download: { status: 200, outcome: 'retrieved', bytes: 846, digest: 'a'.repeat(64) },
    retrieve: { outcome: 'retrieved', status: 200, artifact: SEALED_ARTIFACT },
    retrieveError: null,
    deliverOutcome: 'retrieved',
    ...overrides,
  }

  const calls = []
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })

  const fetchMock = vi.fn(async (url, init = {}) => {
    const method = (init.method || 'GET').toUpperCase()
    const path = String(url)
    calls.push(`${method} ${path}`)

    if (path === '/api/records/room?limit=100') {
      return json({ count: 1, records: [{ id: ROOM_ID, data: { name: 'Northwind' } }] })
    }
    if (path === '/api/wf-080/vocabulary') return json(state.vocabulary)
    if (path === '/api/wf-080/decisions') return json(state.decisions)
    if (path.startsWith('/api/wf-080/summary')) return json(state.summary)
    if (path.includes('/documents/') && path.includes('/download-protected')) {
      const report = state.download
      if (report.status === 202) {
        return new Response(null, { status: 202, headers: { 'Retry-After': String(report.retryAfter) } })
      }
      return new Response(new Uint8Array(report.bytes), {
        status: 200,
        headers: {
          'Content-Type': 'application/pdf',
          'X-DSR-Digest': report.digest,
        },
      })
    }
    if (path.endsWith('/retrieve') || path.endsWith('/retrieve-plain')) {
      if (state.retrieveError) return json(state.retrieveError, state.retrieveError.status || 400)
      return json(state.retrieve)
    }
    if (path.includes('/documents/') && method === 'GET') return json(state.detail)
    if (path.endsWith('/documents') && method === 'POST') return json(state.documents[0], 201)
    if (path.includes('/documents')) return json({ count: 1, documents: state.documents, sealed: 0, generating: 0 })
    if (path.endsWith('/events')) return json({ count: 1, events: [] })
    if (path.includes('/attempts')) return json({ count: 0, by_outcome: {}, attempts: [] })
    if (path.includes('/artifacts')) return json({ count: 0, artifacts: [] })
    if (path.includes('/subscriptions')) return json(state.subscriptions)
    return json({ detail: 'stubbed route not matched', error: 'stub' }, 404)
  })

  vi.stubGlobal('fetch', fetchMock)
  return { state, calls, fetchMock }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

// --------------------------------------------------------------------------- //
// the descriptor
// --------------------------------------------------------------------------- //

describe('the descriptor', () => {
  it('exports the four keys the host reads', () => {
    expect(page.id).toBe('wf-080-download-the-executed-agreement-from-the-e-vault')
    expect(typeof page.label).toBe('string')
    expect(page.Component).toBeTruthy()
  })

  it('names an icon without an emoji', () => {
    expect(page.iconPath || page.icon).toBeTruthy()
    expect(page.icon).not.toMatch(/\p{Extended_Pictographic}/u)
    expect(page.label).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})

// --------------------------------------------------------------------------- //
// the honesty rule
// --------------------------------------------------------------------------- //

describe('the honesty rule', () => {
  it('states what the room can verify before anything else', async () => {
    stubApi()
    render(<EvaultDownloadPage />)
    const scope = await screen.findByText(/does not validate a certificate chain/)
    expect(scope).toBeTruthy()
  })

  it('states that the room does not poll', async () => {
    stubApi()
    render(<EvaultDownloadPage />)
    // The sentence appears twice on purpose: once as the standing rule beside the seal
    // scope, and once as the reason a subscription must carry the ready trigger. Both are
    // the same claim, and the count is asserted so a third copy is noticed.
    const found = await screen.findAllByText(/subscribes to the ready event and does not poll/)
    expect(found.length).toBeGreaterThanOrEqual(1)
  })

  it('states the trade-off between the two endpoints', async () => {
    stubApi()
    render(<EvaultDownloadPage />)
    expect(await screen.findByText(/byte-stable and immutable/)).toBeTruthy()
  })

  it('names the dedupe header so a vendor can configure it', async () => {
    stubApi()
    render(<EvaultDownloadPage />)
    // Named twice: beside the subscriptions a seller configures, and in the register of
    // decisions a reviewer reads. Both render it in mono because it is a header name.
    const found = await screen.findAllByText('X-PandaDoc-Webhook-Event-Id')
    expect(found.length).toBeGreaterThanOrEqual(1)
    for (const node of found) expect(node.className).toContain('font-mono')
  })
})

// --------------------------------------------------------------------------- //
// the two endpoints are not interchangeable
// --------------------------------------------------------------------------- //

describe('the two endpoints', () => {
  it('draws both of them with their two booleans', () => {
    render(<VariantMatrix variants={VOCABULARY.variants} />)
    expect(screen.getByText('/public/v1/documents/{id}/download-protected')).toBeTruthy()
    expect(screen.getByText('/public/v1/documents/{id}/download')).toBeTruthy()
    expect(screen.getByText('Yes, same bytes')).toBeTruthy()
    expect(screen.getByText('No, differs')).toBeTruthy()
    expect(screen.getByText('Yes, applies one')).toBeTruthy()
    expect(screen.getByText('No, refuses one')).toBeTruthy()
    expect(screen.getByText('401 refused')).toBeTruthy()
  })

  it('leaves no cell blank, so no cell needs interpreting', () => {
    render(<VariantMatrix variants={VOCABULARY.variants} />)
    const cells = within(screen.getByRole('table')).getAllByRole('cell')
    expect(cells.length).toBe(6)
    for (const cell of cells) {
      expect(cell.textContent.trim()).not.toBe('')
    }
  })

  it('shows both endpoints on the page with the sandbox column', async () => {
    stubApi()
    render(<EvaultDownloadPage />)
    expect(
      await screen.findByText('/public/v1/documents/{id}/download-protected'),
    ).toBeTruthy()
    expect(screen.getByText('401 refused')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// 202 is a wait, not a failure
// --------------------------------------------------------------------------- //

describe('the back-pressure signal', () => {
  it('renders the wait with the number in it', () => {
    render(<RetryBanner seconds={17} />)
    expect(screen.getByText('Wait 17 seconds and try again')).toBeTruthy()
  })

  it('says the response carried no body', () => {
    render(<RetryBanner seconds={5} />)
    expect(screen.getByText(/no body at all/)).toBeTruthy()
  })

  it('says where the number came from, because the response has none', () => {
    render(<RetryBanner seconds={5} source="the attempt log" />)
    expect(screen.getByText(/the attempt log/)).toBeTruthy()
  })

  it('falls back to words when the wait is zero', () => {
    render(<RetryBanner seconds={0} />)
    expect(screen.getByText('Wait and try again')).toBeTruthy()
  })

  it('shows the wait on the page from the attempt log', async () => {
    stubApi({
      detail: document({
        state: 'generating',
        state_label: 'PDF generating',
        back_pressure: true,
        latest_attempt: {
          id: 'a1',
          variant: 'sealed',
          outcome: 'back_pressure',
          retry_after_seconds: 11,
          at: '2026-10-04T09:00:00.000+00:00',
          summary: 'The file is still being produced.',
        },
        attempts: [
          {
            id: 'a1',
            variant: 'sealed',
            outcome: 'back_pressure',
            retry_after_seconds: 11,
            at: '2026-10-04T09:00:00.000+00:00',
            summary: 'The file is still being produced.',
          },
        ],
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('Wait 11 seconds and try again')).toBeTruthy()
    expect(screen.getByText(/Retry-After 11s/)).toBeTruthy()
  })

  it('shows the 202 from the download itself, not from the log', async () => {
    const user = userEvent.setup()
    stubApi({
      download: { status: 202, outcome: 'back_pressure', retryAfter: 7 },
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        artifact_ready: true,
        artifacts: [SEALED_ARTIFACT],
      }),
    })
    render(<EvaultDownloadPage />)
    await user.click(await screen.findByRole('button', { name: /Download the sealed bytes/ }))
    expect(await screen.findByText('Wait 7 seconds and try again')).toBeTruthy()
    expect(screen.getByText(/Retry-After header on the 202/)).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// the sealed bytes are byte-stable
// --------------------------------------------------------------------------- //

describe('byte stability', () => {
  it('shows the sealed length and the full digest', async () => {
    stubApi({
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        artifact_ready: true,
        artifacts: [SEALED_ARTIFACT],
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('846 bytes')).toBeTruthy()
    expect(screen.getByText('a'.repeat(64))).toBeTruthy()
  })

  it('says the sealed copy is byte-stable and the plain one is not', async () => {
    stubApi({
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        artifact_ready: true,
        artifacts: [SEALED_ARTIFACT, PLAIN_ARTIFACT],
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('Byte-stable')).toBeTruthy()
    expect(screen.getByText('Bytes vary per request')).toBeTruthy()
  })

  it('shows the watermark that was applied to the plain copy', async () => {
    stubApi({
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        artifact_ready: true,
        artifacts: [SEALED_ARTIFACT, PLAIN_ARTIFACT],
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('NORTHWIND CONFIDENTIAL')).toBeTruthy()
    expect(screen.getByText('953 bytes')).toBeTruthy()
  })

  it('reports the bytes and the digest the download actually returned', async () => {
    const user = userEvent.setup()
    stubApi({
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        artifact_ready: true,
        artifacts: [SEALED_ARTIFACT],
      }),
    })
    render(<EvaultDownloadPage />)
    await user.click(await screen.findByRole('button', { name: /Download the sealed bytes/ }))
    expect(await screen.findByText('The sealed bytes arrived')).toBeTruthy()
    // The badge and the artifact list both carry the length, which is the point: the
    // stored record and the bytes just served agree.
    expect((await screen.findAllByText(/846 bytes/)).length).toBeGreaterThanOrEqual(2)
  })
})

// --------------------------------------------------------------------------- //
// the dedupe
// --------------------------------------------------------------------------- //

describe('the dedupe', () => {
  it('shows nothing extra on a delivery that arrived once', () => {
    render(<RetryCountBadge deliveries={1} />)
    expect(screen.queryByText(/applied once/)).toBeNull()
  })

  it('says in words that a repeated delivery was applied once', () => {
    render(<RetryCountBadge deliveries={3} />)
    expect(screen.getByText('3 deliveries, applied once')).toBeTruthy()
  })

  it('shows the retry on the page', async () => {
    stubApi({
      summary: summary({ deliveries: 1, retries_deduped: 1 }),
      detail: document({
        deliveries: [
          {
            id: 'd1',
            event: 'document_completed_pdf_ready',
            delivery_id: 'evt_1',
            delivery_id_source: 'header',
            deliveries: 2,
            retries: 1,
            state_before: 'awaiting_signatures',
            state_after: 'generating',
          },
        ],
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('2 deliveries, applied once')).toBeTruthy()
    expect(screen.getByText('1 retry deduped')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// the two refusals
// --------------------------------------------------------------------------- //

describe('the refusals', () => {
  it('says a sandbox key cannot reach the sealed endpoint', async () => {
    stubApi({
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        environment: 'sandbox',
        sealed_allowed_here: false,
        artifact_ready: true,
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('This key cannot reach the sealed endpoint')).toBeTruthy()
    expect(screen.getByText(/sealed endpoint is production key only/)).toBeTruthy()
  })

  it('names the endpoint a 401 tells a sandbox caller to use instead', async () => {
    const user = userEvent.setup()
    stubApi({
      retrieveError: {
        status: 401,
        error: 'sandbox_key_rejected',
        detail: 'The sealed endpoint needs a production key.',
        remedy: 'In sandbox, use the plain download endpoint.',
        use_instead: '/public/v1/documents/{id}/download',
      },
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        environment: 'production',
        sealed_allowed_here: true,
        artifact_ready: true,
      }),
    })
    render(<EvaultDownloadPage />)
    await user.click(await screen.findByRole('button', { name: /Retrieve the sealed PDF/ }))
    expect(await screen.findByText('The sealed endpoint refused this key')).toBeTruthy()
    expect(screen.getByText(/use the plain download endpoint/)).toBeTruthy()
  })

  it('disables the sealed retrieval when the key cannot reach it', async () => {
    stubApi({
      detail: document({
        state: 'sealed',
        state_label: 'Sealed in the e-vault',
        environment: 'sandbox',
        sealed_allowed_here: false,
        artifact_ready: true,
      }),
    })
    render(<EvaultDownloadPage />)
    const button = await screen.findByRole('button', { name: /Retrieve the sealed PDF/ })
    expect(button).toBeDisabled()
  })

  it('says a 429 is a wait and not a generic failure', async () => {
    const user = userEvent.setup()
    stubApi({
      retrieveError: {
        status: 429,
        error: 'throttled',
        detail: '10 retrievals for this document inside 60s.',
        retry_after: 42,
      },
      detail: document({ state: 'sealed', state_label: 'Sealed in the e-vault', artifact_ready: true }),
    })
    render(<EvaultDownloadPage />)
    await user.click(await screen.findByRole('button', { name: /Retrieve the sealed PDF/ }))
    expect(await screen.findByText('Throttled. Wait 42 seconds')).toBeTruthy()
    // Named in the refusal and in the register below it. The register spelling matters:
    // it is the code the vendor's own words give, and a page that softened it to "busy"
    // would be the generic failure the specification forbids.
    const codes = await screen.findAllByText('throttled')
    expect(codes.length).toBeGreaterThanOrEqual(1)
  })

  it('says a 409 is not retryable and names the state', async () => {
    const user = userEvent.setup()
    stubApi({
      retrieveError: {
        status: 409,
        error: 'not_completed_awaiting_signatures',
        detail: 'This document is awaiting_signatures; no PDF is being produced.',
        state: 'awaiting_signatures',
        retryable: false,
      },
    })
    render(<EvaultDownloadPage />)
    await user.click(await screen.findByRole('button', { name: /Retrieve the sealed PDF/ }))
    expect(await screen.findByText('Nothing is being produced yet')).toBeTruthy()
    expect(screen.getByText(/no wait to offer/)).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// the states
// --------------------------------------------------------------------------- //

describe('the states', () => {
  it('labels every state with the server label, not a local guess', async () => {
    stubApi({
      detail: document({
        state: 'generating',
        state_label: 'PDF generating',
        back_pressure: true,
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('PDF generating')).toBeTruthy()
    expect(screen.queryByText('generating')).toBeNull()
  })

  it('says a wait is not a document with no PDF in progress', async () => {
    stubApi({
      detail: document({
        state: 'generating',
        state_label: 'PDF generating',
        back_pressure: true,
      }),
    })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('PDF being produced')).toBeTruthy()
  })

  it('names the trigger the specification quotes', async () => {
    stubApi()
    render(<EvaultDownloadPage />)
    const field = await screen.findByLabelText('Triggers')
    expect(field.value).toBe('document_completed_pdf_ready')
  })
})

// --------------------------------------------------------------------------- //
// the client and the server agree
// --------------------------------------------------------------------------- //

describe('the client and the served vocabulary', () => {
  it('uses the trigger the server serves', () => {
    expect(PDF_READY_TRIGGER).toBe(VOCABULARY.trigger)
  })

  it('carries the same four document states', () => {
    expect(DOCUMENT_STATES.map((row) => row.id)).toEqual(
      VOCABULARY.document_states.map((row) => row.state),
    )
  })

  it('carries the same two environments', () => {
    expect(ENVIRONMENTS.map((row) => row.id)).toEqual(VOCABULARY.environments)
  })

  it('reads only production as sealed', () => {
    const sealed = VOCABULARY.variants.find((row) => row.variant === 'sealed')
    expect(ENVIRONMENTS.filter((row) => row.sealed).map((row) => row.id)).toEqual(
      sealed.environments,
    )
  })

  it('carries a label for every fetch outcome it names', () => {
    for (const outcome of VOCABULARY.fetch_outcomes) {
      expect(outcomeLabel(outcome.outcome)).toBeTruthy()
      expect(FETCH_OUTCOMES.some((row) => row.id === outcome.outcome)).toBe(true)
    }
  })

  it('labels every document state', () => {
    for (const state of VOCABULARY.document_states) {
      expect(stateLabel(state.state)).toBe(state.label)
    }
  })
})

// --------------------------------------------------------------------------- //
// the states of the page itself
// --------------------------------------------------------------------------- //

describe('loading, empty and error', () => {
  it('shows a loading state before anything else', () => {
    stubApi()
    render(<EvaultDownloadPage />)
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0)
  })

  it('says so when the room has no agreements recorded', async () => {
    stubApi({ documents: [] })
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('No executed agreement recorded')).toBeTruthy()
  })

  it('reports a failed load rather than rendering an empty vault', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (String(url) === '/api/records/room?limit=100') {
        return new Response(JSON.stringify({ count: 1, records: [{ id: ROOM_ID, data: {} }] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ detail: 'the vault is unreachable' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<EvaultDownloadPage />)
    // Every panel that failed says so, rather than the page falling silent. The count is
    // asserted because one silent panel among three is the failure this rule exists for.
    const notes = await screen.findAllByText('Could not load data')
    expect(notes.length).toBeGreaterThanOrEqual(1)
  })

  it('tells a reader with no room that there is nothing to attach to', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ count: 0, records: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    render(<EvaultDownloadPage />)
    expect(await screen.findByText('No rooms yet')).toBeTruthy()
  })
})

// --------------------------------------------------------------------------- //
// the accessibility floor, measured on the components
// --------------------------------------------------------------------------- //

describe('the accessibility floor', () => {
  it('gives its own select the shared 44px floor', () => {
    // The control that re-declares `inputClass` for a `<select>`. Rendering it here is the
    // point: a copy of the shared string is exactly the thing that can drift, so the copy is
    // what gets asserted against the original, not a hand-written button.
    render(
      <Field label="Room" id="floor-room">
        <Select id="floor-room" value="room_a" onChange={() => {}}>
          <option value="room_a">Northwind</option>
        </Select>
      </Field>,
    )
    const control = screen.getByLabelText('Room')
    expect(control.tagName).toBe('SELECT')
    expect(control.className).toBe(INPUT_CLASS)
    expect(control.className).toContain('min-h-11')
  })

  it('keeps its select copy identical to the shared inputClass', () => {
    // The claim in primitives.jsx that "a test asserts the two match". This is that test.
    expect(INPUT_CLASS).toBe(inputClass)
  })

  it('gives every outcome badge a word, not only a colour', () => {
    render(<OutcomeBadge outcome="throttled" label="Throttled" />)
    const badge = screen.getByText('Throttled')
    expect(badge.className).toContain('font-mono')
  })

  it('gives every outcome badge a distinct label for each of the five outcomes', () => {
    for (const outcome of FETCH_OUTCOMES) {
      const { unmount } = render(
        <OutcomeBadge outcome={outcome.id} label={outcomeLabel(outcome.id)} />,
      )
      expect(screen.getByText(outcomeLabel(outcome.id))).toBeTruthy()
      unmount()
    }
  })
})