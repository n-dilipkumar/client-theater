/**
 * CRM read panel API (WF-042).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-042`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own vocabulary - `identity_id`,
 * `buyer_email`, `refresh`, `limit` - so the documented flow can be followed
 * against the API directly and a reader can match what the page sends against
 * what the research says should be sent. The room list is read from the core
 * records route, so this page depends on the HTTP contract rather than on
 * another module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-042'

/** Drop empty values so we never send `?refresh=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const crmReadApi = {
  /**
   * The published vocabularies: the three systems, the three objects, each
   * vendor's own spelling of them, the identity sources, the display-label
   * capability per vendor, the paging shape per vendor, the cache states, the
   * read outcomes and every vendor limit with the number the research fixed.
   * Every picker on the page renders from this rather than from a list compiled
   * into this file.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one. The
   * research fixes the numbers and the capability check. It does not say which
   * identity source to use, what "a short TTL" means in seconds, or whether a
   * read is finished, so those edges are served as data.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Each vendor's read endpoints, limits and display-label capability. */
  vendors: () => apiRequest(`${PREFIX}/vendors`),

  /** The room's CRM identities, optionally narrowed to one room. */
  identities: (params = {}) => apiRequest(`${PREFIX}/identities${query(params)}`),
  registerIdentity: (payload, roomId) =>
    apiRequest(`${PREFIX}/identities${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  identity: (identityId, roomId) =>
    apiRequest(`${PREFIX}/identities/${identityId}${query({ room_id: roomId })}`),

  /** The room's own display labels, for vendors that cannot annotate. */
  optionSets: (params = {}) => apiRequest(`${PREFIX}/option-sets${query(params)}`),
  registerOptionSet: (payload, roomId) =>
    apiRequest(`${PREFIX}/option-sets${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The rows the room holds for the vendor's tables. */
  tables: (roomId) => apiRequest(`${PREFIX}/tables${query({ room_id: roomId })}`),

  /**
   * The buyer's deal panel, read through the cache. A fresh cache serves
   * without touching the vendor; `refresh` is the scheduler's door, because
   * "read-through cache refresh on a room scheduler" means the scheduler asks.
   */
  panel: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/panel${query(params)}`),

  /** Force the read: issue the plans, normalise, label, cache, render. */
  pull: (roomId, payload = {}, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/panel/pull${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Every buyer's panel side by side, each read through its own cache. */
  dealPanel: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/deal-panel`),

  /** Every read plan this room issued, with the exact query it sent. */
  queries: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/queries${query(params)}`),

  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),
  cache: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/cache`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * A stored option value as the panel shows it.
 *
 * The research's step five is that the panel shows "Proposal sent", not the
 * integer `2`. So a labelled value prefers its label, an unlabelled one falls
 * back to the stored value, and an absent one reads as a dash rather than as an
 * empty cell a reader has to interpret.
 *
 * The fallback is per-value, not per-field: the room's own option sets usually
 * cover the common stages and not the rare ones, and a panel that hid every
 * stage value because one was unlabelled would be worse than one that shows the
 * codes it knows and admits which those are.
 */
export function displayValue(option) {
  if (!option) return '-'
  const { value, label } = option
  if (label) return label
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}

/**
 * Whether an option value is showing a raw code rather than a label.
 *
 * Used as a text marker beside the value, never as colour alone. The design floor
 * forbids conveying status by colour, and a stage code is exactly the kind of
 * thing a reader must be able to see is unlabelled.
 */
export function isUnlabelled(option) {
  if (!option) return false
  return !option.label && option.value !== null && option.value !== undefined && option.value !== ''
}

/**
 * The tone for a cache state, from the published vocabulary rather than from a
 * list compiled into the page.
 */
export function cacheTone(cache, state) {
  const entry = (cache?.states || []).find((row) => row.value === state)
  if (!entry) return 'neutral'
  if (state === 'fresh') return 'insert'
  if (state === 'stale') return 'delete'
  if (state === 'expired') return 'restore'
  return 'neutral'
}

/**
 * How a read outcome reads, and whether it is a finding or a description.
 *
 * `complete` is the only outcome that is not a finding, and the page says so in
 * words rather than by leaving the reader to infer it from an absence.
 */
export function outcomeNote(outcome) {
  switch (outcome) {
    case 'complete':
      return 'Every object read in full.'
    case 'paged':
      return 'A vendor owes more rows than this page carried. The continuation is logged.'
    case 'empty':
      return 'Nothing was read. A room with no CRM context renders this.'
    case 'truncated_at_vendor_limit':
      return 'The table is larger than this vendor returns in one request.'
    case 'capability_fallback':
      return 'This vendor annotates no display label, so the panel used the room option sets.'
    default:
      return ''
  }
}

/** The age of a cached panel, in words rather than in seconds. */
export function cacheAge(cache) {
  if (!cache || cache.age_seconds === null || cache.age_seconds === undefined) return ''
  const seconds = Math.round(cache.age_seconds)
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  return `${hours}h ago`
}

/** A plain-language sentence for a cache state, so state is never colour alone. */
export function cacheNote(state) {
  switch (state) {
    case 'fresh':
      return 'Within its TTL. No vendor call was made.'
    case 'expired':
      return 'Past its TTL. The next view reads the vendor again.'
    case 'stale':
      return 'The last read did not complete. Shown with its gap named.'
    case 'absent':
      return 'Nothing cached. The next view reads the vendor.'
    default:
      return ''
  }
}

/**
 * The one line a vendor's read plan is, for the query log.
 *
 * The research quotes the exact query each vendor wants, and a log row that hid
 * it behind a summary would make the "read-only, field-scoped" claim
 * unverifiable from the page.
 */
export function planSummary(row) {
  if (!row) return ''
  if (row.query?.q) return row.query.q
  if (row.query?.$select) {
    const parts = [`$select=${row.query.$select}`]
    if (row.query.$filter) parts.push(`$filter=${row.query.$filter}`)
    if (row.query.$top) parts.push(`$top=${row.query.$top}`)
    return parts.join(' ')
  }
  if (row.body) return `${row.method} ${row.path}`
  return `${row.method} ${row.path}`
}