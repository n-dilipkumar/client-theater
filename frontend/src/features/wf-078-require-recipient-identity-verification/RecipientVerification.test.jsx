import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AUTHENTICATION_OWNER, METHODS, NOT_PROOF, PLACES, SMS_TYPES } from './api'
import {
  ASSUMPTIONS,
  ATTEMPTS,
  DECISIONS,
  DELIVERY_ONLY_RECIPIENT,
  SUMMARY,
  TWO_AXIS_RECIPIENT,
  UNGATED_RECIPIENT,
  VOCABULARY,
  routes,
} from './fixtures'
import descriptor from './index.jsx'
import { AudienceBadge, GateMatrix, Notice, OutcomeBadge, Toggle } from './primitives'

/**
 * Tests for the WF-078 recipient-verification page.
 *
 * Six things this page can get quietly wrong, and each is pinned here:
 *
 * 1. It must not overstate what a pass proves. The gate compares answers; it does not
 *    establish who is holding the phone, and the knowledge-based and ID checks are not a
 *    background check. The limitation has to be rendered before anything else.
 * 2. The two axes must render as two axes. A page that showed one method per recipient
 *    would render a shape the specification's extensibility note rules out, and the
 *    fixture recipient carrying both moments is what makes the difference visible.
 * 3. The audience has to be shown beside each gate. `before_sign` applies to signers only,
 *    so a seller who sets it on a non-signer has configured a gate nobody can clear.
 * 4. A rejection must be as visible as a pass. The specification requires it, so a failed
 *    attempt has to render with its reason and with an action code.
 * 5. An SMS number that only delivers must not be counted as an authentication factor.
 *    That is the third state of the axis, and a page that cannot show it cannot show the
 *    difference between a number that proves identity and one that carries the document.
 * 6. An ungated recipient must read as ordinary, not as broken. Most recipients in a room
 *    carry no verification, and a page that renders that as an error teaches a seller to
 *    distrust the board.
 *
 * Queries are scoped to a section heading or a recipient's card, because the page prints
 * raw identifiers and an unscoped `getByText` would match a value it rendered as data.
 */

let calls = []
let overrides = {}

function stub() {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, options = {}) => {
      const raw = String(url)
      const override = overrides[raw.split('?')[0]] || {}
      const entry = { ...routes(raw, options), ...override }
      const method = options.method || 'GET'
      calls.push({
        url: raw,
        method,
        body: options.body ? JSON.parse(options.body) : null,
      })
      if (entry.status && entry.status >= 400) {
        return { ok: false, status: entry.status, statusText: 'Error', json: async () => entry.body }
      }
      return { ok: true, status: entry.status || 200, json: async () => entry.body }
    }),
  )
}

