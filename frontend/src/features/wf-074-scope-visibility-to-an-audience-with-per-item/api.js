/**
 * WF-074's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared `api`
 * object grows no methods, so a hundred features can each talk to their own `/api/<feature>`
 * routes without anyone editing a shared file.
 *
 * The write calls go through `requestWithBody` instead, and that is a finding rather than a
 * preference. `apiRequest` reads the error body to build a message and then throws it away, so a
 * failure survives as `status` plus one string. This workflow has three responses whose body is
 * the whole point:
 *
 *   - a 400 from a rule carries `errors`, a field-keyed map, so each message lands beside the
 *     input that caused it. A rep who typed a bad domain needs to see which field, not a
 *     sentence about a request.
 *   - a 422 from a scope conflict carries `rule` and `way_out`, so a panel can say what to do
 *     rather than only that it cannot.
 *   - a 200 from the view carries `membership_step`, `hidden_by_reason` and `scope_state`, which
 *     is what lets the page distinguish a viewer who is not in the audience from an audience
 *     that has been granted nothing.
 *
 * The shared client would need four more fields on one function, which is a shared file and a
 * platform decision. Until then the calls that need the body read it themselves. Recorded as
 * promotion work, not smuggled across the boundary.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-074'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** The shared client. Fine for everything whose failure is just a failure. */
function call(path, options) {
  return apiRequest(`${BASE}${path}`, options)
}

