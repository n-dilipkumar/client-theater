/**
 * Fixtures for the WF-047 CRM file mirror tests.
 *
 * One place decides what this feature's API looks like, so a change to a route
 * name is a change to one file rather than to every test that stubs it. The
 * payloads are the researched shapes verbatim: the upload path and its field
 * names, the vendor gap text, both attach modes, all five chip states, and the
 * idempotent second mirror.
 */

export const ROOM = { id: 'room-1', name: 'Northwind Q4' }

export const ROOM_2 = { id: 'room-empty', name: 'Contoso pilot' }

export const VOCABULARY = {
  ticket: 'WF-047',
  systems: ['hubspot', 'salesforce', 'dataverse'],
  file_capable_systems: ['hubspot'],
  unsourced_systems: {
    salesforce:
      'Salesforce Files (ContentVersion / ContentDocument / ContentDocumentLink) could not be ' +
      'sourced: every candidate resources-content* URL returned 404. The Salesforce blob page was ' +
      'reachable only as a link index and the research quotes no payload for it, so this build ' +
      'does not invent one.',
    dataverse:
      'Microsoft Dataverse file/image columns and annotation/attachment tables could not be ' +
      'sourced: the corresponding documentation pages returned 404.',
  },
  upload_path: '/files/2026-09/files',
  upload_content_type: 'multipart/form-data',
  upload_required_fields: ['file'],
  upload_optional_fields: ['folderId', 'fileName'],
  folder_recommendation:
    'While a specific folder ID is not required at upload, it is recommended to upload files ' +
    'into a folder and not the root directory.',
  folder_caveat: 'Folder requirements at upload are subject to change.',
  attach_modes: ['note', 'file_property'],
  default_attach_mode: 'note',
  attach_mode_notes: {
    note:
      'Attach files to records using the notes APIs. The file id is written into a note attached ' +
      'to the record, so the attachment appears on the record timeline.',
    file_property:
      'A file-type property Allows for a file to be uploaded on a record or via a form. Stores a ' +
      'file ID.',
  },
  owner_objects: ['deal', 'contact', 'account'],
  default_owner_object: 'deal',
  mirror_states: ['pending', 'mirrored', 'stale', 'failed', 'withheld'],
  mirror_state_notes: {
    pending: 'Not pushed to the CRM yet. No file id is stored on this asset.',
    mirrored: 'Pushed. The CRM holds this file and the file id is stored on this asset.',
    stale: 'Changed since the last push. The next mirror creates a new CRM file.',
    failed: 'The last push was refused. The reason is named on the mirror row.',
    withheld: 'Push to CRM is off for this asset, so nothing was sent.',
  },
  mirror_outcomes: [
    'mirrored',
    'skipped_unchanged',
    'withheld',
    'no_owner_record',
    'failed',
  ],
  reconcile_rule: 'find room assets with no crm_file_id and push them',
  default_poll_interval_seconds: 300,
  min_poll_interval_seconds: 30,
  connector_methods: ['upload', 'attach'],
  digest_algorithm: 'sha256',
  file_id_key: 'crm_file_id',
}

export const VENDORS = {
  count: 3,
  sourced: 1,
  unsourced: 2,
  connector_methods: ['upload', 'attach'],
  vendors: [
    {
      system: 'hubspot',
      status: 'sourced',
      upload_path: '/files/2026-09/files',
      content_type: 'multipart/form-data',
      required_fields: ['file'],
      optional_fields: ['folderId', 'fileName'],
      attach_modes: ['note', 'file_property'],
      attach_mode_notes: VOCABULARY.attach_mode_notes,
      folder_recommendation: VOCABULARY.folder_recommendation,
      folder_caveat: VOCABULARY.folder_caveat,
      connector_methods: ['upload', 'attach'],
      gap: '',
    },
    {
      system: 'salesforce',
      status: 'unsourced',
      upload_path: '',
      content_type: '',
      required_fields: [],
      optional_fields: [],
      attach_modes: [],
      attach_mode_notes: {},
      folder_recommendation: VOCABULARY.folder_recommendation,
      folder_caveat: VOCABULARY.folder_caveat,
      connector_methods: ['upload', 'attach'],
      gap: VOCABULARY.unsourced_systems.salesforce,
    },
    {
      system: 'dataverse',
      status: 'unsourced',
      upload_path: '',
      content_type: '',
      required_fields: [],
      optional_fields: [],
      attach_modes: [],
      attach_mode_notes: {},
      folder_recommendation: VOCABULARY.folder_recommendation,
      folder_caveat: VOCABULARY.folder_caveat,
      connector_methods: ['upload', 'attach'],
      gap: VOCABULARY.unsourced_systems.dataverse,
    },
  ],
}

