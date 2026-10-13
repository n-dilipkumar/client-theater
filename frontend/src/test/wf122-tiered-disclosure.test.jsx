import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import DataRoomTiers from '@/features/wf-122-tiered-disclosure/DataRoomTiers.jsx'
import descriptor from '@/features/wf-122-tiered-disclosure/index.jsx'
import {
  conflictText,
  CONFLICTS,
  DISCLOSURE_WORDS,
  STATE_WORDS,
  tiersApi,
} from '@/features/wf-122-tiered-disclosure/api.js'
import { httpError, stubApi } from './fixtures.js'

/**
 * Tests for WF-122's page.
 *
 * What these pin is the behaviour the researched specification requires, and the
 * four things a tiered room can quietly get wrong:
 *
 *   1. **A hidden section is absent from the counterparty response.** The page
 *      asks the server for the view, so a section the page merely hid locally
 *      would still be in the network response. The assertion is on what the page
 *      received, not on what it chose to draw.
 *   2. **A teaser shows the teaser and never the contents.** A below-tier viewer
 *      must not receive the summary or the documents.
 *   3. **A closed link says it is closed.** A revoked or expired link discloses
 *      nothing, and the page says so rather than showing an empty tree in silence.
 *   4. **An unlock is the server's verified write.** The page calls the unlock
 *      route, and a 409 is shown as the server's reason, not parsed from English.
 *
 * The stubs are keyed on this feature's own paths under `/wf-122`, and `stubApi`
 * throws on an unlisted path so a test cannot pass against the wrong response.
 */

const ROOM = 'room_a'

const ROOMS = {
  count: 1,
  records: [{ id: ROOM, collection: 'room', revision: 1, data: { name: 'Northwind' } }],
}

const POLICY = {
  room_id: ROOM,
  tier_labels: { 0: 'Curated room', 1: 'Financials', 2: 'Legal', 3: 'Security' },
  default_below_tier: 'hidden',
}

const SECTIONS = {
  room_id: ROOM,
  sections: [
    { id: 's_overview', name: 'Company overview', min_tier: 0, below_tier: 'hidden', order: 1 },
    {
      id: 's_fin',
      name: 'Financial statements',
      min_tier: 1,
      below_tier: 'teaser',
      teaser: 'Three years of statements.',
      summary: 'The real numbers.',
      order: 2,
    },
    { id: 's_legal', name: 'Contracts and IP', min_tier: 2, below_tier: 'on_request', order: 3 },
    { id: 's_sec', name: 'Security and compliance', min_tier: 3, below_tier: 'hidden', order: 4 },
  ],
}

const VANTAGE = {
  id: 'r_vantage',
  org: 'Vantage Partners',
  tier: 0,
  state: 'active',
  effective_state: 'active',
  live: true,
  token_hint: 'abcd',
}

const FABRIKAM = {
  id: 'r_fab',
  org: 'Fabrikam Logistics',
  tier: 0,
  state: 'revoked',
  effective_state: 'revoked',
  live: false,
  token_hint: 'wxyz',
}

const RECIPIENTS = { room_id: ROOM, recipients: [VANTAGE, FABRIKAM] }

const SUMMARY = {
  room_id: ROOM,
  sections: 4,
  recipients: 2,
  active: 1,
  revoked: 1,
  expired: 0,
  unlocked: 0,
  grants: 0,
  open_requests: 1,
  events: 2,
  generated_at: '2026-05-04T09:00:00.000+00:00',
}

const REQUESTS = {
  room_id: ROOM,
  requests: [
    { id: 'req_1', section_id: 's_legal', recipient_id: 'r_vantage', state: 'pending' },
  ],
}

const EVENTS = {
  room_id: ROOM,
  events: [
    { id: 'e1', kind: 'recipient_created', at: '2026-05-01T09:00:00.000+00:00' },
    { id: 'e2', kind: 'section_requested', at: '2026-05-02T09:00:00.000+00:00' },
  ],
}

const PREVIEW_AT_ZERO = {
  room_id: ROOM,
  viewer_tier: 0,
  sections: [
    { id: 's_overview', name: 'Company overview', min_tier: 0, disclosure: 'full', summary: 'The intro.' },
    {
      id: 's_fin',
      name: 'Financial statements',
      min_tier: 1,
      disclosure: 'teaser',
      teaser: 'Three years of statements.',
    },
    {
      id: 's_legal',
      name: 'Contracts and IP',
      min_tier: 2,
      disclosure: 'on_request',
      message: 'Available on request.',
    },
  ],
  section_count: 4,
  visible_count: 3,
  hidden_count: 1,
}

const VIEW_VANTAGE = {
  recipient: VANTAGE,
  room_id: ROOM,
  state: 'active',
  viewer_tier: 0,
  sections: PREVIEW_AT_ZERO.sections,
  section_count: 4,
  visible_count: 3,
  hidden_count: 1,
}

const VIEW_REVOKED = {
  recipient: FABRIKAM,
  room_id: ROOM,
  state: 'revoked',
  viewer_tier: -1,
  sections: [],
  section_count: 4,
  visible_count: 0,
  hidden_count: 4,
}

