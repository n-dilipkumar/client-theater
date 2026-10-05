/**
 * The page-outreach API helpers (WF-106).
 *
 * These are the functions the page calls before it renders anything, and they are the
 * place a sentence can quietly turn into a lie. Three claims are pinned here:
 *
 * 1. Every call stays inside this feature's own prefix. `apiRequest` prepends `/api`,
 *    so a caller that reaches outside it is reaching into another feature's routes,
 *    which is the coupling the feature contract forbids.
 * 2. `refusalMessage` turns each domain refusal into the mistake the caller can fix,
 *    and passes anything it does not recognise through verbatim rather than swallowing
 *    it.
 * 3. `ruleSentence` reads as the sentence a seller wrote, not as a raw value, and the
 *    observed value is reported beside the expected one so the arithmetic is visible.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PAGE_OUTREACH_ICON } from './primitives'
import {
  progressSentence,
  refusalMessage,
  ruleSentence,
  workflowLabel,
} from './api.js'

const calls = []

beforeEach(() => {
  calls.length = 0
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    calls.push({ url: raw, method: (init.method || 'GET').toUpperCase(), body: init.body })
    return { ok: true, status: 200, statusText: 'OK', json: async () => ({}) }
  })
})

async function loadModule() {
  return import('./api.js')
}

describe('the api calls', () => {
  it('stay inside this feature prefix, apart from the core room list', async () => {
    const { outreachApi } = await loadModule()
    await outreachApi.vocabulary()
    await outreachApi.inferences()
    await outreachApi.summary('room-1')
    await outreachApi.workflows('room-1')
    await outreachApi.workflow('wf_1')
    await outreachApi.views({ room_id: 'room-1', visitor_key: 'v' })
    await outreachApi.deliveries({ room_id: 'room-1' })
    await outreachApi.receipts({ room_id: 'room-1' })
    await outreachApi.prospects('room-1')
    await outreachApi.evaluate({ path: '/pricing' })
    await outreachApi.rooms()

    for (const call of calls) {
      const path = call.url.replace('/api', '').split('?')[0]
      expect(path === '/records/room' || path.startsWith('/wf-106')).toBe(true)
    }
  })

  it('drops empty filters so a blank value never becomes a filter', async () => {
    const { outreachApi } = await loadModule()
    await outreachApi.views({ room_id: 'room-1', visitor_key: '', workflow_id: undefined })
    expect(calls[0].url).toContain('room_id=room-1')
    expect(calls[0].url).not.toContain('visitor_key')
    expect(calls[0].url).not.toContain('workflow_id')
  })

  it('sends a filter through rather than dropping a real one', async () => {
    const { outreachApi } = await loadModule()
    await outreachApi.views({ room_id: 'room-1', visitor_key: 'visitor-1' })
    expect(calls[0].url).toContain('visitor_key=visitor-1')
  })

  it('posts the state change as a json body rather than as a query', async () => {
    const { outreachApi } = await loadModule()
    await outreachApi.setState('wf_1', 'live')
    expect(calls[0].method).toBe('POST')
    expect(JSON.parse(calls[0].body)).toEqual({ state: 'live' })
  })

  it('escapes an id that needs escaping', async () => {
    const { outreachApi } = await loadModule()
    await outreachApi.workflow('a/b c')
    expect(calls[0].url).toContain('a%2Fb%20c')
  })
})

describe('refusalMessage', () => {
  it('names the mistake behind a taken workflow name', () => {
    const message = refusalMessage({
      status: 409,
      message: 'a workflow named X already exists in this room',
    })
    expect(message).toMatch(/same buyer the same block twice/)
  })

  it('names a workflow that no longer exists', () => {
    expect(refusalMessage({ status: 404, message: 'no workflow with id x' })).toMatch(
      /retired/
    )
  })

  it('explains a rule on a kind nothing implements', () => {
    expect(refusalMessage({ message: 'this workflow implements url, dwell' })).toMatch(
      /never fire/
    )
  })

  it('explains an unread page view field', () => {
    expect(refusalMessage({ message: 'the page view carries field(s) ua' })).toMatch(
      /no rule reads on/
    )
  })

  it('explains an unread audience key', () => {
    expect(refusalMessage({ message: 'audience carries key(s) plan' })).toMatch(
      /company_keys, tags and segments/
    )
  })

  it('explains a block with no words in it', () => {
    expect(refusalMessage({ message: 'a message block needs text' })).toMatch(/empty box/)
  })

  it('explains two paths on one key', () => {
    expect(refusalMessage({ message: 'two paths answer to yes; cannot both be right' })).toMatch(
      /one question/
    )
  })

  it('explains why a second channel is refused rather than stored', () => {
    expect(refusalMessage({ message: 'this build has no outbound transport' })).toMatch(
      /only an in-app block is available/i
    )
  })

  it('explains a path selection naming a branch nobody declared', () => {
    expect(refusalMessage({ message: 'path_key must be one of yes_upgrade, got maybe' })).toMatch(
      /would point at nothing/
    )
  })

  it('passes an unrecognised refusal through rather than swallowing it', () => {
    expect(refusalMessage({ message: 'the widget fell off' })).toBe('the widget fell off')
  })

  it('copes with an error that is a bare string', () => {
    expect(refusalMessage('something broke')).toBe('something broke')
  })
})

describe('ruleSentence', () => {
  it('reads a prefix rule as the boundary a seller means', () => {
    expect(ruleSentence({ kind: 'url', mode: 'prefix', expected: '/pricing' })).toBe(
      'the path starts with /pricing, at a slash boundary'
    )
  })

  it('reads an exact rule as exact', () => {
    expect(ruleSentence({ kind: 'url', mode: 'exact', expected: '/pricing' })).toBe(
      'the path is exactly /pricing'
    )
  })

  it('reads a contains rule as a substring', () => {
    expect(ruleSentence({ kind: 'url', mode: 'contains', expected: 'pricing' })).toBe(
      'the path contains pricing'
    )
  })

  it('reads a dwell rule as a number of seconds', () => {
    expect(ruleSentence({ kind: 'dwell', mode: 'at_or_above', expected: '60' })).toBe(
      'the buyer spent 60 seconds or more on the page'
    )
  })

  it('reads a utm rule as the signal it is', () => {
    expect(ruleSentence({ kind: 'utm_source', expected: 'linkedin*' })).toMatch(/utm_source/)
    expect(ruleSentence({ kind: 'utm_campaign', expected: 'q3' })).toMatch(/utm_campaign/)
  })

  it('falls back to the raw value when a rule names no kind', () => {
    expect(ruleSentence({ value: '/x' })).toBe('an unknown rule on /x')
  })

  it('copes with being called with nothing', () => {
    expect(typeof ruleSentence()).toBe('string')
  })
})

describe('progressSentence', () => {
  it('leads with engagement when the buyer engaged', () => {
    expect(progressSentence({ engaged: true })).toMatch(/^Engaged/)
  })

  it('names the session damper when that is what happened', () => {
    expect(progressSentence({ hidden_for_session: true })).toMatch(/Dismissed or Messenger/)
  })

  it('says the block is shown when it was and nothing else happened', () => {
    expect(progressSentence({ deliveries: 1 })).toMatch(/Shown the block/)
  })

  it('says plainly that no block was shown', () => {
    expect(progressSentence({})).toMatch(/was not shown the block/)
  })
})

describe('workflowLabel', () => {
  it('prefers the name', () => {
    expect(workflowLabel({ name: 'Upgrade page repeaters' })).toBe('Upgrade page repeaters')
  })

  it('falls back to the id rather than rendering nothing', () => {
    expect(workflowLabel({ id: 'wf_1' })).toBe('wf_1')
  })

  it('has a last resort', () => {
    expect(workflowLabel({})).toBe('Unnamed workflow')
  })
})

describe('the nav glyph', () => {
  it('is a path, so the shared icon map is untouched', () => {
    expect(typeof PAGE_OUTREACH_ICON).toBe('string')
    expect(PAGE_OUTREACH_ICON.startsWith('M')).toBe(true)
  })
})
