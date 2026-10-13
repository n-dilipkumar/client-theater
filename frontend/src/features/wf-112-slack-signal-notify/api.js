/**
 * Slack-notification API (WF-112).
 *
 * These calls live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 *
 * `PREFIX` is this feature's own: the backend module mounts these under `/api/wf-112`,
 * and `apiRequest` already prepends `/api`. Nothing here calls a route outside that
 * prefix, apart from the core room list, which every page that picks a room uses.
 *
 * Room is a query parameter rather than a path segment. A connection, a block and a
 * decision are all room-scoped, and a client that already holds a room list gets the
 * room it cares about from a picker.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-112'

/** Drop empty values, so a blank filter never reaches the server as a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const slackApi = {
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  inferences: () => apiRequest(`${PREFIX}/inferences`),
  summary: (roomId) => apiRequest(`${PREFIX}/summary${query({ room_id: roomId })}`),

  connection: (roomId) => apiRequest(`${PREFIX}/connection${query({ room_id: roomId })}`),
  connect: (payload, roomId, actor) =>
    apiRequest(`${PREFIX}/connection${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  revoke: (connectionId, actor) =>
    apiRequest(`${PREFIX}/connection/${encodeURIComponent(connectionId)}${query({ actor })}`, {
      method: 'DELETE',
    }),

  automations: (roomId) => apiRequest(`${PREFIX}/automations${query({ room_id: roomId })}`),
  automation: (id) => apiRequest(`${PREFIX}/automations/${encodeURIComponent(id)}`),
  createAutomation: (payload, roomId, actor) =>
    apiRequest(`${PREFIX}/automations${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateAutomation: (id, payload, actor) =>
    apiRequest(`${PREFIX}/automations/${encodeURIComponent(id)}${query({ actor })}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteAutomation: (id, actor) =>
    apiRequest(`${PREFIX}/automations/${encodeURIComponent(id)}${query({ actor })}`, {
      method: 'DELETE',
    }),
  sendTest: (id, payload, roomId, actor) =>
    apiRequest(`${PREFIX}/automations/${encodeURIComponent(id)}/test${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  preview: (payload, roomId) =>
    apiRequest(`${PREFIX}/notify/preview${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  notify: (signalId, payload, roomId, actor) =>
    apiRequest(`${PREFIX}/signals/${encodeURIComponent(signalId)}/notify${query({ room_id: roomId, actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  dispatches: (params = {}) => apiRequest(`${PREFIX}/dispatches${query(params)}`),
  dispatch: (id) => apiRequest(`${PREFIX}/dispatches/${encodeURIComponent(id)}`),

  /** The core room list. The page is handed no props, so it fetches its own rooms. */
  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * Turn a domain refusal into the mistake the caller can fix.
 *
 * The server already writes a sentence, but a seller reading a page wants the next
 * action rather than the rule's name. Each branch names that action. Anything this
 * does not recognise is shown verbatim rather than swallowed, so a new refusal
 * still reaches the reader.
 */
export function refusalMessage(error) {
  const text = String(error?.message || error)
  const status = error?.status
  if (status === 422 && /bot' scope|bot scope/i.test(text)) {
    return 'The Slack credential needs the bot scope. Authorise the app again and grant it.'
  }
  if (status === 422 && /needs a channel/i.test(text)) {
    return 'Choose a channel. The research requires one at connect time, because every message names a destination.'
  }
  if (status === 409 && /already has a connected Slack workspace/i.test(text)) {
    return 'This room is already connected to a workspace. Revoke it before connecting another.'
  }
  if (status === 409 && /already exists in this room/i.test(text)) {
    return 'A Slack block with that name already exists here. Give this one a different name.'
  }
  if (status === 422 && /not a Slack user id/i.test(text)) {
    return 'A mention must carry a Slack user id, like <@U012AB3CD>. A handle is text no client renders as a mention.'
  }
  if (status === 422 && /not a Slack mention and not a link/i.test(text)) {
    return 'The message carries a token that is neither a mention nor a link. Slack shows it literally.'
  }
  if (status === 404 && /no buyer signal/i.test(text)) {
    return 'No buyer signal is on file under that id. This workflow notifies on signals the intent-signal workflow emitted.'
  }
  if (status === 404) {
    return 'That record is gone. Reload the page and try again.'
  }
  return text
}

/** The sentence a refused decision carries, so a seller knows what to fix. */
export function dispatchReason(dispatch) {
  const code = String(dispatch?.refusal || '')
  if (!code) return String(dispatch?.reason || '')
  const reasons = {
    no_connection: 'Connect a Slack workspace for this room before a signal can notify anyone.',
    connection_revoked: 'The Slack connection is revoked. Connect the workspace again.',
    automation_not_live: 'This block is a draft. Publish it for a signal to fire it.',
    channel_required: 'No destination channel is set. Choose one per message or on the block.',
    bot_not_in_channel: 'The bot is not in the private channel. Invite it with /invite, then try again.',
  }
  return reasons[code] || String(dispatch?.reason || code)
}

/** One word and a sentence for a decision's state, so colour is never the only label. */
const STATE_MEANING = {
  recorded: 'Recorded: this message would be posted to the channel',
  refused: 'Refused: a rule stopped this notification',
}

export function stateMeaning(state) {
  return STATE_MEANING[String(state || '')] || 'No meaning published for this state'
}

/** A decision as one sentence a seller reads at a glance. */
export function dispatchSentence(dispatch) {
  const kind = dispatch?.kind === 'test' ? 'Test send' : 'Signal'
  const channel = dispatch?.channel ? `#${dispatch.channel}` : 'no channel'
  const state = String(dispatch?.state || 'unknown')
  if (state === 'refused') return `${kind} refused for ${channel}`
  return `${kind}: would post to ${channel}`
}

/**
 * The retry window a rate-refused decision carries, as a sentence.
 *
 * The number is the point: a broadcast is one request every ten seconds, and a
 * seller told only "too fast" cannot know whether to wait a moment or a minute.
 */
export function retrySentence(dispatch) {
  const seconds = Number(dispatch?.retry_after_seconds || 0)
  if (String(dispatch?.refusal || '') !== 'rate_limited' || !seconds) return ''
  return `The broadcast limit is one message per ten seconds. Try again in ${Math.ceil(seconds)}s.`
}

/** The message that would be posted, or an empty string on a refused decision. */
export function messageText(dispatch) {
  return String(dispatch?.message?.text || '')
}

/** A block's label, preferring its name and never rendering a blank. */
export function blockLabel(automation) {
  return automation?.name || automation?.id || 'Unnamed block'
}

/** The one sentence that must not be inferable: nothing is sent. */
export const NOTHING_SENT_NOTE =
  'This workflow records what would be posted and to which channel. It does not send a Slack message: ' +
  'this product has no Slack transport, and a Slack action carries no conversion.'
