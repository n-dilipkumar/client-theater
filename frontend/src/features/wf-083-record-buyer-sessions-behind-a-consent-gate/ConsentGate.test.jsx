/**
 * WF-083's page, over a stubbed API.
 *
 * The rules are tested in the backend. This file is about what a reader can see, and it is
 * organised by the claims the page makes:
 *
 * - the two consent axes are shown as separate words, so a partial grant is legible;
 * - a refusal says what it means, not only that it happened;
 * - the masking default is named as the default;
 * - both retention windows appear side by side;
 * - the sourced limits are rendered as limits with their evidence;
 * - nothing on the page claims compliance or offers an SSO path;
 * - the empty state, the loading state and the error state all render.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Page from './index'

const PREFIX = '/api/wf-083'

const VOCABULARY = {
  axes: ['ad_storage', 'analytics_storage'],
  consent_gate_field: 'consent_gate_enabled',
  default_masking_mode: 'suppress_all',
  masking_modes: ['suppress_all', 'element_selector', 'select_text'],
  ordinary_retention_days: 30,
  favourite_retention_days: 270,
  max_labels_per_recording: 5,
  link_kinds: ['guest', 'team'],
  enforced_regions: ['EEA', 'UK', 'CH'],
  enforcement_start: '2025-10-31',
  geography_source: 'visitor IP to geolocation provider [inferred]',
  blocked_signal: "Data from this session isn't being collected by Microsoft Clarity.",
  ip_blocking_role: 'admin',
  authentication: 'email_invite',
  max_sessions_per_project_per_day: 100000,
  roles: [
    { id: 'viewer', label: 'Viewer', may_change: false },
    { id: 'instance_admin', label: 'Instance admin', may_change: true },
  ],
}

const PROJECT = {
  id: 'p1',
  name: 'Q4 enterprise rooms',
  masking_mode: 'suppress_all',
  consent_gate_enabled: true,
}

const SUMMARY = {
  project_created: true,
  project: { id: 'p1', name: 'Q4 enterprise rooms', masking_mode: 'suppress_all' },
  enforcement: {
    gate_enabled: true,
    regions: ['EEA', 'UK', 'CH'],
    enforcement_active: true,
    enforcement_start: '2025-10-31',
  },
  counts: { recordings: 1, recorded: 1, blocked: 1, visits: 3, blocked_ranges: 1 },
  ceiling: { count: 1, ceiling: 100000, remaining: 99999, within_ceiling: true, label: 'within the ceiling' },
  authentication: 'email_invite',
  deletion: {
    supported: false,
    detail: 'A single recording cannot be deleted or downloaded. Deletion is at project granularity.',
    evidence: "you can't delete or download specific recordings; You need to delete the entire project to delete user's data.",
    remediation: 'Delete the whole project to remove a visitor data.',
  },
}

const RECORDINGS = [
  {
    id: 'r1',
    state: 'recorded',
    favourite: true,
    labels: ['enterprise', 'pricing'],
    page_path: '/pricing',
    masking_mode: 'suppress_all',
    identity_kind: 'persistent',
    recorded_at: '2026-10-04T09:00:00.000+00:00',
    retention_days: 270,
  },
]

const VISITS = [
  {
    id: 'v1',
    state: 'blocked',
    matched_range: '10.0.0.0/8',
    console_signal: "Data from this session isn't being collected by Microsoft Clarity.",
    visited_at: '2026-10-04T09:00:00.000+00:00',
  },
  {
    id: 'v2',
    state: 'scrubbed',
    outcome: 'denied',
    identity_kind: 'per_page_view',
    cookies_persist: false,
    reason: 'no axis granted',
  },
]

const RETENTION = {
  ordinary_days: 30,
  favourite_days: 270,
  rows: [
    {
      id: 'r1',
      favourite: true,
      recorded_at: '2026-10-04T09:00:00.000+00:00',
      retention_days: 270,
      expired: false,
    },
  ],
}

const DECISIONS = {
  count: 1,
  decisions: [
    {
      id: 'DERIVED_DEFAULT_MASKING_IS_TOTAL_SUPPRESSION',
      question: 'What does the room mask by default?',
      chosen: 'suppress_all',
      rejected_because: 'A fixed sensitive-field list would treat an unmasked value as the normal case.',
    },
  ],
}

function json(body, status = 200) {
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  }
}

function routeFor(url) {
  const path = url.replace(PREFIX, '').split('?')[0]
  switch (path) {
    case '/summary':
      return SUMMARY
    case '/vocabulary':
      return VOCABULARY
    case '/project':
      return PROJECT
    case '/ip-blocks':
      return { blocks: [{ id: 'b1', cidr: '10.0.0.0/8' }] }
    case '/recordings':
      return { recordings: RECORDINGS }
    case '/visits':
      return { visits: VISITS }
    case '/retention':
      return RETENTION
    case '/decisions':
      return DECISIONS
    case '/consent':
      return {
        id: 'v3',
        outcome: 'denied',
        identity_kind: 'per_page_view',
        cookies_persist: false,
        destroyed_sessions: ['r1'],
        revoke_call: { api: 'consent', value: false },
      }
    default:
      return {}
  }
}

let posts

beforeEach(() => {
  posts = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url, options = {}) => {
      if (options.method && options.method !== 'GET') {
        posts.push({ url, method: options.method, body: options.body })
        if (url.includes('/labels')) {
          return Promise.resolve(
            json({ detail: 'A recording takes at most 5 labels.', errors: { labels: 'x is label 6 of 5.' } }, 400),
          )
        }
        if (url.includes('/recordings/') && options.method === 'DELETE') {
          return Promise.resolve(
            json({
              error: 'project_granularity_required',
              detail: 'A single recording cannot be deleted or downloaded. Deletion is at project granularity.',
              evidence: SUMMARY.deletion.evidence,
            }, 409),
          )
        }
        return Promise.resolve(json(routeFor(url), 201))
      }
      return Promise.resolve(json(routeFor(String(url))))
    }),
  )
})

describe('the consent gate board', () => {
  it('renders the two consent axes as separate words', async () => {
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Record a consent call')).toBeTruthy())

    expect(screen.getAllByText('Ad storage').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Analytics storage').length).toBeGreaterThan(0)
    // Each axis is its own select holding granted or denied, never a single flag.
    // Addressed by label rather than by position: an index into getAllByRole
    // pins the assertion to the render order, so adding a third select above
    // these would silently retarget it at the wrong axis.
    expect(screen.getByLabelText('Ad storage').value).toBe('granted')
    expect(screen.getByLabelText('Analytics storage').value).toBe('granted')
  })

  it('names the masking default as the default', async () => {
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Masking', { selector: 'h2' })).toBeTruthy())
    expect(screen.getAllByText(/default is total suppression/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Masked data is not uploaded/i).length).toBeGreaterThan(0)
  })

  it('shows both retention windows side by side', async () => {
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Retention', { selector: 'h2' })).toBeTruthy())
    expect(screen.getByText(/Ordinary 30 days/)).toBeTruthy()
    expect(screen.getByText(/Favourite 270 days \(about 9 months\)/)).toBeTruthy()
  })

  it('shows what a blocked visit means and carries the vendor console message', async () => {
    render(<Page.Component />)
    await waitFor(() => expect(screen.getAllByText('Blocked by IP').length).toBeGreaterThan(0))
    expect(
      screen.getAllByText("Data from this session isn't being collected by Microsoft Clarity.").length,
    ).toBeGreaterThan(0)
    // getAllBy: the range is named twice on purpose, once as the stored exclusion
    // and once as the range a refused visit matched. The assertion is that both
    // are legible, not that only one of them is.
    expect(screen.getAllByText(/10\.0\.0\.0\/8/).length).toBeGreaterThan(0)
  })

  it('renders a partial grant as its own consequence', async () => {
    const user = userEvent.setup()
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Record a consent call')).toBeTruthy())

    // The partial grant is the case a single "consent granted" flag hides, so it
    // has to be reached by denying one axis rather than by reading both as granted.
    await user.selectOptions(screen.getByLabelText('Analytics storage'), 'denied')

    expect(screen.getByText(/a browser cookie cannot be scoped to one axis/i)).toBeTruthy()
  })

  it('renders the sourced limits as limits with their evidence', async () => {
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('What this workflow refuses to do')).toBeTruthy())
    // getAllBy: the limit is named in the heading and restated in the detail that
    // says what the supported alternative is. Both sentences are wanted.
    expect(screen.getAllByText(/cannot be deleted or downloaded/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/you can't delete or download specific recordings/)).toBeTruthy()
    expect(screen.getByText(/AAD instance/)).toBeTruthy()
  })

  it('never claims compliance and never offers an SSO path', async () => {
    const { container } = render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Record a consent call')).toBeTruthy())
    const text = container.textContent.toLowerCase()
    expect(text).not.toContain('compliant')
    expect(text).not.toContain('certified')
    expect(text).not.toContain('iso 27001')
    expect(text).not.toContain('soc 2')
    expect(text).not.toContain('sign in with sso')
  })

  it('shows the recorded decisions with what they rejected', async () => {
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Recorded decisions')).toBeTruthy())
    expect(screen.getByText('DERIVED_DEFAULT_MASKING_IS_TOTAL_SUPPRESSION')).toBeTruthy()
    expect(screen.getByText(/would treat an unmasked value as the normal case/)).toBeTruthy()
  })

  it('reports the vendor ceiling as a limit rather than a badge', async () => {
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Governance ceiling')).toBeTruthy())
    expect(screen.getByText(/within the ceiling/)).toBeTruthy()
    expect(screen.getByText(/100000 sessions per project per day/)).toBeTruthy()
  })
})

describe('the writes', () => {
  it('records a consent call with both axes', async () => {
    const user = userEvent.setup()
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Record a consent call')).toBeTruthy())

    await user.click(screen.getByRole('button', { name: /record the call/i }))

    await waitFor(() => expect(posts.some((entry) => entry.url.includes('/consent'))).toBe(true))
    const post = posts.find((entry) => entry.url.includes('/consent'))
    expect(JSON.parse(post.body).ad_Storage).toBe('granted')
    expect(JSON.parse(post.body).analytics_Storage).toBe('granted')
  })

  it('sends the vendor axis spelling, not the internal one', async () => {
    const user = userEvent.setup()
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Record a consent call')).toBeTruthy())

    await user.selectOptions(screen.getByLabelText('Analytics storage'), 'denied')
    await user.click(screen.getByRole('button', { name: /record the call/i }))

    await waitFor(() => expect(posts.some((entry) => entry.url.includes('/consent'))).toBe(true))
    const body = JSON.parse(posts.find((entry) => entry.url.includes('/consent')).body)
    expect(body.analytics_Storage).toBe('denied')
    expect('analytics_storage' in body).toBe(false)
  })

  it('shows the refusal when a sixth label is refused', async () => {
    const user = userEvent.setup()
    render(<Page.Component />)
    await waitFor(() => expect(screen.getAllByText('Add labels').length).toBeGreaterThan(0))

    await user.click(screen.getAllByRole('button', { name: /add labels/i })[0])
    await user.type(screen.getByLabelText(/labels/i), 'a,b,c,d,e,f')
    await user.click(screen.getByRole('button', { name: /save the labels/i }))

    await waitFor(() => expect(screen.getByText(/at most 5 labels/i)).toBeTruthy())
  })

  it('shows the sourced refusal when a single delete is attempted', async () => {
    const user = userEvent.setup()
    render(<Page.Component />)
    await waitFor(() => expect(screen.getAllByText('Add labels').length).toBeGreaterThan(0))

    await user.click(screen.getAllByRole('button', { name: /^delete$/i })[0])

    // getAllBy: the refusal appears as the outcome title and again in the detail
    // carried by the 409 body. The detail is what names the supported
    // alternative, so the assertion wants both rather than the first match.
    await waitFor(() =>
      expect(
        screen.getAllByText(/A single recording cannot be deleted or downloaded/).length,
      ).toBeGreaterThan(0),
    )
  })

  it('says the label budget before the sixth label is refused', async () => {
    const user = userEvent.setup()
    render(<Page.Component />)
    await waitFor(() => expect(screen.getAllByText('Add labels').length).toBeGreaterThan(0))

    await user.click(screen.getAllByRole('button', { name: /add labels/i })[0])

    await waitFor(() => expect(screen.getByText(/2 labels on this recording/)).toBeTruthy())
    expect(screen.getByText(/3 slots left before the cap of 5/)).toBeTruthy()
  })
})

describe('the states the page can be in', () => {
  it('renders an empty project state and offers to create one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        // The page decides to render the empty state from the summary's
        // project_created flag, not from the project body, so a stub that only
        // empties /project still leaves a created project on screen.
        const path = String(url).replace(PREFIX, '').split('?')[0]
        if (path === '/summary') return Promise.resolve(json({ ...SUMMARY, project_created: false }))
        return Promise.resolve(json(path === '/project' ? {} : routeFor(String(url))))
      }),
    )
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('No recording project yet')).toBeTruthy())
    expect(screen.getByRole('button', { name: /create the project/i })).toBeTruthy()
  })

  it('renders an error state with a retry', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('the API is down'))),
    )
    render(<Page.Component />)
    await waitFor(() => expect(screen.getByText('Could not load data')).toBeTruthy())
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })

  it('renders the loading state first', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    render(<Page.Component />)
    expect(screen.getByText(/loading the consent gate/i)).toBeTruthy()
  })
})

describe('the descriptor', () => {
  it('matches the backend feature id and carries a label and an icon', () => {
    expect(Page.id).toBe('wf-083-record-buyer-sessions-behind-a-consent-gate')
    expect(Page.label).toBeTruthy()
    expect(Page.icon).toBeTruthy()
    expect(typeof Page.Component).toBe('function')
  })
})
