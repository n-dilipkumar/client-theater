import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import descriptor, { AudiencePage } from './index.jsx'
import {
  CAPS,
  LIMITATION,
  NOT_PROOF,
  SCOPE_OWNER,
  SCOPE_STATES,
  hiddenReason,
  itemTypeLabel,
  membershipLabel,
  scopeStateLabel,
} from './api'
import { AUDIENCE_ICON, DOWNLOAD_ICON, VIEW_ICON } from './icons'
import { FlagToggle, PermissionGrid, Select, StateBadge } from './primitives'

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
    listRooms: vi.fn(async () => ({ records: [{ id: 'room_a', data: { name: 'Northwind' } }] })),
    audienceApi: {
      vocabulary: vi.fn(),
      summary: vi.fn(),
      groups: vi.fn(),
      links: vi.fn(),
      decisions: vi.fn(),
      members: vi.fn(),
      addMembers: vi.fn(),
      removeMember: vi.fn(),
      createGroup: vi.fn(),
      updateGroup: vi.fn(),
      groupPermissions: vi.fn(),
      setGroupPermissions: vi.fn(),
      linkPermissions: vi.fn(),
      setLinkPermissions: vi.fn(),
      view: vi.fn(),
    },
  }
})

const { audienceApi, listRooms } = await import('./api')

const GROUP = {
  id: 'grp_1',
  room_id: 'room_a',
  name: 'Co-investors',
  allow_all: false,
  domains: ['@sequoia.example'],
  member_count: 1,
  link_count: 1,
  permission_count: 1,
  revision: 3,
}

const DOC = { item_id: 'doc_1', item_type: 'dataroom_document', name: 'Enterprise deck', can_view: true, can_download: true, state: 'visible', deny_reason: null, row_present: true, auto_opened: false }
const FOLDER_VIEW_ONLY = { item_id: 'folder_1', item_type: 'dataroom_folder', name: 'Decks', can_view: true, can_download: false, state: 'view_only', deny_reason: null, row_present: true, auto_opened: true }
const UNGRANTED = { item_id: 'doc_2', item_type: 'dataroom_document', name: 'Contract draft', can_view: false, can_download: false, state: 'hidden_no_permission', deny_reason: 'no_permission_row', row_present: false, auto_opened: false }
const REVOKED = { item_id: 'doc_3', item_type: 'dataroom_document', name: 'Pricing', can_view: false, can_download: false, state: 'hidden_can_view_false', deny_reason: 'can_view_is_false', row_present: true, auto_opened: false }

function gridResponse(items) {
  return {
    group_id: 'grp_1',
    scope: 'group',
    semantics: 'delta',
    semantics_text: 'Entries you send are upserted.',
    count: items.length,
    items,
    granted: items.filter((row) => row.can_view).length,
    hidden: items.filter((row) => !row.can_view).length,
    dangling: [],
    no_items_note: null,
    items_page_truncated: false,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  listRooms.mockResolvedValue({ records: [{ id: 'room_a', data: { name: 'Northwind' } }] })
  audienceApi.vocabulary.mockResolvedValue({
    item_types: [
      { id: 'dataroom_document', label: 'Document', collection: 'document' },
      { id: 'dataroom_folder', label: 'Folder', collection: 'documentFolder' },
    ],
    permission_entry: { required: ['item_id', 'item_type', 'can_view', 'can_download'], closed: true },
    scopes: [
      { id: 'group', semantics: 'delta', description: 'Entries you send are upserted.' },
      { id: 'link', semantics: 'full_replace', description: 'The payload is the complete desired state.' },
    ],
    membership: {
      steps: [
        { id: 'by_email', label: 'An explicit member email.' },
        { id: 'by_domain', label: 'An email domain on the group.' },
        { id: 'allow_all', label: 'The group allows anyone.' },
      ],
      allow_all: 'The email and domain checks are skipped.',
    },
    caps: { domains: 100, members_per_call: 500, permissions_per_call: 1000 },
    link_gating: { email_gated: true, note: 'There is no setting that turns this off.' },
    scope_conflict: { rule: 'group_link_rejects_link_overrides', message: 'Switch the link to general.' },
    link_scope_states: SCOPE_STATES,
    limitation: LIMITATION,
    scope_owner: SCOPE_OWNER,
  })
  audienceApi.summary.mockResolvedValue({
    groups: 2,
    members: 3,
    links: 1,
    group_links: 1,
    general_links: 0,
    permissions: 1,
    groups_with_no_permissions: 1,
    groups_with_no_permissions_names: ['Data room reviewers'],
    items: 4,
  })
  audienceApi.groups.mockResolvedValue({ room_id: '', count: 1, groups: [GROUP] })
  audienceApi.links.mockResolvedValue({ room_id: '', count: 0, links: [] })
  audienceApi.decisions.mockResolvedValue({
    count: 1,
    decisions: [{ id: 'DERIVED_X', question: 'What X?', chosen: 'a', rejected_because: 'because b.' }],
  })
  audienceApi.members.mockResolvedValue({
    group_id: 'grp_1',
    count: 1,
    members: [{ id: 'mem_1', email: 'jane@sequoia.example', added_at: '2026-10-05T08:00:00.000+00:00' }],
  })
  audienceApi.groupPermissions.mockResolvedValue(gridResponse([DOC, FOLDER_VIEW_ONLY, UNGRANTED, REVOKED]))
})