export const INFERENCES = {
  count: 2,
  inferences: [
    {
      id: 'revision-detected-by-content-digest',
      topic: 'how this workflow decides that a room asset is a new revision',
      why: 'The store revision counter measures writes rather than content, so a renamed document would create a second copy in the CRM.',
      change_it: 'assets.content_digest',
    },
    {
      id: 'unsourced-vendors-are-refused',
      topic: 'what this workflow does for the two systems the research could not source',
      why: 'The issue says do not invent a payload, and a refusal that names the gap is more useful than a body that is wrong.',
      change_it: 'vocabulary.UNSOURCED_SYSTEMS',
    },
  ],
}

/** One asset per chip state, so every chip on the page is exercised. */
export const ASSETS = {
  room_id: ROOM.id,
  count: 3,
  states: VOCABULARY.mirror_states,
  state_notes: VOCABULARY.mirror_state_notes,
  assets: [
    {
      id: 'asset-1',
      connection_id: 'conn-1',
      filename: 'northwind-rollout-deck.pdf',
      mime_type: 'application/pdf',
      byte_size: 4096,
      digest: 'sha256:aaa',
      mirrored_digest: 'sha256:aaa',
      crm_file_id: 'file-4f6212863afc57fd',
      state: 'mirrored',
      state_note: VOCABULARY.mirror_state_notes.mirrored,
      push_to_crm: true,
      is_revision: false,
      needs_mirror: false,
      revision: 2,
      updated_at: '2026-10-01T09:00:00.000+00:00',
    },
    {
      id: 'asset-2',
      connection_id: 'conn-1',
      filename: 'fabrikam-pricing-2026.xlsx',
      mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      byte_size: 20480,
      digest: 'sha256:bbb',
      mirrored_digest: 'sha256:ccc',
      crm_file_id: 'file-308a8000f27ae16c',
      state: 'stale',
      state_note: VOCABULARY.mirror_state_notes.stale,
      push_to_crm: true,
      is_revision: true,
      needs_mirror: true,
      revision: 3,
      updated_at: '2026-10-01T09:05:00.000+00:00',
    },
    {
      id: 'asset-3',
      connection_id: 'conn-2',
      filename: 'contoso-security-questionnaire.docx',
      mime_type:
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      byte_size: 51200,
      digest: 'sha256:ddd',
      mirrored_digest: '',
      crm_file_id: '',
      state: 'withheld',
      state_note: VOCABULARY.mirror_state_notes.withheld,
      push_to_crm: false,
      is_revision: false,
      needs_mirror: false,
      revision: 1,
      updated_at: '2026-10-01T09:02:00.000+00:00',
    },
  ],
}

export const CONNECTIONS = {
  count: 2,
  room_id: ROOM.id,
  systems: VOCABULARY.systems,
  file_capable_systems: VOCABULARY.file_capable_systems,
  unsourced_systems: VOCABULARY.unsourced_systems,
  connections: [
    {
      id: 'conn-1',
      room_id: ROOM.id,
      name: 'Fabrikam deal files',
      system: 'hubspot',
      owner_object: 'deal',
      crm_record_id: 'fabrikam-dl-9001',
      attach_mode: 'note',
      file_property: '',
      folder_id: '',
      folder_recommendation_followed: false,
      mirror_all: false,
      poll_interval_seconds: 300,
    },
    {
      id: 'conn-2',
      room_id: ROOM.id,
      name: 'Contoso account files',
      system: 'hubspot',
      owner_object: 'account',
      crm_record_id: '',
      attach_mode: 'note',
      file_property: '',
      folder_id: 'contoso-folder-1',
      folder_recommendation_followed: true,
      mirror_all: true,
      poll_interval_seconds: 300,
    },
  ],
}