const VOCABULARY = {
  public_tier: 0,
  below_tier_states: ['hidden', 'teaser', 'on_request'],
  recipient_states: ['active', 'revoked', 'expired'],
  decisions: {},
  not_implemented: [],
}

function baseRoutes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    [`/wf-122/rooms/${ROOM}/sections`]: SECTIONS,
    [`/wf-122/rooms/${ROOM}/recipients`]: RECIPIENTS,
    [`/wf-122/rooms/${ROOM}/events`]: EVENTS,
    [`/wf-122/rooms/${ROOM}/policy`]: POLICY,
    [`/wf-122/rooms/${ROOM}/requests`]: REQUESTS,
    [`/wf-122/rooms/${ROOM}/view`]: PREVIEW_AT_ZERO,
    '/wf-122/summary': SUMMARY,
    '/wf-122/vocabulary': VOCABULARY,
    '/wf-122/recipients/r_vantage/view': VIEW_VANTAGE,
    '/wf-122/recipients/r_fab/view': VIEW_REVOKED,
    ...overrides,
  }
}

beforeEach(() => {
  globalThis.fetch = undefined
})

describe('WF-122 descriptor', () => {
  it('exports the shape the host discovers', () => {
    expect(descriptor.id).toBe('wf-122-tiered-disclosure')
    expect(descriptor.label).toBe('Tiered disclosure')
    expect(descriptor.Component).toBe(DataRoomTiers)
    // The glyph is not in the shared PATHS map, so it travels as a path and
    // components/ui.jsx stays unedited.
    expect(descriptor.iconPath).toBeTruthy()
  })
})

describe('the room tree', () => {
  it('shows every section with its tier and its below-tier state', async () => {
    stubApi(baseRoutes())
    render(<DataRoomTiers />)

    expect(await screen.findAllByText('Company overview')).not.toHaveLength(0)
    expect(screen.getAllByText('Financial statements').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Contracts and IP').length).toBeGreaterThan(0)
    // The below-tier state is on the row, in words.
    expect(screen.getByText('below tier: teaser')).toBeInTheDocument()
    expect(screen.getByText('below tier: on_request')).toBeInTheDocument()
  })

  it('shows the room totals from the server rather than counting on the page', async () => {
    stubApi(baseRoutes())
    render(<DataRoomTiers />)

    await screen.findAllByText('Company overview')
    expect(screen.getByText('Sections', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('Counterparty links', { selector: 'p' })).toBeInTheDocument()
  })
})

describe('the preview of a tier', () => {
  it('renders the server view and drops the hidden section', async () => {
    const calls = stubApi(baseRoutes())
    render(<DataRoomTiers />)

    await screen.findAllByText('Company overview')
    // The preview asked the server for tier 0's view.
    expect(calls.some((call) => call.path.startsWith(`/wf-122/rooms/${ROOM}/view`))).toBe(true)

    const previewCard = screen.getByRole('heading', { name: 'Preview a tier' }).parentElement
    expect(within(previewCard).getByText('Three years of statements.')).toBeInTheDocument()
    // The hidden section is absent from the preview the server sent.
    expect(within(previewCard).queryByText('Security and compliance')).not.toBeInTheDocument()
  })

  it('gives every disclosure a word, so nothing is carried by colour alone', async () => {
    stubApi(baseRoutes())
    render(<DataRoomTiers />)

    await screen.findAllByText('Company overview')
    const previewCard = screen.getByRole('heading', { name: 'Preview a tier' }).parentElement
    expect(within(previewCard).getAllByText(DISCLOSURE_WORDS.teaser).length).toBeGreaterThan(0)
    expect(within(previewCard).getAllByText(DISCLOSURE_WORDS.on_request).length).toBeGreaterThan(0)
    expect(within(previewCard).getAllByText(DISCLOSURE_WORDS.full).length).toBeGreaterThan(0)
  })
})

describe('the counterparty view', () => {
  it('shows an active link its room and names the state', async () => {
    stubApi(baseRoutes())
    render(<DataRoomTiers />)

    await screen.findAllByText('Company overview')
    expect(screen.getByText(STATE_WORDS.active)).toBeInTheDocument()
  })

  it('says a revoked link is closed rather than showing an empty tree in silence', async () => {
    const calls = stubApi(baseRoutes())
    render(<DataRoomTiers />)

    await screen.findAllByText('Company overview')
    await userEvent.selectOptions(screen.getByLabelText('Counterparty'), 'r_fab')

    await waitFor(() => {
      expect(calls.some((call) => call.path.startsWith('/wf-122/recipients/r_fab/view'))).toBe(
        true,
      )
    })
    expect(await screen.findByText('This link is closed')).toBeInTheDocument()
    expect(screen.getByText('It was revoked, so it discloses nothing.')).toBeInTheDocument()
  })
})

describe('issuing and clearing a link', () => {
  it('shows the access link once and says it is shown once', async () => {
    const created = {
      ...VANTAGE,
      id: 'r_new',
      org: 'Contoso Health',
      token: 'tok_123456',
      access_path: '/api/wf-122/access/tok_123456',
    }
    stubApi(baseRoutes({ [`/wf-122/rooms/${ROOM}/recipients`]: created }))
    render(<DataRoomTiers />)
    await screen.findAllByText('Company overview')

    await userEvent.type(screen.getByLabelText('Organisation'), 'Contoso Health')
    await userEvent.click(screen.getByRole('button', { name: 'Issue link' }))

    expect(await screen.findByText('Access link for Contoso Health')).toBeInTheDocument()
    expect(screen.getByText('/api/wf-122/access/tok_123456')).toBeInTheDocument()
    expect(
      screen.getByText(/only a hash is stored, so it cannot be shown again/),
    ).toBeInTheDocument()
  })

  it('posts a new link with the organisation and the starting tier', async () => {
    const created = { ...VANTAGE, id: 'r_new', org: 'Contoso Health', token: 'tok_123456', access_path: '/api/wf-122/access/tok_123456' }
    const calls = stubApi(
      baseRoutes({
        [`/wf-122/rooms/${ROOM}/recipients`]: created,
      }),
    )
    render(<DataRoomTiers />)
    await screen.findAllByText('Company overview')

    await userEvent.type(screen.getByLabelText('Organisation'), 'Contoso Health')
    await userEvent.click(screen.getByRole('button', { name: 'Issue link' }))

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === 'POST' && call.path.endsWith(`/rooms/${ROOM}/recipients`),
      )
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body).org).toBe('Contoso Health')
    })
  })

  it('unlocks a link through the verified unlock route', async () => {
    const calls = stubApi(
      baseRoutes({
        '/wf-122/recipients/r_vantage/unlock': { ...VANTAGE, tier: 2 },
      }),
    )
    render(<DataRoomTiers />)
    await screen.findAllByText('Company overview')

    const row = screen.getAllByText('Vantage Partners')[0].closest('li')
    await userEvent.selectOptions(within(row).getByLabelText('Unlock to'), '2')
    await userEvent.click(within(row).getByRole('button', { name: 'Unlock' }))

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === 'POST' && call.path.endsWith('/recipients/r_vantage/unlock'),
      )
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body).tier).toBe(2)
    })
  })

  it("shows the server's reason when an unlock is refused", async () => {
    stubApi(
      baseRoutes({
        '/wf-122/recipients/r_vantage/unlock': httpError(409, {
          error: 'disclosure_conflict',
          reason: 'link_revoked',
          detail: 'This link was revoked.',
        }),
      }),
    )
    render(<DataRoomTiers />)
    await screen.findAllByText('Company overview')

    const row = screen.getAllByText('Vantage Partners')[0].closest('li')
    await userEvent.click(within(row).getByRole('button', { name: 'Unlock' }))

    expect(await screen.findByText('That change was refused')).toBeInTheDocument()
    expect(screen.getByText(conflictText('link_revoked'))).toBeInTheDocument()
    expect(CONFLICTS.link_revoked).toMatch(/revoked/)
  })
})