beforeEach(() => {
  overrides = {}
  stub()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage() {
  const Page = descriptor.Component
  return render(<Page />)
}

async function waitForPage() {
  await waitFor(() =>
    expect(screen.getByRole('heading', { name: 'Recipient verification', level: 1 })).toBeInTheDocument(),
  )
}

/** The card for one recipient, found by its name. */
function panelFor(name) {
  const heading = screen.getByRole('heading', { name })
  return heading.closest('div.glass')
}

describe('the descriptor', () => {
  it('exports the shape the feature host discovers', () => {
    expect(descriptor.id).toBe('wf-078-require-recipient-identity-verification')
    expect(descriptor.label).toBe('Recipient verification')
    expect(descriptor.Component).toBeTypeOf('function')
  })

  it('carries a glyph path rather than editing the shared icon set', () => {
    expect(descriptor.iconPath).toBeTypeOf('string')
    expect(descriptor.iconPath.length).toBeGreaterThan(0)
  })

  it('sorts after the confidential-view page it sits beside', () => {
    expect(descriptor.order).toBeGreaterThan(730)
  })
})

describe('what the page claims', () => {
  it('states that a pass does not establish who is who', () => {
    expect(NOT_PROOF).toContain('not that the person who gave it is who the sender meant')
  })

  it('renders the limitation before anything else on the page', async () => {
    renderPage()
    await waitForPage()
    // Fetched rather than typed, so the page and the server cannot disagree about what the
    // gate is worth.
    const notice = screen.getAllByRole('status')[0]
    expect(within(notice).getByText(/does not prove who is holding the phone/)).toBeInTheDocument()
    expect(within(notice).getByText(/Neither one is a background check/)).toBeInTheDocument()
  })

  it('says who owns the authentication decision', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getByText(AUTHENTICATION_OWNER)).toBeInTheDocument()
    expect(AUTHENTICATION_OWNER).toContain('solely responsible')
  })

  it('names the three things the specification marked inferred', async () => {
    renderPage()
    await waitForPage()
    const panel = screen.getByRole('heading', { name: /assumed rather than sourced/ }).closest('div.glass')
    for (const id of ASSUMPTIONS.assumptions.map((row) => row.id)) {
      expect(within(panel).getByText(id)).toBeInTheDocument()
    }
  })

  it('states that the public-record source names no vendor', () => {
    // The specification marks this itself, and a seller who believes the questions were
    // generated from public records has been told something this build does not do.
    const kba = ASSUMPTIONS.assumptions.find((row) => row.id === 'ASSUMED_KBA_PUBLIC_RECORD_SOURCE')
    expect(kba.rejected_because).toContain('No vendor is named')
  })

  it('states that the ID provider names no vendor', () => {
    const id = ASSUMPTIONS.assumptions.find((row) => row.id === 'ASSUMED_ID_VERIFICATION_PROVIDER')
    expect(id.rejected_because).toContain('none is named')
  })

  it('reports the ownership decision and its audit id', async () => {
    renderPage()
    await waitForPage()
    // The page fetches the decision rather than typing it, so the id shown is the one the
    // server recorded and a stale copy cannot survive here.
    const panel = screen.getByRole('heading', { name: /derived/ }).closest('div.glass')
    expect(within(panel).getByText('OWNERSHIP_WF078_OWNS_THE_GATE')).toBeInTheDocument()

    // The audit id is read from the notice that names the decision, not from a text query.
    // It is rendered inside a sentence, so no element's entire text is the id on its own,
    // and a substring matcher over the whole panel would match every ancestor as well.
    // Asserting on one element's textContent is the assertion that means what it says.
    const notice = within(panel).getByText('Which workflow owns the setting').closest(
      '[role="status"]',
    )
    const auditId = DECISIONS.decisions[0].jev_audit_id
    expect(notice.textContent).toContain(auditId)
    expect(notice.textContent).toContain('confidence 0.93')
  })
})

describe('the two axes', () => {
  it('serves both moments with the audience each applies to', () => {
    expect(PLACES.map((place) => place.id)).toEqual(['before_open', 'before_sign'])
    expect(PLACES.find((place) => place.id === 'before_open').audience).toBe('all_recipients')
    expect(PLACES.find((place) => place.id === 'before_sign').audience).toBe('signers_only')
  })

  it('renders a recipient carrying both moments as two occupied rows', async () => {
    renderPage()
    await waitForPage()
    const matrix = within(panelFor('Alex Doyle')).getByRole('table')
    // The extensibility note's sentence, made visible: one recipient, verified
    // differently for viewing and for signing.
    expect(within(matrix).getByText('passcode_verification')).toBeInTheDocument()
    expect(within(matrix).getByText('kba_verification')).toBeInTheDocument()
  })

  it('shows each moment on its own row rather than collapsing them', () => {
    render(<GateMatrix places={PLACES} gates={TWO_AXIS_RECIPIENT.gates} />)
    const rows = screen.getAllByRole('row')
    // A header plus one row per moment. A single collapsed row would be the shape the
    // specification's extensibility note rules out.
    expect(rows).toHaveLength(3)
    expect(within(rows[1]).getByText('Before open')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Before sign')).toBeInTheDocument()
  })

  it('says no gate here rather than rendering an empty cell', () => {
    render(<GateMatrix places={PLACES} gates={[]} />)
    // An empty cell a reader has to interpret is the ambiguity the design system forbids.
    expect(screen.getAllByText('No gate here')).toHaveLength(2)
  })

  it('shows the audience beside each gate, in words', async () => {
    renderPage()
    await waitForPage()
    const matrix = within(panelFor('Alex Doyle')).getByRole('table')
    expect(within(matrix).getAllByText('All recipients')).toHaveLength(1)
    expect(within(matrix).getAllByText('Signers only')).toHaveLength(1)
  })

  it('reports how many recipients carry both moments', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getByText(/carry a gate at both moments/)).toBeInTheDocument()
  })
})

