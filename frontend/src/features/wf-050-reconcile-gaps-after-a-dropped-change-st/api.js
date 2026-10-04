/**
 * Reconcile gaps after a dropped change stream (WF-050): the API client and the
 * display helpers.
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is this feature's own: the backend module mounts these under
 * `/api/wf-050`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own spelling - `changeType`,
 * `transactionKey`, `commitTimestamp`, `recordIds`, `replayId` - so the documented
 * flow can be followed against the API directly and a reader can match what this
 * page sends against what the research says should be sent.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-050'

/** The vendor a room streams from when it has not named another. */
export const DEFAULT_VENDOR = 'salesforce'

/** A room that streams from HubSpot has no gap path at all, by the research. */
export const UNSUPPORTED_VENDORS = ['hubspot']

/** Drop empty values so we never send `?state=` and confuse a filter. */
export function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const gapApi = {
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  // Step 1. The subscriber reports what the stream lost.
  reportGapEvent: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/gap-events`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  gapEvents: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/gap-events${query(params)}`),
  gapEvent: (roomId, eventId) => apiRequest(`${PREFIX}/rooms/${roomId}/gap-events/${eventId}`),

  // Step 2. An incremental change arrives; the route says whether it was applied.
  changeEvent: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/change-events`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // The data-health view.
  dirty: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/dirty${query(params)}`),

  // Steps 3 and 5. The full re-read, the overwrite, the delete diff.
  reconcile: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reconcile`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  runs: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/runs${query(params)}`),
  run: (roomId, runId) => apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}`),
  runLog: (roomId, runId) => apiRequest(`${PREFIX}/rooms/${roomId}/runs/${runId}/log`),

  // The two resumable positions, and the rows this workflow repaired.
  cursors: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/cursors`),
  replica: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/replica${query(params)}`),

  // Step 6. Resubscribe, and one payload for the page's stat cards.
  subscribe: (roomId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/subscribe`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  health: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/health`),

  // The room list comes from the core records route, so this page depends on the
  // HTTP contract rather than on another feature's idea of the shape of a room.
  // Normalised to a list here so the picker never has to know the envelope.
  rooms: async () => {
    const body = await apiRequest('/records/room?limit=100')
    const rows = Array.isArray(body) ? body : body?.records || []
    return rows.map((row) => ({ ...row, id: row.id || row.room_id, name: row.name || row.id }))
  },
}

/**
 * The label a change type renders under, read from the published vocabulary rather
 * than from a list compiled into this file.
 *
 * A page that hard-codes these would go stale the moment the vendor added a sixth,
 * and it would go stale silently: the picker would offer a value the API refuses.
 */
export function changeTypeLabel(vocabulary, changeType) {
  if (!changeType) return 'unknown'
  if (changeType === vocabulary?.overflow_change_type) return 'overflow'
  const named = (vocabulary?.gap_change_types || []).includes(changeType)
  return named ? 'gap' : 'unrecognised'
}

/** A sentence for a gap type, in the words the research uses. */
export function gapTypeNote(vocabulary, changeType) {
  const operation = (vocabulary?.gap_operations || {})[changeType]
  if (!operation) return 'This event names no operation. An overflow carries no record id.'
  return `The CRM could not emit the ${operation}. The room owes this record a full re-read.`
}

/**
 * A sentence for a cursor verdict, so a resumable position is never a bare colour.
 *
 * The two cursors are not the same shape and the research says so, so the sentence
 * names the kind and whether the vendor will still answer for it.
 */
export function cursorNote(cursor) {
  if (!cursor) return 'This room holds no resumable position for this vendor yet.'
  if (!cursor.present) {
    return `${cursor.kind}: the room stored a slot but no position, so there is nothing to resume from.`
  }
  if (cursor.resumable) {
    if (cursor.applies) {
      return `${cursor.kind}: held ${formatDays(cursor.age_days)}, inside the ${cursor.expiry_days} day window.`
    }
    return `${cursor.kind}: held ${formatDays(cursor.age_days)}. This kind does not expire.`
  }
  return `${cursor.kind}: past the ${cursor.expiry_days} day window, so recovery falls back to a full re-read.`
}

/** A sentence for a dropped change event, so a drop is never silent. */
export function dropNote(outcome) {
  if (!outcome) return ''
  if (outcome.applied) {
    if (outcome.reason === 'no_dirty_marker') {
      return 'Applied. This record carries no dirty flag, so nothing was at risk.'
    }
    return 'Applied. The full re-read already carried this change, so writing it again would duplicate it.'
  }
  if (outcome.reason === 'older_than_the_gap') {
    return 'Dropped. This change did not commit after the gap, so it is already inside the window the re-read will cover.'
  }
  if (outcome.reason === 'no_dirty_marker') {
    return 'Not applied. The event named neither the entity nor the record.'
  }
  return 'Dropped. This change is newer than the record the vendor returned, so it has not reached this room. The dirty flag stays set.'
}

/** A sentence for a run state, in words rather than as a badge. */
export function runStateNote(run) {
  if (!run) return ''
  if (run.state === 'complete') {
    const written = run.counts?.written ?? 0
    const deleted = run.counts?.deleted ?? 0
    return `Reconciled. ${written} row(s) overwritten, ${deleted} tombstoned.`
  }
  if (run.state === 'expired_cursor') {
    return 'Stopped before reading. The stored cursor is past its window, so recovery falls back to a full re-read.'
  }
  if (run.state === 'refused') return 'Refused. Nothing was written to the replica.'
  return 'Open. The repair has not finished.'
}

/** A sentence for the subscription state an overflow leaves behind. */
export function subscriptionNote(event) {
  if (!event) return ''
  if (event.kind !== 'overflow') return 'The room is subscribed. A gap repairs one record and does not pause the stream.'
  if (event.subscription_state === 'unsubscribed') {
    return 'The room unsubscribed and stored the Replay ID. Resubscribe once the repair is done.'
  }
  return 'The room is subscribed again.'
}

/** One line naming a reconciliation event, for the sync log. */
export function logLineNote(line) {
  if (!line) return ''
  return `Step ${line.seq}. ${line.event.replace(/_/g, ' ')}${line.detail ? `: ${line.detail}` : ''}`
}

/** The researched number a picker should default to, if the caller has none. */
export function defaultOf(vocabulary, name) {
  return vocabulary?.numbers?.[name]?.value
}

/** A day count in words rather than as a decimal. */
export function formatDays(days) {
  if (typeof days !== 'number') return 'an unknown age'
  if (days < 1) return `${Math.max(1, Math.round(days * 24))}h`
  return `${Math.round(days)}d`
}

/**
 * The tone a state should be rendered in, from the published vocabulary rather
 * than from a colour chosen here.
 *
 * Tone is decoration: every state also renders its own words, so nothing here is
 * load-bearing for meaning.
 */
export function stateTone(vocabulary, state) {
  if (state === 'complete') return 'insert'
  if (state === 'expired_cursor' || state === 'refused') return 'delete'
  if (state === 'open') return 'update'
  if (state === 'dirty') return 'delete'
  return 'neutral'
}

/** Whether the page should offer a repair for this room. */
export function canReconcile(dirtyCount, openRunCount) {
  return Boolean(dirtyCount) && !openRunCount
}

/** Whether the page should offer a resubscription. */
export function canSubscribe(events) {
  return (events || []).some((row) => row.kind === 'overflow' && row.subscription_state === 'unsubscribed')
}

/** The vendor a room streams from, defaulting to Salesforce. */
export function vendorOf(event) {
  return event?.vendor || DEFAULT_VENDOR
}