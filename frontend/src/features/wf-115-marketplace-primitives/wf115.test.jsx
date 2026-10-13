/**
 * Tests for WF-115's page.
 *
 * The page is driven against a stubbed `fetch`, so the assertions are about what
 * a builder reads and which controls are offered, not about a live server. Every
 * test builds its own stub and its own data, so the file passes alone and under
 * vitest's parallel run without depending on another test's fixture.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import page from './index.jsx'
import {
  canReview,
  canSubmit,
  endpointRows,
  parseSamplePayload,
  stateLabel,
  stateTone,
} from './api.js'

const { Component } = page

const VOCABULARY = {
  ticket: 'WF-115',
  review_states: ['draft', 'submitted', 'in_review'],
  review_decisions: { approve: 'approved', request_changes: 'changes_requested' },
}

const SUMMARY = { total: 2, published: 0, awaiting_review: 1, deleted: 0, by_state: {}, by_kind: {} }

const DRAFT = {
  id: 'mod_1',
  key: 'mycustomtrigger',
  kind: 'trigger',
  name: 'My custom trigger',
  version: 0,
  review_state: 'draft',
  targets: ['submitted', 'deleted'],
  available_actions: {
    submit: true,
    review: false,
    publish: false,
    withdraw: false,
    delete: true,
    restore: false,
    deliver: false,
  },
  issues: [],
  warnings: [],
  sample_payload: { contact: { name: 'Dana' } },
  subscription_url: 'https://apps.example.com/subscribe',
}

const SUBMITTED = {
  id: 'mod_2',
  key: 'mycustomaction',
  kind: 'action',
  name: 'My custom action',
  version: 1,
  review_state: 'submitted',
  targets: ['approved', 'changes_requested', 'withdrawn', 'deleted'],
  available_actions: {
    submit: false,
    review: true,
    publish: false,
    withdraw: true,
    delete: true,
    restore: false,
    deliver: false,
  },
  issues: [],
  warnings: [],
  sample_payload: { contact: { name: 'Dana' } },
  execution: { method: 'url', url: 'https://apps.example.com/execute' },
}

function jsonResponse(body, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function stubFetch(overrides = {}) {
  const calls = []
  const routes = {
    '/wf-115/vocabulary': VOCABULARY,
    '/wf-115/summary': SUMMARY,
    ...overrides,
  }
  const handler = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body })
    if (url.includes('/wf-115/modules')) {
      return jsonResponse(routes['/wf-115/modules'] ?? { count: 2, modules: [DRAFT, SUBMITTED] })
    }
    for (const [pattern, body] of Object.entries(routes)) {
      if (url.includes(pattern)) return jsonResponse(typeof body === 'function' ? body() : body)
    }
    return jsonResponse({ detail: `no stub for ${url}` }, 404)
  }
  global.fetch = vi.fn(handler)
  return calls
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('the descriptor', () => {
  it('exports the four fields the host discovers', () => {
    expect(page.id).toBe('wf-115-marketplace-primitives')
    expect(typeof page.label).toBe('string')
    expect(page.icon).toBeTruthy()
    expect(typeof page.iconPath).toBe('string')
    expect(typeof Component).toBe('function')
  })
})

describe('the helpers', () => {
  it('names a state in words and a tone from one place', () => {
    expect(stateLabel('published')).toBe('Published')
    expect(stateTone('rejected')).toBe('delete')
    expect(stateLabel('unknown_state')).toBe('unknown_state')
    expect(stateTone('unknown_state')).toBe('neutral')
  })

  it('reads the derived actions rather than guessing', () => {
    expect(canSubmit(DRAFT)).toBe(true)
    expect(canSubmit(SUBMITTED)).toBe(false)
    expect(canReview(SUBMITTED)).toBe(true)
    expect(canReview(null)).toBe(false)
  })

  it('parses the pasted sample payload', () => {
    expect(parseSamplePayload('{"a": 1}')).toEqual({ ok: true, value: { a: 1 }, error: null })
    expect(parseSamplePayload('').ok).toBe(false)
    expect(parseSamplePayload('{oops').ok).toBe(false)
    expect(parseSamplePayload('[1, 2]').ok).toBe(false)
  })

  it('lists the recorded endpoints and never claims to call them', () => {
    const rows = endpointRows(DRAFT)
    expect(rows).toHaveLength(1)
    expect(rows[0].label).toMatch(/subscription url/i)
    expect(rows[0].note).toMatch(/not called/i)

    const custom = endpointRows({
      execution: { method: 'custom_code', code: 'return 1' },
      pause_resume_webhook: 'https://apps.example.com/pause',
    })
    expect(custom.map((row) => row.label)).toContain('Custom code (JS)')
    expect(custom.find((row) => row.label === 'Custom code (JS)').note).toMatch(/never executed/i)
    expect(endpointRows(null)).toEqual([])
  })
})

describe('the page', () => {
  it('renders the summary and the modules', async () => {
    stubFetch()
    render(<Component />)

    expect((await screen.findAllByText('My custom trigger')).length).toBeGreaterThan(0)
    expect(screen.getByText('My custom action')).toBeInTheDocument()
    expect(screen.getAllByText('Draft').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Submitted').length).toBeGreaterThan(0)
    expect(screen.getByText('mycustomtrigger · trigger · v0')).toBeInTheDocument()
  })

  it('renders an empty state rather than a blank page', async () => {
    stubFetch({ '/wf-115/modules': { count: 0, modules: [] } })
    render(<Component />)

    expect(await screen.findByText(/no modules yet/i)).toBeInTheDocument()
  })

  it('creates a module with the parsed payload', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('My custom trigger')

    await userEvent.type(screen.getByLabelText(/^key$/i), 'brandnewkey')
    await userEvent.type(screen.getByLabelText(/^name$/i), 'Brand new trigger')
    await userEvent.click(screen.getByRole('button', { name: /save draft/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.method === 'POST' && call.url.endsWith('/wf-115/modules'))
      expect(post).toBeDefined()
      const body = JSON.parse(post.body)
      expect(body.key).toBe('brandnewkey')
      expect(body.kind).toBe('trigger')
      expect(body.sample_payload).toEqual({ contact: { name: 'Dana' }, page: { url: '/pricing' } })
      expect(body.subscription_url).toBeTruthy()
    })
  })

  it('refuses to submit an invalid payload', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('My custom trigger')

    const payload = screen.getByLabelText(/sample json payload/i)
    await userEvent.clear(payload)
    await userEvent.type(payload, '{{oops')

    expect(await screen.findByText(/not valid json/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save draft/i })).toBeDisabled()
    expect(calls.some((call) => call.method === 'POST')).toBe(false)
  })

  it('submits a selected module for review with a changelog', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('My custom trigger')

    await userEvent.click(screen.getByRole('button', { name: /My custom trigger/i }))
    await userEvent.type(await screen.findByLabelText(/changelog/i), 'First cut')
    await userEvent.click(screen.getByRole('button', { name: /submit for review/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/modules/mod_1/submit'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).changelog).toBe('First cut')
    })
  })

  it('approves a submitted module through the review route', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('My custom action')

    await userEvent.click(screen.getByRole('button', { name: /My custom action/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^approve$/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes('/modules/mod_2/review'))
      expect(post).toBeDefined()
      expect(JSON.parse(post.body).decision).toBe('approve')
    })
  })

  it('soft-deletes a selected module', async () => {
    const calls = stubFetch()
    render(<Component />)
    await screen.findByText('My custom trigger')

    await userEvent.click(screen.getByRole('button', { name: /My custom trigger/i }))
    await userEvent.click(await screen.findByRole('button', { name: /delete module/i }))

    await waitFor(() => {
      const call = calls.find((item) => item.method === 'DELETE')
      expect(call).toBeDefined()
      expect(call.url).toContain('/modules/mod_1')
    })
  })

  it('shows the recorded endpoints once a module is selected', async () => {
    stubFetch()
    render(<Component />)
    await screen.findByText('My custom trigger')

    await userEvent.click(screen.getByRole('button', { name: /My custom trigger/i }))

    expect(await screen.findByText(/subscription url \(post\)/i)).toBeInTheDocument()
    expect(screen.getByText(/recorded, not called by this room/i)).toBeInTheDocument()
  })
})