describe('the descriptor', () => {
  it('exports the id the backend and the folder both name', () => {
    expect(descriptor.id).toBe('wf-074-scope-visibility-to-an-audience-with-per-item')
    expect(descriptor.id).toBe('wf-074-scope-visibility-to-an-audience-with-per-item')
  })

  it('carries a label, a shared icon fallback and its own path', () => {
    expect(descriptor.label).toBe('Audience permissions')
    expect(descriptor.icon).toBe('audit')
    expect(descriptor.iconPath).toBe(AUDIENCE_ICON)
    expect(typeof descriptor.Component).toBe('function')
  })

  it('sorts next to the other WF-070s by order', () => {
    expect(descriptor.order).toBe(740)
  })
})

describe('the honesty the page leads with', () => {
  it('renders the default-deny rule above the fold', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    // getAllByText, because the sentence appears twice by design: once in the standing notice at
    // the top of the page and once on the audience card whose grid is empty. Both are the same
    // claim, and a test that insisted on one occurrence would fail the first time somebody made
    // the second one read better.
    const found = screen.getAllByText(/A new audience sees nothing until you grant permissions/i)
    expect(found.length).toBeGreaterThanOrEqual(1)
    // And it is in the first Notice on the page, which is the position the design requires.
    const firstNotice = document.querySelector('[role="status"]')
    expect(firstNotice.textContent).toMatch(/A new audience sees nothing until you grant permissions/i)
  })

  it('renders the limitation and the not-proof sentence', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    expect(screen.getByText(/does not invite anybody/i)).toBeTruthy()
    expect(screen.getByText(/does not establish who used the address/i)).toBeTruthy()
  })

  it('names the scope ownership and the two assumptions', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    expect(screen.getByText(/owns the per-item access control list/i)).toBeTruthy()
    expect(screen.getByText(/assumptions rather than sourced facts/i)).toBeTruthy()
  })

  it('names the audiences granted nothing, which is the shipped default', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    expect(screen.getByText(/Data room reviewers/)).toBeTruthy()
  })
})

describe('the two flags on the grid', () => {
  it('gives view and download a column each rather than one allowed word', () => {
    render(
      <PermissionGrid
        rows={[DOC, FOLDER_VIEW_ONLY]}
        itemTypeLabels={{ dataroom_document: 'Document', dataroom_folder: 'Folder' }}
      />,
    )
    expect(screen.getByRole('columnheader', { name: 'View' })).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: 'Download' })).toBeTruthy()
    expect(screen.queryByRole('columnheader', { name: /allowed/i })).toBeNull()
  })

  it('renders the four states with words, not colour alone', () => {
    render(
      <PermissionGrid
        rows={[DOC, FOLDER_VIEW_ONLY, UNGRANTED, REVOKED]}
        itemTypeLabels={{ dataroom_document: 'Document', dataroom_folder: 'Folder' }}
      />,
    )
    expect(screen.getByText('Visible and downloadable')).toBeTruthy()
    expect(screen.getByText('View only')).toBeTruthy()
    // The two denials carry different words, because they are different facts.
    expect(screen.getByText('No grant yet')).toBeTruthy()
    expect(screen.getByText('Revoked')).toBeTruthy()
  })

  it('says which folder was auto-opened rather than chosen', () => {
    render(
      <PermissionGrid
        rows={[FOLDER_VIEW_ONLY]}
        itemTypeLabels={{ dataroom_folder: 'Folder' }}
      />,
    )
    expect(screen.getByText(/Opened for an item inside it, not chosen by a rep/)).toBeTruthy()
  })

  it('offers a flag control per row when a write is possible', async () => {
    const onToggle = vi.fn()
    render(
      <PermissionGrid
        rows={[DOC]}
        itemTypeLabels={{ dataroom_document: 'Document' }}
        onToggle={onToggle}
      />,
    )
    await userEvent.click(screen.getByRole('checkbox', { name: /May download/i }))
    expect(onToggle).toHaveBeenCalledWith(DOC, 'can_download', false)
  })

  it('renders read-only flags as words rather than controls when no write is possible', () => {
    render(
      <PermissionGrid
        rows={[DOC]}
        itemTypeLabels={{ dataroom_document: 'Document' }}
        readOnlyReason="Set these on the group instead."
      />,
    )
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.getByText('Set these on the group instead.')).toBeTruthy()
  })

  it('says a room with no library rows has nothing to grant', () => {
    render(<PermissionGrid rows={[]} />)
    expect(screen.getByText(/there is nothing to grant/i)).toBeTruthy()
  })
})

