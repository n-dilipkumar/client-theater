import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor, { ViewOnlyAccessPage } from './index.jsx'
import {
  AND_SEMANTICS,
  ASSUMPTION,
  GATES,
  LIMITATION,
  READ_SOURCE,
  SCOPE_OWNER,
  gateRows,
} from './api'
import { DecisionResult, GateList } from './primitives'

vi.mock('@/lib/api', () => ({
  apiRequest: vi.fn(async (path) => {
    if (path === '/records/room?limit=100') {
      return { records: [{ id: 'room_a', data: { name: 'Northwind' } }] }
    }
    throw new Error(`unmocked GET ${path}`)
  }),
}))

vi.mock('./api', async () => {
  const actual = await vi.importActual('./api')
  return {
    ...actual,
    listRooms: vi.fn(),
    viewOnlyApi: {
      vocabulary: vi.fn(),
      summary: vi.fn(),
      links: vi.fn(),
      roomPolicy: vi.fn(),
      setRoomPolicy: vi.fn(),
      updateLink: vi.fn(),
      decision: vi.fn(),
      download: vi.fn(),
      allDownloads: vi.fn(),
    },
  }
})

const { viewOnlyApi, listRooms } = await import('./api')

const VOCABULARY = {
  controls: [
    { field: 'bulk_download', label: 'Bulk download', cli_flag: '--bulk-download on|off', owned: true },
    { field: 'allow_download', label: 'Allow download', cli_flag: '--allow-download', owned: true },
    { field: 'can_download', label: 'Item download', cli_flag: '--download <on|off>', owned: false },
  ],
  defaults: { bulk_download: false, allow_download: false, can_download: false },
  collections: ['wf072_room_policy', 'wf072_link', 'wf072_view'],
  limitation: LIMITATION,
}

const LINK_VIEW_ONLY = { id: 'link_1', room_id: 'room_a', name: 'Default round', allow_download: false }
const LINK_OPEN = { id: 'link_2', room_id: 'room_a', name: 'Partner round', allow_download: true }

function decision(overrides = {}) {
  return {
    link_id: 'link_1',
    room_id: 'room_a',
    item_id: 'doc_1',
    download: true,
    state: 'download',
    gates: { room_bulk_download: true, link_allow_download: true, item_can_download: true },
    blocked_by: [],
    reason: 'All three controls permit this download.',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  listRooms.mockResolvedValue({ records: [{ id: 'room_a', data: { name: 'Northwind' } }] })
  viewOnlyApi.vocabulary.mockResolvedValue(VOCABULARY)
  viewOnlyApi.summary.mockResolvedValue({
    links: 2,
    allow_download: 1,
    view_only_links: 1,
    bulk_download: 1,
    rooms_configured: 1,
    downloads: 1,
    views: 1,
  })
  viewOnlyApi.links.mockResolvedValue({ room_id: 'room_a', count: 2, links: [LINK_VIEW_ONLY, LINK_OPEN] })
  viewOnlyApi.roomPolicy.mockResolvedValue({ room_id: 'room_a', configured: true, bulk_download: false })
  viewOnlyApi.setRoomPolicy.mockResolvedValue({ room_id: 'room_a', configured: true, bulk_download: true })
  viewOnlyApi.updateLink.mockResolvedValue({ ...LINK_VIEW_ONLY, allow_download: true })
  viewOnlyApi.allDownloads.mockResolvedValue({ count: 1, downloads: [{ id: 'v1', downloaded: true, download_type: 'pdf' }] })
})

async function renderPage() {
  render(<ViewOnlyAccessPage />)
  // The page shows a spinner until rooms and vocabulary resolve.
  const select = await screen.findByLabelText('Room')
  await userEvent.selectOptions(select, 'room_a')
  return select
}

describe('descriptor', () => {
  it('is discovered with the id the backend feature uses', () => {
    expect(descriptor.id).toBe('wf-072-enforce-view-only-access-and-block-bulk')
    expect(typeof descriptor.Component).toBe('function')
    expect(descriptor.iconPath).toMatch(/^M/)
  })
})