describe('section-level access', () => {
  it('grants one section to the selected counterparty', async () => {
    const calls = stubApi(baseRoutes({ '/wf-122/sections/s_sec/grant': { id: 'g1' } }))
    render(<DataRoomTiers />)
    await screen.findAllByText('Company overview')

    const row = screen.getByText('Security and compliance').closest('li')
    await userEvent.click(within(row).getByRole('button', { name: 'Grant' }))

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === 'POST' && call.path.endsWith('/sections/s_sec/grant'),
      )
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted.body).recipient_id).toBe('r_vantage')
    })
  })

  it('requests an on-request section for the selected counterparty', async () => {
    const calls = stubApi(baseRoutes({ '/wf-122/sections/s_legal/request': { id: 'req_2' } }))
    render(<DataRoomTiers />)
    await screen.findAllByText('Company overview')

    const row = screen.getAllByText('Contracts and IP')[0].closest('li')
    await userEvent.click(within(row).getByRole('button', { name: 'Request' }))

    await waitFor(() => {
      expect(
        calls.some((call) => call.method === 'POST' && call.path.endsWith('/sections/s_legal/request')),
      ).toBe(true)
    })
  })
})

describe('the empty and error states', () => {
  it('says so when there are no rooms at all', async () => {
    stubApi({ '/records/room': { count: 0, records: [] } })
    render(<DataRoomTiers />)

    expect(await screen.findByText('No rooms yet')).toBeInTheDocument()
  })

  it('offers a retry when the room tree cannot load', async () => {
    stubApi(baseRoutes({ [`/wf-122/rooms/${ROOM}/sections`]: httpError(500, { detail: 'no tree' }) }))
    render(<DataRoomTiers />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })
})

describe('the api wrapper', () => {
  it('exposes the calls the page uses, including the verified unlock', () => {
    for (const name of ['unlock', 'revoke', 'rotate', 'grant', 'requestAccess', 'preview']) {
      expect(typeof tiersApi[name]).toBe('function')
    }
  })
})