describe('the flag control', () => {
  it('is a checkbox, not a switch: a grant is not a setting on the link', () => {
    render(<FlagToggle id="f1" label="May view" checked onChange={() => {}} />)
    const box = screen.getByRole('checkbox', { name: 'May view' })
    expect(box.getAttribute('role')).toBeNull()
  })

  it('has a real label, so nothing is placeholder-as-label', () => {
    render(<FlagToggle id="f2" label="May download" hint="Independent of view." checked onChange={() => {}} />)
    // The accessible name carries the label and the hint together, because the hint is inside
    // the same <label>. That is deliberate: a screen reader user hears the caveat with the flag
    // rather than having to find it separately.
    expect(screen.getByRole('checkbox', { name: /May download.*Independent of view\./s })).toBeTruthy()
    expect(screen.getByText('Independent of view.')).toBeTruthy()
  })
})

describe('the two empty link states', () => {
  it('says a never-scoped link shows the whole room', () => {
    render(<PermissionGrid rows={[DOC]} itemTypeLabels={{ dataroom_document: 'Document' }} />)
    expect(scopeStateLabel('unscoped')).toBe('Never scoped')
  })

  it('gives cleared and unscoped different labels and meanings', () => {
    const unscoped = SCOPE_STATES.find((state) => state.id === 'unscoped')
    const cleared = SCOPE_STATES.find((state) => state.id === 'cleared')
    expect(unscoped.meaning).not.toBe(cleared.meaning)
    expect(cleared.meaning).toMatch(/Every item is hidden/)
  })
})

describe('the vocabulary the grid reads', () => {
  it('renders the three size caps the server sends', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    // Scoped to the caps panel. A bare number appears elsewhere on the page (an item count, a
    // member total), so an unscoped getByText would pass for the wrong reason or fail for one.
    const panel = screen.getByText('The three size caps').closest('div')
    const lines = within(panel)
      .getAllByRole('listitem')
      .map((node) => node.textContent.replace(/\s+/g, ' ').trim())
    expect(lines).toContain(`${CAPS.domains} domains on a group`)
    expect(lines).toContain(`${CAPS.membersPerCall} member addresses per call`)
    expect(lines).toContain(`${CAPS.permissionsPerCall} permission entries per call`)
  })

  it('renders the membership steps in the order the server sends', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    const items = screen.getAllByRole('listitem').map((node) => node.textContent)
    const emailAt = items.findIndex((text) => text?.includes('explicit member email'))
    const domainAt = items.findIndex((text) => text?.includes('domain on the group'))
    const allowAt = items.findIndex((text) => text?.includes('allows anyone'))
    expect(emailAt).toBeLessThan(domainAt)
    expect(domainAt).toBeLessThan(allowAt)
  })

  it('names the delta and the full-replace semantics side by side', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    const panel = screen.getByText('The two scopes write differently').closest('div')
    const scopeLines = within(panel).getAllByRole('listitem').map((node) => node.textContent)
    expect(scopeLines.some((line) => line.includes('group (delta)'))).toBe(true)
    expect(scopeLines.some((line) => line.includes('link (full_replace)'))).toBe(true)
  })
})

describe('the helper vocabulary', () => {
  it('labels a membership step by id', () => {
    expect(membershipLabel('by_email')).toMatch(/explicit member email/i)
    expect(membershipLabel('not_a_member')).toMatch(/not a member/i)
  })

  it('labels an item type by id', () => {
    expect(itemTypeLabel('dataroom_folder')).toBe('Folder')
    expect(itemTypeLabel('dataroom_document')).toBe('Document')
  })

  it('distinguishes the two denials in words', () => {
    expect(hiddenReason(UNGRANTED)).toBe('No grant yet')
    expect(hiddenReason(REVOKED)).toBe('Revoked by a rep')
    expect(hiddenReason(DOC)).toBeNull()
  })
})

