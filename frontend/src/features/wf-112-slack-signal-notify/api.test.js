/**
 * The Slack-notification API helpers (WF-112).
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
 * 3. A rate-refused decision reports its retry window, because a broadcast is one
 *    request every ten seconds and "too fast" alone tells a seller nothing.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SLACK_ICON } from './primitives'
import {
  NOTHING_SENT_NOTE,
  blockLabel,
  dispatchReason,
  dispatchSentence,
  messageText,
  refusalMessage,
  retrySentence,
  slackApi,
  stateMeaning,
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
    const { slackApi: api } = await loadModule()
    await api.vocabulary()
    await api.inferences()
    await api.summary('room-1')
    await api.connection('room-1')
    await api.connect({ workspace: 'acme' }, 'room-1', 'dana')
    await api.revoke('conn_1', 'dana')
    await api.automations('room-1')
    await api.automation('blk_1')
    await api.createAutomation({ name: 'x' }, 'room-1', 'dana')
    await api.updateAutomation('blk_1', { state: 'live' }, 'dana')
    await api.deleteAutomation('blk_1', 'dana')
    await api.sendTest('blk_1', {}, 'room-1', 'dana')
    await api.preview({ kind: 'live' }, 'room-1')
    await api.notify('sig_1', {}, 'room-1', 'dana')
    await api.dispatches({ room_id: 'room-1' })
    await api.dispatch('disp_1')
    await api.rooms()

    for (const call of calls) {
      const path = call.url.replace('/api', '').split('?')[0]
      expect(path === '/records/room' || path.startsWith('/wf-112')).toBe(true)
    }
  })

  it('drops empty filters so a blank value never becomes a filter', async () => {
    const { slackApi: api } = await loadModule()
    await api.dispatches({ room_id: 'room-1', kind: '', state: undefined })
    expect(calls[0].url).toContain('room_id=room-1')
    expect(calls[0].url).not.toContain('kind=')
    expect(calls[0].url).not.toContain('state=')
  })

  it('sends the test channel and profile as a json body', async () => {
    const { slackApi: api } = await loadModule()
    await api.sendTest('blk_1', { channel: 'sales', customer: { customer: { username: 'Dana' } } }, 'room-1')
    expect(calls[0].method).toBe('POST')
    expect(JSON.parse(calls[0].body).channel).toBe('sales')
  })

  it('escapes an id that needs escaping', async () => {
    const { slackApi: api } = await loadModule()
    await api.automation('a/b c')
    expect(calls[0].url).toContain('a%2Fb%20c')
  })
})

describe('refusalMessage', () => {
  it('names the missing bot scope as the action to take', () => {
    const message = refusalMessage({ status: 422, message: "does not carry the 'bot' scope" })
    expect(message).toMatch(/bot scope/)
  })

  it('names the missing channel as required at connect time', () => {
    expect(refusalMessage({ status: 422, message: 'a Slack connection needs a channel' })).toMatch(
      /connect time/,
    )
  })

  it('explains a room that is already connected', () => {
    expect(
      refusalMessage({ status: 409, message: 'this room already has a connected Slack workspace' }),
    ).toMatch(/Revoke it/)
  })

  it('explains a taken block name', () => {
    expect(
      refusalMessage({ status: 409, message: "a Slack block named 'x' already exists in this room" }),
    ).toMatch(/different name/)
  })

  it('explains a mention that is not a user id', () => {
    expect(refusalMessage({ status: 422, message: "'dana' is not a Slack user id" })).toMatch(
      /<@U012AB3CD>/,
    )
  })

  it('explains a token that is neither a mention nor a link', () => {
    expect(
      refusalMessage({ status: 422, message: 'not a Slack mention and not a link' }),
    ).toMatch(/literally/)
  })

  it('explains an unknown signal', () => {
    expect(refusalMessage({ status: 404, message: 'no buyer signal exists under it' })).toMatch(
      /intent-signal workflow/,
    )
  })

  it('shows an unrecognised refusal verbatim rather than swallowing it', () => {
    expect(refusalMessage({ status: 500, message: 'the disk is on fire' })).toBe('the disk is on fire')
  })

  it('copes with an error that is a bare string', () => {
    expect(refusalMessage('something broke')).toBe('something broke')
  })
})

describe('dispatchReason', () => {
  it('turns a private-channel refusal into the invite a seller must send', () => {
    expect(dispatchReason({ refusal: 'bot_not_in_channel' })).toMatch(/\/invite/)
  })

  it('turns no-connection into the next action', () => {
    expect(dispatchReason({ refusal: 'no_connection' })).toMatch(/Connect a Slack workspace/)
  })

  it('names the draft state when that is what stopped it', () => {
    expect(dispatchReason({ refusal: 'automation_not_live' })).toMatch(/Publish it/)
  })

  it('passes an unknown code through as the server reason', () => {
    expect(dispatchReason({ refusal: 'mystery', reason: 'because' })).toBe('because')
  })

  it('returns an empty string for a recorded decision', () => {
    expect(dispatchReason({ reason: '' })).toBe('')
  })
})

describe('dispatchSentence', () => {
  it('reads a recorded signal as what would be posted', () => {
    expect(dispatchSentence({ kind: 'live', state: 'recorded', channel: 'sales' })).toBe(
      'Signal: would post to #sales',
    )
  })

  it('reads a refused decision as refused', () => {
    expect(dispatchSentence({ kind: 'test', state: 'refused', channel: 'sales' })).toBe(
      'Test send refused for #sales',
    )
  })
})

describe('retrySentence', () => {
  it('names the broadcast window and the wait', () => {
    const sentence = retrySentence({ refusal: 'rate_limited', retry_after_seconds: 7.2 })
    expect(sentence).toMatch(/one message per ten seconds/)
    expect(sentence).toMatch(/8s/)
  })

  it('says nothing for a decision that was not rate limited', () => {
    expect(retrySentence({ state: 'recorded', retry_after_seconds: 0 })).toBe('')
  })
})

describe('messageText and blockLabel', () => {
  it('reads the rendered message', () => {
    expect(messageText({ message: { text: 'hello' } })).toBe('hello')
    expect(messageText({})).toBe('')
  })

  it('never renders a blank block', () => {
    expect(blockLabel({ name: 'High intent' })).toBe('High intent')
    expect(blockLabel({ id: 'blk_1' })).toBe('blk_1')
    expect(blockLabel({})).toBe('Unnamed block')
  })
})

describe('stateMeaning', () => {
  it('gives every state a sentence, so colour is never the only label', () => {
    expect(stateMeaning('recorded')).toMatch(/would be posted/)
    expect(stateMeaning('refused')).toMatch(/a rule stopped/)
    expect(stateMeaning('???')).toMatch(/No meaning/)
  })
})

describe('the nothing-sent note', () => {
  it('says plainly that no message is sent', () => {
    expect(NOTHING_SENT_NOTE).toMatch(/does not send a Slack message/)
    expect(NOTHING_SENT_NOTE).toMatch(/no conversion/)
  })
})

describe('the nav glyph', () => {
  it('is a path, so the shared icon map is untouched', () => {
    expect(typeof SLACK_ICON).toBe('string')
    expect(SLACK_ICON.startsWith('M')).toBe(true)
  })
})