export const FILES = {
  room_id: ROOM.id,
  count: 1,
  attach_modes: VOCABULARY.attach_modes,
  files: [
    {
      crm_file_id: 'file-4f6212863afc57fd',
      filename: 'northwind-rollout-deck.pdf',
      mime_type: 'application/pdf',
      byte_size: 4096,
      asset_id: 'asset-1',
      system: 'hubspot',
      owner_object: 'deal',
      crm_record_id: 'fabrikam-dl-9001',
      attach_mode: 'note',
      folder_id: '',
      simulated: true,
    },
  ],
}

export const MIRRORS = {
  room_id: ROOM.id,
  count: 3,
  outcome: '',
  outcomes: VOCABULARY.mirror_outcomes,
  mirrors: [
    {
      id: 'mirror-1',
      room_id: ROOM.id,
      asset_id: 'asset-1',
      connection_id: 'conn-1',
      system: 'hubspot',
      outcome: 'mirrored',
      is_revision: false,
      error_code: '',
      error_detail: '',
      request: {
        method: 'POST',
        path: '/files/2026-09/files',
        content_type: 'multipart/form-data',
        required_fields: ['file'],
        optional_fields: ['folderId', 'fileName'],
        fields: { file: 'northwind-rollout-deck.pdf', fileName: 'northwind-rollout-deck.pdf' },
        byte_size: 18,
        sha256: 'aaaa',
      },
      uploaded: { file_id: 'file-4f6212863afc57fd', folder_id: '', simulated: true },
      attachment: { mode: 'note', record_id: 'fabrikam-dl-9001', simulated: true },
      simulated: true,
      connector: 'hubspot',
      at: '2026-10-01T09:00:00.000+00:00',
    },
    {
      id: 'mirror-2',
      room_id: ROOM.id,
      asset_id: 'asset-2',
      connection_id: 'conn-1',
      system: 'hubspot',
      outcome: 'skipped_unchanged',
      is_revision: false,
      error_code: '',
      error_detail: '',
      request: {},
      uploaded: {},
      attachment: {},
      simulated: false,
      connector: 'hubspot',
      at: '2026-10-01T09:01:00.000+00:00',
    },
    {
      id: 'mirror-3',
      room_id: ROOM.id,
      asset_id: 'asset-3',
      connection_id: 'conn-2',
      system: 'hubspot',
      outcome: 'withheld',
      is_revision: false,
      error_code: '',
      error_detail: 'push_to_crm is off for this asset',
      request: {},
      uploaded: {},
      attachment: {},
      simulated: false,
      connector: 'hubspot',
      at: '2026-10-01T09:02:00.000+00:00',
    },
  ],
}

export const SUMMARY = {
  room_id: ROOM.id,
  connections: 2,
  assets: 3,
  assets_by_state: { pending: 0, mirrored: 1, stale: 1, failed: 0, withheld: 1 },
  mirrors: 3,
  mirrors_by_outcome: {
    mirrored: 1,
    skipped_unchanged: 1,
    withheld: 1,
    no_owner_record: 0,
    failed: 0,
  },
  reconcile_rule: VOCABULARY.reconcile_rule,
  interval_seconds: 300,
  simulated_mirrors: 1,
}

export const ROOMS = {
  records: [
    { id: ROOM.id, data: { name: ROOM.name } },
    { id: ROOM_2.id, data: { name: ROOM_2.name } },
  ],
}

/** The answer a mirror gives, keyed on the asset that was pushed. */
export const MIRROR_RESULT = {
  outcome: 'mirrored',
  error_code: '',
  error_detail: '',
  mirror_id: 'mirror-9',
  request: {
    method: 'POST',
    path: '/files/2026-09/files',
    content_type: 'multipart/form-data',
    required_fields: ['file'],
    optional_fields: ['folderId', 'fileName'],
    fields: { file: 'northwind-rollout-deck.pdf', fileName: 'northwind-rollout-deck.pdf' },
    byte_size: 18,
    sha256: 'aaaa',
  },
  uploaded: { file_id: 'file-new', folder_id: '', simulated: true },
  attachment: { mode: 'note', record_id: 'fabrikam-dl-9001', simulated: true },
  simulated: true,
  asset: { ...ASSETS.assets[0], crm_file_id: 'file-new', state: 'mirrored' },
}