describe('the states every panel must render', () => {
  it('renders a loading state before anything resolves', () => {
    audienceApi.summary.mockReturnValue(new Promise(() => {}))
    render(<AudiencePage />)
    expect(screen.getByText(/Loading audience permissions/i)).toBeTruthy()
  })

  it('renders an error state rather than a blank grid when the API is down', async () => {
    audienceApi.groups.mockRejectedValue(new Error('backend down'))
    render(<AudiencePage />)
    await waitFor(() => expect(screen.getByText(/backend down/i)).toBeTruthy())
  })

  it('renders an empty state when no audience exists yet', async () => {
    audienceApi.groups.mockResolvedValue({ room_id: '', count: 0, groups: [] })
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    expect(screen.getByText(/No audiences yet/i)).toBeTruthy()
    expect(screen.getByText(/sees nothing until you do/i)).toBeTruthy()
  })

  it('renders an empty state for links rather than nothing', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    expect(screen.getByText(/No links yet/i)).toBeTruthy()
  })
})

describe('adding a member', () => {
  it('reports that no invitation email was sent', async () => {
    audienceApi.addMembers.mockResolvedValue({
      group_id: 'grp_1',
      added: ['jane@acme.example'],
      added_count: 1,
      skipped: [],
      skipped_count: 0,
      invitations_sent: 0,
      invitation_note: 'No invitation email is sent.',
      member_count: 2,
    })
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    await userEvent.type(screen.getByLabelText(/Add member addresses/i), 'jane@acme.example')
    await userEvent.click(screen.getByRole('button', { name: /Add these addresses/i }))
    await waitFor(() => expect(audienceApi.addMembers).toHaveBeenCalledWith('grp_1', ['jane@acme.example']))
    expect(await screen.findByText(/1 address\(es\) added/i)).toBeTruthy()
  })

  it('says an address already present was skipped rather than failing', async () => {
    audienceApi.addMembers.mockResolvedValue({
      added: [],
      added_count: 0,
      skipped: ['jane@acme.example'],
      skipped_count: 1,
      invitations_sent: 0,
      invitation_note: 'No invitation email is sent.',
    })
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    await userEvent.type(screen.getByLabelText(/Add member addresses/i), 'jane@acme.example')
    await userEvent.click(screen.getByRole('button', { name: /Add these addresses/i }))
    expect(await screen.findByText(/already a member/i)).toBeTruthy()
  })

  it('shows the not-proof sentence on the membership result', async () => {
    audienceApi.addMembers.mockResolvedValue({
      added: ['jane@acme.example'],
      added_count: 1,
      skipped: [],
      skipped_count: 0,
      invitations_sent: 0,
      invitation_note: 'No invitation email is sent.',
    })
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    await userEvent.type(screen.getByLabelText(/Add member addresses/i), 'jane@acme.example')
    await userEvent.click(screen.getByRole('button', { name: /Add these addresses/i }))
    const banner = await screen.findByText(/1 address\(es\) added/i)
    const region = banner.closest('[role="status"]')
    expect(within(region).getByText(/does not establish who used the address/i)).toBeTruthy()
  })
})

describe('the accessibility floor', () => {
  it('gives every control a visible label and no emoji icon', async () => {
    render(<AudiencePage />)
    await screen.findByText('Audience permissions')
    // The room picker is the only select, and it has a label.
    expect(screen.getByLabelText('Room')).toBeTruthy()
    // Every control on the rendered page carries a name. A control whose only label is an icon or
    // a placeholder is the accessibility failure this asserts, so the check walks the DOM rather
    // than trusting the markup by eye.
    for (const node of document.querySelectorAll('button, input, select, textarea')) {
      const labelled =
        node.getAttribute('aria-label') ||
        node.textContent?.trim() ||
        (node.id && document.querySelector(`label[for="${node.id}"]`)?.textContent)
      expect(labelled, `unlabelled control: ${node.outerHTML.slice(0, 90)}`).toBeTruthy()
    }
  })

  it('keeps every control at the 44px touch target floor', () => {
    render(
      <>
        <FlagToggle id="t1" label="May view" checked onChange={() => {}} />
        <Select id="s1" value="a" onChange={() => {}}>
          <option value="a">A</option>
        </Select>
      </>,
    )
    const row = screen.getByLabelText('May view').closest('div')
    expect(row.className).toMatch(/min-h-11/)
    expect(screen.getByRole('combobox').className).toMatch(/min-h-11/)
  })

  it('states the badge as well as colouring it', () => {
    render(<StateBadge row={REVOKED} />)
    expect(screen.getByText('Revoked')).toBeTruthy()
  })
})

describe('the icons', () => {
  it('are path strings, not additions to the shared map', () => {
    for (const path of [AUDIENCE_ICON, VIEW_ICON, DOWNLOAD_ICON]) {
      expect(path).toMatch(/^M/)
      expect(path).not.toMatch(/[\u{1F300}-\u{1FAFF}]/u)
    }
  })
})