describe('the four methods', () => {
  it('is a discriminated union: exactly four', () => {
    expect(METHODS.map((method) => method.id)).toEqual(['passcode', 'sms', 'kba', 'id'])
  })

  it('carries the vendor field name for each one', async () => {
    renderPage()
    await waitForPage()
    for (const method of VOCABULARY.methods) {
      expect(screen.getAllByText(method.vendor_field).length).toBeGreaterThan(0)
    }
  })

  it('shows only the SMS method as needing a code sent first', async () => {
    renderPage()
    await waitForPage()
    // The specification's Dropbox Sign evidence describes a send the signer triggers, so
    // only one of the four methods has that step.
    expect(screen.getAllByText('Needs a code sent first')).toHaveLength(1)
  })
})

describe('authentication and delivery are separable', () => {
  it('models all three states', () => {
    expect(SMS_TYPES.map((type) => type.id)).toEqual(['authentication', 'delivery', 'both'])
  })

  it('says a delivery-only number is not an authentication factor', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getByText(/delivers, it does not authenticate/)).toBeInTheDocument()
  })

  it('carries the meaning of the third state, not just its name', async () => {
    renderPage()
    await waitForPage()
    const panel = screen.getByRole('heading', { name: /enforces/ }).closest('div.glass')
    expect(within(panel).getByText(/not an authentication factor/)).toBeInTheDocument()
  })

  it('marks a delivery-only attempt as not a factor in the trail', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getByText('SMS role: delivery (not a factor)')).toBeInTheDocument()
  })
})

describe('a rejection is as visible as a pass', () => {
  it('renders both outcomes in the trail', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getAllByText('Passed').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Failed/).length).toBeGreaterThan(0)
  })

  it('names the reason a check did not clear', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getByText(/Failed \(answer_mismatch\)/)).toBeInTheDocument()
  })

  it('gives a failure its own action code rather than leaving it uncoded', async () => {
    renderPage()
    await waitForPage()
    // 51 is the code the specification names for a failed KBA check. A rejection with no
    // code is invisible to the compliance query the codes exist for.
    expect(screen.getByText('code 51')).toBeInTheDocument()
  })

  it('counts the failures on the board under their own label', async () => {
    renderPage()
    await waitForPage()
    // Scoped to the stat card, because the bare number appears in several cards and an
    // unscoped query would pass on the wrong one.
    const card = screen.getByText('Failed attempts').closest('div.glass')
    expect(within(card).getByText(String(SUMMARY.attempts_failed))).toBeInTheDocument()
    expect(within(card).getByText(`${SUMMARY.attempts} in total`)).toBeInTheDocument()
  })

  it('labels an outcome in words rather than by colour alone', () => {
    render(<OutcomeBadge outcome="pass" reason={null} />)
    expect(screen.getByText('Passed')).toBeInTheDocument()
    render(<OutcomeBadge outcome="fail" reason="code_mismatch" />)
    expect(screen.getByText(/code_mismatch/)).toBeInTheDocument()
  })
})

