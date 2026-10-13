/**
 * CRM file mirror API (WF-047).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-047`, and `apiRequest` already prepends `/api`.
 *
 * Every payload is sent in the research's own vocabulary - `crm_file_id`,
 * `push_to_crm`, `folderId`, `owner_object`, `attach_mode` - so the documented
 * flow can be followed against the API directly. The room list is read from the
 * core records route, so this page depends on the HTTP contract rather than on
 * another module's idea of the shape of a room.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-047'

/** Drop empty values so we never send `?state=` and confuse a filter. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const crmFileApi = {
  /**
   * Every published word this workflow uses: the systems, which of them can
   * receive a file, the upload path and its field names, the folder
   * recommendation and its caveat, the two attach modes, the chip states with a
   * sentence each, the mirror outcomes, and the reconciliation rule and its floor.
   * Every picker on the page renders from this rather than from a list compiled
   * into this file.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one. The
   * research names the two-method connector interface without narrowing it, says
   * "revisions create a new file" without saying how a revision is detected, and
   * refuses to source two of the three vendors at all.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Each vendor's file capability, with the gap named where there is one. */
  vendors: () => apiRequest(`${PREFIX}/vendors`),

  /** The connections a room pushes its documents through. */
  connections: (roomId) => apiRequest(`${PREFIX}/connections${query({ room_id: roomId })}`),
  saveConnection: (payload, roomId) =>
    apiRequest(`${PREFIX}/connections${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  connection: (connectionId, roomId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}${query({ room_id: roomId })}`),
  deleteConnection: (connectionId, roomId) =>
    apiRequest(`${PREFIX}/connections/${connectionId}${query({ room_id: roomId })}`, {
      method: 'DELETE',
    }),

  /** The room's assets, each with its "Push to CRM" chip. */
  assets: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/assets${query(params)}`),
  asset: (roomId, assetId) => apiRequest(`${PREFIX}/rooms/${roomId}/assets/${assetId}`),
  registerAsset: (roomId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/assets${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched toggle, or a byte change that turns the chip stale. */
  patchAsset: (roomId, assetId, payload, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/assets/${assetId}${query(params)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /** Steps three and four in one call: upload, attach, store. */
  mirror: (roomId, assetId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/assets/${assetId}/mirror${query(params)}`, { method: 'POST' }),

  /** Every mirror attempt this room made, with the request it made. */
  mirrors: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/mirrors${query(params)}`),
  mirror_row: (mirrorId) => apiRequest(`${PREFIX}/mirrors/${mirrorId}`),

  /** The quoted reconciliation rule, as a route a scheduler calls. */
  reconcile: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/reconcile${query(params)}`, { method: 'POST' }),

  /** The two researched product surfaces. */
  documents: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/documents`),
  files: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/files`),
  remoteFiles: (params = {}) => apiRequest(`${PREFIX}/remote-files${query(params)}`),
  summary: (roomId) => apiRequest(`${PREFIX}/rooms/${roomId}/summary`),

  rooms: () => apiRequest('/records/room?limit=100'),
}

/**
 * The tone for a chip state, from the published vocabulary rather than from a
 * list compiled into the page.
 *
 * A tone is never the whole message. Every chip also carries the state's own
 * sentence, because the design floor forbids conveying status by colour alone
 * and a rep who cannot read the state cannot act on it.
 */
export function stateTone(state) {
  switch (state) {
    case 'mirrored':
      return 'insert'
    case 'stale':
      return 'restore'
    case 'failed':
      return 'delete'
    case 'withheld':
      return 'neutral'
    default:
      return 'neutral'
  }
}

/**
 * What a mirror outcome means, in words.
 *
 * `mirrored` is the only outcome that is not a finding, and the page says so
 * rather than leaving the reader to infer it from an absence. The rest each carry
 * the researched reason the room reached them.
 */
export function outcomeNote(outcome) {
  switch (outcome) {
    case 'mirrored':
      return 'Uploaded and attached. The CRM holds the file and the room stores its id.'
    case 'skipped_unchanged':
      return 'Nothing was sent. The stored file id already matches these bytes.'
    case 'withheld':
      return 'Nothing was sent. Push to CRM is off for this document.'
    case 'no_owner_record':
      return 'Nothing was sent. This connection names no CRM record to own the attachment.'
    case 'failed':
      return 'The last push was refused. The reason is named on the mirror row.'
    default:
      return ''
  }
}

/**
 * One line describing the multipart request a mirror built.
 *
 * The research quotes the exact endpoint and the field names, and a log row that
 * hid them behind a summary would make the "multipart/form-data to
 * files/2026-09/files" claim unverifiable from the page.
 */
export function requestSummary(request) {
  if (!request || !request.path) return ''
  const fields = Object.keys(request.fields || {})
  return `${request.method} ${request.path} as ${request.content_type} (${fields.join(', ')})`
}

/**
 * The attachment a mirror wrote, in words.
 *
 * Both modes are sourced, so the page names which one ran rather than showing an
 * id with nothing beside it.
 */
export function attachSummary(attachment) {
  if (!attachment || !attachment.mode) return ''
  if (attachment.mode === 'file_property') {
    return `file id written into the ${attachment.property_name} property`
  }
  return `file id attached to ${attachment.record_id} as a note`
}

/**
 * Whether a vendor's file API was documented, and what to say when it was not.
 *
 * The research is explicit that only one vendor's Files API is documented, so a
 * page that showed three identical vendor rows would be claiming a coverage the
 * specification does not have.
 */
export function vendorNote(vendor) {
  if (!vendor) return ''
  if (vendor.status === 'sourced') {
    return `${vendor.upload_path} as ${vendor.content_type}.`
  }
  return vendor.gap || 'The research did not document a file API for this system.'
}

/**
 * Whether a byte size reads better in bytes, kilobytes or megabytes.
 *
 * Mono is for machine values and this is one, so the number keeps the unit it was
 * measured in rather than being rounded into something a reader has to trust.
 */
export function byteSize(value) {
  const size = Number(value || 0)
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} kB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Whether a connection carries the folder the vendor recommends.
 *
 * "While a specific folder ID is not required at upload, it's recommended to
 * upload files into a folder and not the root directory." A connection without
 * one is valid, and the page says which it is rather than leaving it blank.
 */
export function folderNote(connection) {
  if (!connection) return ''
  if (connection.folder_id) return `Uploads into folder ${connection.folder_id}.`
  return 'No folder set. The vendor recommends one but does not require it, so this uploads to the root directory.'
}