/** As `apiRequest`, but keeps the parsed error body on the thrown error. */
async function requestWithBody(path, options) {
  const response = await fetch(`/api${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    // The scope-conflict fields. A panel that can only say "no" sends a rep looking in the wrong
    // place; the message names the way out and this carries it to the component.
    error.rule = body?.rule || null
    error.wayOut = body?.way_out || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function send(path, method, payload) {
  return requestWithBody(path, { method, body: JSON.stringify(payload) })
}

const encode = encodeURIComponent

export const audienceApi = {
  // -- the board ----------------------------------------------------------- //

  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the grid cannot drift from the rules that validate it: the two
   * item types and their collections, the closed entry shape, both scopes with their write
   * semantics, the three membership steps in order, the three size caps, the email gate, the
   * scope-conflict rule and the four link scope states all come from one place on the server.
   */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),
  decision: (decisionId) => call(`/decisions/${encode(decisionId)}`),

  // -- audiences ----------------------------------------------------------- //

  groups: (roomId) => call(`/rooms/${encode(roomId)}/groups`),
  createGroup: (roomId, payload) => send(`/rooms/${encode(roomId)}/groups`, 'POST', payload),
  group: (groupId) => call(`/groups/${encode(groupId)}`),
  updateGroup: (groupId, changes) => send(`/groups/${encode(groupId)}`, 'PATCH', changes),

  // -- members ------------------------------------------------------------- //

  members: (groupId) => call(`/groups/${encode(groupId)}/members`),

  /**
   * Add member addresses. Idempotent and silent: the source says already-present members are
   * skipped and no invitation email is sent, so the response separates what was added from what
   * was already there and reports `invitations_sent: 0`. This workflow cannot tell a rep whether
   * a buyer knows they were added, and it does not pretend to.
   */
  addMembers: (groupId, emails) =>
    send(`/groups/${encode(groupId)}/members`, 'POST', { emails }),

  /**
   * Remove one member. No re-share: the link is unchanged and the next request from that address
   * is refused, because membership is re-evaluated on every view.
   */
  removeMember: (groupId, memberId) =>
    send(`/groups/${encode(groupId)}/members/${encode(memberId)}`, 'DELETE'),

  // -- the items a permission points at ------------------------------------- //

  /** The room's own library rows, read and never copied. `GET .../documents` and `.../folders`
   *  are one call here because an entry names an item type beside its id. */
  items: (roomId) => call(`/rooms/${encode(roomId)}/items`),

  // -- the group ACL: delta ------------------------------------------------- //

  /** The whole grid: every item in the room and what this audience may do with it, hidden items
   *  included, because a rep needs to know whether nobody granted an item or somebody revoked it. */
  groupPermissions: (groupId) => call(`/groups/${encode(groupId)}/permissions`),

  /**
   * Upsert the entries sent. An item the payload omits keeps what it had, so the response names
   * what this call touched and what it left alone. A rep who granted one document and turned off
   * another in the same call needs to know both happened.
   */
  setGroupPermissions: (groupId, entries) =>
    send(`/groups/${encode(groupId)}/permissions`, 'PUT', { permissions: entries }),

  // -- links and the link ACL: full replace --------------------------------- //

  links: (roomId, groupId) => call(`/rooms/${encode(roomId)}/links${query({ group_id: groupId })}`),
  createLink: (roomId, payload) => send(`/rooms/${encode(roomId)}/links`, 'POST', payload),
  link: (linkId) => call(`/links/${encode(linkId)}`),
  linkPermissions: (linkId) => call(`/links/${encode(linkId)}/permissions`),

  /**
   * Replace the link's overrides with the payload. An item the payload omits loses its override,
   * and an empty array clears every one of them, which hides the whole room rather than opening
   * it. That is why the server records that a write happened at all: a cleared link and a link
   * that was never scoped both store zero rows and mean opposite things.
   *
   * A link that belongs to a group answers 422. `error.wayOut` names the way out.
   */
  setLinkPermissions: (linkId, entries) =>
    send(`/links/${encode(linkId)}/permissions`, 'PUT', { permissions: entries }),

  // -- what a viewer sees --------------------------------------------------- //

  /**
   * What this address sees on this link right now, filtered before any bytes. The hidden items
   * come back as a count and a per-reason tally, never as rows.
   *
   * A refusal is a 200 with `admitted: false` rather than a 403: the link exists, the answer is
   * that this address is not in the audience, and a viewer screen needs `membership_step` to say
   * why.
   */
  view: (linkId, email) => call(`/links/${encode(linkId)}/view${query({ email })}`),
}

/** Every room, so the panel can offer one to scope an audience in. Read from the core collection. */
export const listRooms = () => apiRequest('/records/room?limit=100')

/**
 * The word this workflow is measured by.
 *
 * Exported rather than written into the page because a test imports it, and a test that imports
 * a literal checks nothing. The server sends the same sentence with every response, so the page
 * and the API cannot disagree.
 */
export const NOT_PROOF =
  'A membership match records that an address matched a list, a domain or an open group. It does not establish who used the address.'

/**
 * What this control is worth.
 *
 * The limitation says it "does not invite anybody, it does not send an email, and it does not
 * check that the address behind a request belongs to the person who made it", and that the items
 * are the room's own rows, "read and filtered, never copied".
 */
export const LIMITATION =
  'This scopes what an audience is shown. It does not invite anybody, it does not send an email, and it does not check that the address behind a request belongs to the person who made it. The items are the room’s own document and folder rows, read and filtered, never copied.'

/**
 * The default, quoted from the guide.
 *
 * "A new group sees nothing until you grant permissions." A security page that opened with a
 * populated grid and no statement of this would be showing the exceptional case as the ordinary
 * one.
 */
export const DEFAULT_DENY =
  'A new audience sees nothing until you grant permissions. An item without an entry is invisible to that audience.'

/**
 * Who owns the per-item access control list.
 *
 * The specification calls the two scopes "deliberately distinct" and says their conflict is
 * resolved by an explicit, documented rule. Both scopes live in this one feature, and the rule
 * is a refusal rather than a precedence order.
 */
export const SCOPE_OWNER =
  'This workflow owns the per-item access control list for both scopes: the group’s delta-upsert set and the link’s full-replace set, and the rule that a group link refuses link overrides.'

/**
 * The two parts that are assumptions rather than sourced facts.
 *
 * The item ids this grid points at are the room's own library rows, because the cited sources
 * describe a vendor's own join rows and no such table is public API here. And the permissions
 * grid surface is documented only as a dashboard, so this page is a reconstruction of it: the
 * object it edits is sourced, its layout is not.
 */
export const ASSUMPTION =
  'Two parts of this workflow are assumptions rather than sourced facts. The item ids this grid points at are the room’s own library document and folder rows, because the cited sources describe a vendor’s own DataroomDocument join rows and no such join table is public API here. And the permissions grid surface is documented only as a dashboard, so this page is a reconstruction of it: the object it edits is sourced, its layout is not.'

/**
 * The four scope states a link can be in.
 *
 * Mirrors what `GET /wf-074/vocabulary` serves, written here as well so the board can render
 * before the vocabulary request resolves and so the loading and error states have a defined value
 * rather than a blank card. A test asserts the two agree, so this cannot quietly become a second
 * source of truth.
 */
export const SCOPE_STATES = [
  {
    id: 'unscoped',
    label: 'Never scoped',
    meaning: 'Every item in the room is visible on this link.',
  },
  {
    id: 'cleared',
    label: 'Cleared',
    meaning: 'Every item is hidden. This is what clearing the override set is for.',
  },
  { id: 'scoped', label: 'Scoped', meaning: 'This link carries its own per-item permissions.' },
  {
    id: 'group',
    label: 'From a group',
    meaning: "The group's permissions decide what this link shows. Link overrides are refused.",
  },
]

/**
 * The three membership steps, in the fixed order the data flow names: "explicit email, then
 * domain, then `allow_all`".
 */
export const MEMBERSHIP_STEPS = [
  { id: 'by_email', label: 'An explicit member email' },
  { id: 'by_domain', label: 'An email domain on the group' },
  { id: 'allow_all', label: 'The group allows anyone through the link’s other gates' },
  { id: 'not_a_member', label: 'Not a member of this audience' },
]

/** The two item types, with the wording the source uses. */
export const ITEM_TYPES = [
  { id: 'dataroom_document', label: 'Document', collection: 'document' },
  { id: 'dataroom_folder', label: 'Folder', collection: 'documentFolder' },
]

/** The three size caps, quoted from the specification. */
export const CAPS = { domains: 100, membersPerCall: 500, permissionsPerCall: 1000 }

/** The domain normalisation rule, quoted. */
export const DOMAIN_RULE =
  'Give a domain bare as acme.com or with a leading @ as @acme.com. Both are lowercased and stored as @acme.com, and duplicates are removed.'

/** The email gate, quoted. There is no setting that turns it off, so there is nothing to set. */
export const EMAIL_GATE_NOTE =
  'A group link is always email-gated. A viewer has to be a member, by email or by domain, unless the group allows everyone. There is no setting that turns this off.'

/** The scope conflict, quoted from the refusal. */
export const SCOPE_CONFLICT_NOTE =
  'This link belongs to a group, and the group determines what the link shows. Switch the link to a general audience before setting permissions on the link itself.'

/** The scope-state label for a state id, for a screen that has only the id. */
export function scopeStateLabel(id) {
  return SCOPE_STATES.find((state) => state.id === id)?.label || id
}

/** The membership-step label for a step id, for a screen that has only the id. */
export function membershipLabel(id) {
  return MEMBERSHIP_STEPS.find((step) => step.id === id)?.label || id
}

/** The item-type label for a type id, for a screen that has only the id. */
export function itemTypeLabel(id) {
  return ITEM_TYPES.find((type) => type.id === id)?.label || id
}

/**
 * Whether a grid row is visible to its audience.
 *
 * Reads the server's own decided state rather than recomputing it from the two flags, because
 * the two denials are different facts: an item nobody granted and an item somebody revoked both
 * hide, and only the row can say which happened.
 */
export function isVisible(row) {
  return row?.can_view === true
}

/**
 * Why a grid row is hidden, in words.
 *
 * A page that rendered hidden rows as blank cells would make the most important question on the
 * grid unanswerable: is this item ungranted, or revoked?
 */
export function hiddenReason(row) {
  if (isVisible(row)) return null
  if (row?.deny_reason === 'can_view_is_false') return 'Revoked by a rep'
  return 'No grant yet'
}