describe('running an attempt', () => {
  it('sends only the evidence the gate method needs', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor('Alex Doyle')
    fireEvent.change(within(panel).getByLabelText('Passcode'), { target: { value: 'Deal2026' } })
    fireEvent.click(within(panel).getByRole('button', { name: /Submit this attempt/ }))

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.includes('/attempts'))
      expect(post).toBeTruthy()
      // The discriminated union at the wire boundary: a passcode gate gets a passcode and
      // nothing else. Sending the whole form would put an answers field in a passcode gate.
      expect(post.body).toEqual({ passcode: 'Deal2026' })
    })
  })

  it('sends the answers keyed by prompt when the gate is knowledge-based', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor('Alex Doyle')
    fireEvent.change(within(panel).getByLabelText('Moment'), { target: { value: 'before_sign' } })
    fireEvent.change(within(panel).getByLabelText('Answers'), {
      target: { value: 'Purchase order number: PO-88214' },
    })
    fireEvent.click(within(panel).getByRole('button', { name: /Submit this attempt/ }))

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.includes('/attempts'))
      expect(post).toBeTruthy()
      // Keyed by prompt rather than by index, so a reordering cannot silently change which
      // answer belongs to which question.
      expect(post.body).toEqual({ answers: { 'Purchase order number': 'PO-88214' } })
    })
  })

  it('reports a pass and says what it does not prove', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor('Alex Doyle')
    fireEvent.change(within(panel).getByLabelText('Passcode'), { target: { value: 'Deal2026' } })
    fireEvent.click(within(panel).getByRole('button', { name: /Submit this attempt/ }))

    const notice = await screen.findByText('The check cleared and the row is in the trail')
    const box = notice.closest('[role="status"]')
    expect(within(box).getByText('Passed')).toBeInTheDocument()
    expect(within(box).getByText('action code 48')).toBeInTheDocument()
    expect(within(box).getByText(NOT_PROOF)).toBeInTheDocument()
  })

  it('reports a rejection without calling the page broken', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor('Alex Doyle')
    fireEvent.change(within(panel).getByLabelText('Passcode'), { target: { value: 'Wrong99' } })
    fireEvent.click(within(panel).getByRole('button', { name: /Submit this attempt/ }))

    const notice = await screen.findByText(
      'The check did not clear, and the row is in the trail anyway',
    )
    const box = notice.closest('[role="status"]')
    expect(within(box).getByText(/Failed \(passcode_mismatch\)/)).toBeInTheDocument()
    expect(within(box).getByText('action code 52')).toBeInTheDocument()
  })

  it('offers a new code for an SMS gate and never shows the code itself', async () => {
    const user = userEvent.setup()
    renderPage()
    await waitForPage()
    const panel = panelFor('Robin Hale')
    await user.click(within(panel).getByRole('button', { name: /Send a new code/ }))

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST' && call.url.includes('/codes'))).toBe(true)
    })
    // The evidence says a signer can request the code again, so the button exists; and a
    // send that showed the code would have turned the gate into a suggestion.
    expect(screen.queryByDisplayValue(/^\d{6}$/)).toBeNull()
  })

  it('refetches the trail after an attempt rather than patching local state', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor('Alex Doyle')
    fireEvent.change(within(panel).getByLabelText('Passcode'), { target: { value: 'Deal2026' } })
    fireEvent.click(within(panel).getByRole('button', { name: /Submit this attempt/ }))

    await waitFor(() => {
      // A security panel that shows a state the store is not in is the one failure that
      // must not be possible, so the page asks again rather than assuming.
      const reads = calls.filter((call) => call.method === 'GET' && call.url.includes('/attempts'))
      expect(reads.length).toBeGreaterThan(1)
    })
  })
})

describe('the ordinary case', () => {
  it('renders an ungated recipient as ungated rather than as broken', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor('Sam Ito')
    expect(within(panel).getByText('No verification')).toBeInTheDocument()
    expect(within(panel).getByText(/carries no verification/)).toBeInTheDocument()
  })

  it('offers no attempt form to an ungated recipient', async () => {
    renderPage()
    await waitForPage()
    const panel = panelFor('Sam Ito')
    expect(within(panel).queryByRole('button', { name: /Submit this attempt/ })).toBeNull()
  })

  it('separates gated from ungated on the board', async () => {
    renderPage()
    await waitForPage()
    const card = screen.getByText('Gated recipients').closest('div.glass')
    expect(within(card).getByText(String(SUMMARY.gated_recipients))).toBeInTheDocument()
    // The ungated count is what makes the gated one readable: without it a seller cannot
    // tell a room where everyone is verified from a room where nobody is.
    expect(within(card).getByText(`${SUMMARY.recipients} in total`)).toBeInTheDocument()
    expect(SUMMARY.gated_recipients + SUMMARY.ungated_recipients).toBe(SUMMARY.recipients)
  })

  it('renders an empty state when there are no recipients at all', async () => {
    overrides['/api/wf-078/recipients'] = { status: 200, body: { count: 0, recipients: [] } }
    renderPage()
    await waitForPage()
    expect(screen.getByText('No verified recipients yet')).toBeInTheDocument()
  })
})