export const FAILED_MIRROR = {
  outcome: 'failed',
  error_code: 'unsupported_system',
  error_detail: VOCABULARY.unsourced_systems.salesforce,
  mirror_id: 'mirror-10',
  request: {},
  uploaded: {},
  attachment: {},
  simulated: false,
  asset: {
    ...ASSETS.assets[0],
    state: 'failed',
    state_note: VOCABULARY.mirror_state_notes.failed,
    crm_file_id: '',
  },
}

export const RECONCILE_RESULT = {
  room_id: ROOM.id,
  outcome: 'reconciled',
  rule: VOCABULARY.reconcile_rule,
  mirrored: 1,
  candidates: 1,
  results: [{ asset_id: 'asset-2', outcome: 'mirrored', error_code: '', crm_file_id: 'file-new' }],
  interval_seconds: 300,
  connection_intervals: { 'conn-1': 300, 'conn-2': 300 },
  at: '2026-10-01T09:10:00.000+00:00',
}

export const NOT_DUE = {
  room_id: ROOM.id,
  outcome: 'not_due',
  note: 'no connection was due. The reconciliation floor is a floor a scheduler respects, not a timer this package runs.',
  interval_seconds: 300,
  connection_intervals: { 'conn-1': 300, 'conn-2': 300 },
  mirrored: 0,
  results: [],
  candidates: 0,
}

/**
 * Route the stubbed fetch answers from. Keyed on this feature's own prefix, so a
 * change to a route name is a change to one file.
 */
export function routes(url, options = {}) {
  const path = String(url).split('?')[0]
  const method = options.method || 'GET'
  const json = options.body ? JSON.parse(options.body) : {}

  if (path === '/api/records/room') return { body: ROOMS }
  if (path === '/api/wf-047/vocabulary') return { body: VOCABULARY }
  if (path === '/api/wf-047/vendors') return { body: VENDORS }
  if (path === '/api/wf-047/inferences') return { body: INFERENCES }

  if (path === '/api/wf-047/connections') {
    if (method === 'POST') {
      return {
        status: 201,
        body: {
          ...json,
          id: 'conn-3',
          folder_recommendation_followed: Boolean(json.folder_id),
        },
      }
    }
    return { body: CONNECTIONS }
  }
  if (path.startsWith('/api/wf-047/connections/')) return { body: CONNECTIONS.connections[0] }

  if (path === '/api/wf-047/rooms/room-1/documents') return { body: ASSETS }
  if (path === '/api/wf-047/rooms/room-1/files') return { body: FILES }
  if (path === '/api/wf-047/rooms/room-1/mirrors') return { body: MIRRORS }
  if (path === '/api/wf-047/rooms/room-1/summary') return { body: SUMMARY }
  if (path === '/api/wf-047/rooms/room-1/assets') {
    if (method === 'POST') return { status: 201, body: { asset: ASSETS.assets[0] } }
    return { body: ASSETS }
  }
  if (path === '/api/wf-047/rooms/room-1/assets/asset-1') {
    if (method === 'PATCH') {
      return { body: { ...ASSETS.assets[0], ...json, note: 'the file already in the CRM stays there' } }
    }
    return { body: ASSETS.assets[0] }
  }
  if (path.endsWith('/mirror')) {
    if (json && json.system === 'salesforce') return { body: FAILED_MIRROR }
    return { body: MIRROR_RESULT }
  }
  if (path === '/api/wf-047/rooms/room-1/reconcile') return { body: RECONCILE_RESULT }
  if (path === `/api/wf-047/rooms/${ROOM_2.id}/documents`) {
    return { body: { ...ASSETS, room_id: ROOM_2.id, count: 0, assets: [] } }
  }
  if (path === `/api/wf-047/rooms/${ROOM_2.id}/files`) {
    return { body: { ...FILES, room_id: ROOM_2.id, count: 0, files: [] } }
  }
  if (path === `/api/wf-047/rooms/${ROOM_2.id}/mirrors`) {
    return { body: { ...MIRRORS, room_id: ROOM_2.id, count: 0, mirrors: [] } }
  }
  if (path === `/api/wf-047/rooms/${ROOM_2.id}/summary`) {
    return { body: { ...SUMMARY, room_id: ROOM_2.id, assets: 0, mirrors: 0 } }
  }
  if (path === `/api/wf-047/rooms/${ROOM_2.id}/connections`) {
    return { body: { ...CONNECTIONS, room_id: ROOM_2.id, count: 0, connections: [] } }
  }
  return { status: 404, body: { error: 'not_found', detail: path } }
}