describe('the page', () => {
  it('states the AND before it shows any switch', async () => {
    await renderPage()
    expect(screen.getByText(AND_SEMANTICS)).toBeInTheDocument()
    expect(screen.getByText(LIMITATION)).toBeInTheDocument()
  })

  it('turns the room bulk download setting on', async () => {
    await renderPage()
    const toggle = await screen.findByRole('switch', { name: 'Bulk download' })
    await userEvent.click(toggle)
    await waitFor(() =>
      expect(viewOnlyApi.setRoomPolicy).toHaveBeenCalledWith('room_a', { bulk_download: true }),
    )
  })

  it('escalates a link to allow download', async () => {
    await renderPage()
    const toggles = await screen.findAllByRole('switch', { name: 'Allow download on this link' })
    expect(toggles.length).toBeGreaterThanOrEqual(1)
    await userEvent.click(toggles[0])
    await waitFor(() =>
      expect(viewOnlyApi.updateLink).toHaveBeenCalledWith('link_1', { allow_download: true }),
    )
  })

  it('checks one item and renders the three gates', async () => {
    viewOnlyApi.decision.mockResolvedValue(decision())
    await renderPage()
    const input = screen.getAllByLabelText('Check a download')[0]
    await userEvent.type(input, 'doc_1')
    await userEvent.click(screen.getAllByRole('button', { name: /check/i })[0])
    expect(await screen.findByText('Download permitted')).toBeInTheDocument()
    expect(screen.getByText('All three controls permit this download.')).toBeInTheDocument()
  })

  it('shows the refusal a real attempt returned', async () => {
    const refusal = decision({
      download: false,
      state: 'view_only',
      gates: { room_bulk_download: true, link_allow_download: true, item_can_download: false },
      blocked_by: ['item_can_download'],
      reason: "The item's own download grant did not permit this download.",
    })
    const error = new Error('view_only')
    error.status = 403
    error.decision = refusal
    viewOnlyApi.download.mockRejectedValue(error)
    await renderPage()
    const input = screen.getAllByLabelText('Check a download')[0]
    await userEvent.type(input, 'doc_1')
    await userEvent.click(screen.getAllByRole('button', { name: /attempt download/i })[0])
    expect(await screen.findByText('View-only: the download was refused')).toBeInTheDocument()
    expect(screen.getByText(/own download grant did not permit/)).toBeInTheDocument()
  })

  it('reads the download log back', async () => {
    await renderPage()
    expect(await screen.findByText(/leak by download is auditable/)).toBeInTheDocument()
  })
})

describe('the decision primitive', () => {
  it('maps every gate to its own row', () => {
    const rows = gateRows(decision())
    expect(rows.map((row) => row.name)).toEqual(GATES.map((gate) => gate.name))
    expect(rows.every((row) => row.permitted)).toBe(true)
  })

  it('labels a blocked gate with a word as well as a tone', () => {
    render(
      <DecisionResult
        decision={decision({
          download: false,
          state: 'view_only',
          gates: { room_bulk_download: false, link_allow_download: true, item_can_download: true },
          blocked_by: ['room_bulk_download'],
          reason: "The room's bulk download setting did not permit this download.",
        })}
      />,
    )
    expect(screen.getByText('View-only: the download was refused')).toBeInTheDocument()
    expect(screen.getAllByText('Blocked')).toHaveLength(1)
    expect(screen.getAllByText('Permitted')).toHaveLength(2)
  })

  it('renders nothing without a decision', () => {
    const { container } = render(<GateList rows={[]} state="download" />)
    expect(container.querySelectorAll('li')).toHaveLength(0)
  })
})

describe('the honesty sentences', () => {
  it('are non-empty and stable', () => {
    for (const sentence of [LIMITATION, SCOPE_OWNER, READ_SOURCE, ASSUMPTION]) {
      expect(typeof sentence).toBe('string')
      expect(sentence.length).toBeGreaterThan(20)
    }
    expect(READ_SOURCE).toMatch(/wf074_link_permission/)
  })
})