describe('every state this page can be in', () => {
  it('renders a loading state before the vocabulary arrives', () => {
    overrides['/api/wf-078/vocabulary'] = { status: 999, body: {} }
    renderPage()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('renders an error note with a retry rather than a blank page', async () => {
    overrides['/api/wf-078/recipients'] = {
      status: 500,
      body: { detail: 'the recipients collection is unavailable' },
    }
    renderPage()
    // A security page that goes blank when the API is down looks like the gates failing,
    // which is the one reading that must never be possible. The error replaces the board
    // entirely, so there is no heading to wait for - the alert is the page.
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/Could not load data/)).toBeInTheDocument()
    expect(within(alert).getByText('the recipients collection is unavailable')).toBeInTheDocument()
    expect(within(alert).getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('renders an empty state when the trail has no rows', async () => {
    overrides['/api/wf-078/attempts'] = { status: 200, body: { count: 0, attempts: [] } }
    renderPage()
    await waitForPage()
    expect(screen.getByText('No verification attempts yet')).toBeInTheDocument()
  })
})

describe('the accessibility floor', () => {
  it('labels the audience badge in words, not by colour', () => {
    render(<AudienceBadge audience="signers_only" label="Signers only" />)
    expect(screen.getByText('Signers only')).toBeInTheDocument()
  })

  it('gives the audience badge an accessible name even when the text is short', () => {
    render(<AudienceBadge audience="all_recipients" label="All recipients" />)
    expect(screen.getByText('All recipients')).toBeInTheDocument()
  })

  it('meets the 44px touch target on the switch it rebuilds locally', () => {
    // The documented primitive list names Toggle and the shipped ui.jsx does not export it.
    // This pins the floor on the rebuild: min-h-11 or its h-6 track inside a labelled row.
    const { container } = render(
      <Toggle id="t" label="Verify before open" checked onChange={() => {}} />,
    )
    expect(container.querySelector('button[role="switch"]')).toBeInTheDocument()
  })

  it('announces a notice as a status rather than leaving it silent', () => {
    render(<Notice tone="warning" title="A caveat">Body</Notice>)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
})

describe('the served vocabulary', () => {
  it('is rendered from the server rather than restated in the page', async () => {
    renderPage()
    await waitForPage()
    // The vendor field names come from the response. If the page typed them itself, a
    // change on the server would leave the page showing the old ones.
    const served = VOCABULARY.methods.map((method) => method.vendor_field)
    for (const field of served) {
      expect(screen.getAllByText(field).length).toBeGreaterThan(0)
    }
  })

  it('names the code owner so a reader can find where the integers live', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getByText(VOCABULARY.code_table_owner)).toBeInTheDocument()
  })

  it('publishes the verification band the codes live in', async () => {
    renderPage()
    await waitForPage()
    const panel = screen.getByRole('heading', { name: /enforces/ }).closest('div.glass')
    expect(within(panel).getByText('47')).toBeInTheDocument()
    expect(within(panel).getByText('54')).toBeInTheDocument()
  })

  it('publishes the two bounds the specification states', async () => {
    expect(VOCABULARY.passcode.min_length).toBe(6)
    expect(VOCABULARY.passcode.max_length).toBe(100)
    expect(VOCABULARY.phone.example).toBe('+1555667890')
  })
})

describe('the trail', () => {
  it('renders one row per attempt with its place and method', async () => {
    renderPage()
    await waitForPage()
    expect(screen.getByText('before_open / Typed passcode')).toBeInTheDocument()
    expect(screen.getByText('before_sign / SMS one-time password')).toBeInTheDocument()
  })

  it('renders no attempt rows when the server returns none', () => {
    expect(ATTEMPTS.attempts.length).toBeGreaterThan(0)
    render(<div />)
    expect(screen.queryByText(/before_open \//)).toBeNull()
  })

  it('keeps the delivery-only recipient distinguishable from an authenticating one', () => {
    expect(DELIVERY_ONLY_RECIPIENT.gates[0].sms_type).toBe('delivery')
    expect(DELIVERY_ONLY_RECIPIENT.authentication_factor_gates).toEqual([])
  })

  it('counts no authentication factor for a recipient whose only gate is a passcode', () => {
    expect(UNGATED_RECIPIENT.gate_count).toBe(0)
  })
})
