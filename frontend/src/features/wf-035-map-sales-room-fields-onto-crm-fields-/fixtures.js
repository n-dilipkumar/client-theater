/**
 * Fixtures shaped like WF-035's real API responses.
 *
 * Hand-written from the backend's own shapes rather than generated, because the
 * point of these tests is to pin the contract between the two layers: if the
 * backend renames a field, these break, which is the signal we want.
 *
 * The stub lives here rather than in `src/test/fixtures.js` because that file
 * hard-codes `/wf-017-white-label` as its base and belongs to another feature.
 * Editing it to add a second prefix would be the shared-file collision the plugin
 * host exists to prevent, so this feature keeps its own.
 */

export const BASE = '/wf-035'

export const VOCABULARY = {
  directions: ['in', 'out', 'both'],
  providers: ['hubspot', 'dataverse', 'salesforce'],
  room_field_types: ['text', 'email', 'url', 'id', 'number', 'date', 'datetime', 'boolean', 'enumeration'],
  metadata_endpoints: {
    hubspot: [
      {
        method: 'GET',
        url: '/crm/properties/2026-09/{object}',
        purpose: 'Discover target properties, types and option sets.',
        document: 'HubSpot CRM properties guide',
        sourced: true,
      },
    ],
    dataverse: [],
    salesforce: [],
  },
  unique_key_limit: 10,
  transforms: [
    { name: 'identity', version: 1, key: 'identity@1', applies_to: [], builtin: true },
    { name: 'email.normalize', version: 1, key: 'email.normalize@1', applies_to: ['email'], builtin: true },
    { name: 'picklist.map', version: 1, key: 'picklist.map@1', applies_to: ['enumeration'], builtin: true },
  ],
}

export const INFERENCES = {
  count: 2,
  inferences: [
    {
      id: 'salesforce_external_id_unsourced',
      question: 'What happens on a Salesforce connection?',
      decision: 'No create request is emitted.',
      why: 'The research records a sourcing gap.',
      change_it: 'SALESFORCE_GAP in dsr.fieldmap.sync_key',
    },
    {
      id: 'metadata_is_recorded_not_fetched',
      question: 'Why does this workflow not fetch?',
      decision: 'A connector reads and posts the document.',
      why: 'The token vault is WF-034.',
      change_it: 'Swap MetadataReader behind the properties routes.',
    },
  ],
}

export const SUMMARY = {
  connections: 1,
  mappings: 1,
  rows: 1,
  by_state: { draft: 1 },
  by_provider: { hubspot: 1 },
  active: 0,
  draft: 1,
  sync_keys_pinned: 1,
  validations: 1,
  metadata_reads: 1,
  objects_without_metadata: [],
  activatable: [],
  transforms: 3,
  declared_transforms: 1,
  unique_key_limit: 10,
  inferences: 2,
}

export const CONNECTIONS = {
  count: 1,
  connections: [
    {
      id: 'conn_1',
      room_id: 'room_1',
      name: 'HubSpot — Northwind portal',
      provider: 'hubspot',
      account: 'Northwind Traders',
      connected: true,
      property_group: 'contactinformation',
      mapping_count: 1,
    },
  ],
}

export const PROPERTIES = {
  provider: 'hubspot',
  crm_object: 'contacts',
  document: 'GET /crm/properties/2026-09/{object}',
  fetched_at: '2026-09-26T10:00:00.000+00:00',
  age_seconds: 120,
  stale: false,
  property_count: 3,
  groups: ['contactinformation'],
  keys: [],
  unique_property_count: 0,
  unique_key_usage: 0,
  unique_key_limit: 10,
  properties: [
    { name: 'dsr_row_id', value_type: 'string', field_type: 'text', group: 'contactinformation', options: [] },
    { name: 'email', value_type: 'string', field_type: 'text', group: 'contactinformation', options: [] },
    {
      name: 'lifecyclestage',
      value_type: 'enumeration',
      field_type: 'select',
      group: 'salesforce',
      options: [
        { value: 'lead', label: 'Lead' },
        { value: 'customer', label: 'Customer' },
      ],
    },
  ],
}

const CLEAN_ROW = {
  row_id: 'row_1',
  source_field: 'primary_contact_email',
  source_type: 'email',
  target_property: 'email',
  direction: 'out',
  transform: 'email.normalize',
  status: 'ok',
  findings: [],
  flags: [],
}

export const MAPPINGS = {
  count: 1,
  mappings: [
    {
      id: 'map_1',
      room_id: 'room_1',
      connection_id: 'conn_1',
      connection_name: 'HubSpot — Northwind portal',
      provider: 'hubspot',
      crm_object: 'contacts',
      crm_object_label: 'Contacts',
      state: 'draft',
      sync_key: { properties: ['dsr_row_id'], unique: true },
      defaults_applied: false,
      row_count: 1,
      rows: [CLEAN_ROW],
      validation: { counts: { ok: 1, warning: 0, error: 0 }, blocking: [] },
      metadata: { recorded: true, document: 'GET /crm/properties/2026-09/{object}', fetched_at: '2026-09-26T10:00:00.000+00:00', property_count: 3 },
    },
  ],
}

export const MAPPING = MAPPINGS.mappings[0]

/** The same mapping before any property read has been recorded for its object. */
export const MAPPING_WITHOUT_METADATA = {
  ...MAPPING,
  metadata: { recorded: false, document: '', fetched_at: '', property_count: 0 },
}

export const NO_METADATA = {
  error: 'metadata_unavailable',
  detail: 'no property metadata has been recorded for this object',
  endpoints: [],
}

export const SYNC_KEY = {
  mapping_id: 'map_1',
  pinned: true,
  sync_key: {
    properties: ['dsr_row_id'],
    unique: true,
    pinned_at: '2026-09-26T10:01:00.000+00:00',
    usage: { used: 9, needed: 1, limit: 10, remaining: 0 },
  },
  limit: 10,
  already_enforced: false,
  key_section: { pinned: true, status: 'ok', findings: [], flags: [] },
  plan: {
    provider: 'hubspot',
    sourced: true,
    gap: '',
    crm_object: 'contacts',
    note: 'To create a property requiring unique values via API: hasUniqueValue set to true.',
    steps: [
      {
        step: 1,
        method: 'POST',
        url: '/crm/properties/2026-09/contacts',
        body: {
          groupName: 'contactinformation',
          name: 'dsr_row_id',
          label: 'dsr_row_id',
          type: 'string',
          fieldType: 'text',
          hasUniqueValue: true,
        },
        sourced: true,
      },
    ],
  },
  usage: { used: 9, needed: 1, limit: 10, remaining: 0 },
}

export const VALIDATION = {
  validation: {
    mapping_id: 'map_1',
    counts: { ok: 1, warning: 0, error: 0 },
    blocking: [],
    rows: [CLEAN_ROW],
    researched_flags: ['unknown_property', 'type_mismatch', 'unsupported_option'],
  },
  validated: true,
  can_activate: true,
  stale: false,
  reason: '',
}

export const TRANSFORMS = {
  registered: VOCABULARY.transforms,
  names: ['email.normalize', 'identity', 'picklist.map'],
  versions: { 'email.normalize': [1], identity: [1], 'picklist.map': [1] },
  declared: [
    {
      id: 'decl_1',
      name: 'account.hierarchy_rollup',
      version: 1,
      key: 'account.hierarchy_rollup@1',
      description: "Resolve an account's ultimate parent before writing.",
      executable: false,
    },
  ],
}

export const PREVIEW = {
  mapping_id: 'map_1',
  crm_object: 'contacts',
  record_id: 'r1',
  out: { email: 'ada@example.com', dsr_row_id: 'r1' },
  in: {},
  sync_key: { property: 'dsr_row_id', value: 'r1', injected: true, unique: true },
  trace: [
    {
      row_id: 'row_1',
      source_field: 'primary_contact_email',
      direction: 'out',
      target_property: 'email',
      transform: 'email.normalize',
      status: 'ok',
      before: ' Ada@Example.COM ',
      after: 'ada@example.com',
      reason: '',
      detail: {},
    },
  ],
  counts: { out: 2, in: 0, ok: 1, skipped: 0, error: 0 },
  writable: true,
  wrote_anything: false,
}

/** A report carrying one finding of every severity the grid can render. */
export const REPORT_WITH_FINDINGS = {
  mapping_id: 'map_1',
  crm_object: 'contacts',
  counts: { ok: 1, warning: 1, error: 2 },
  blocking: ['unknown_property', 'unsupported_option'],
  researched_flags: ['unknown_property', 'type_mismatch', 'unsupported_option'],
  rows: [
    CLEAN_ROW,
    {
      row_id: 'row_2',
      source_field: 'buyer_stage',
      source_type: 'enumeration',
      target_property: 'lifecyclestage',
      direction: 'out',
      transform: 'picklist.map',
      status: 'error',
      flags: ['unsupported_option'],
      findings: [
        {
          flag: 'unsupported_option',
          severity: 'error',
          message:
            "Row buyer_stage: 1 value(s) on the value side of the picklist table are not internal option values of 'lifecyclestage'.",
          detail: { internal_values: ['lead', 'customer'] },
        },
      ],
    },
    {
      row_id: 'row_3',
      source_field: 'internal_note',
      source_type: 'text',
      target_property: '',
      direction: 'out',
      transform: 'identity',
      status: 'warning',
      flags: ['no_target'],
      findings: [
        {
          flag: 'no_target',
          severity: 'warning',
          message: 'Row internal_note has no CRM property, so nothing is sent for it.',
          detail: {},
        },
      ],
    },
  ],
  sync_key: { pinned: true, status: 'ok', findings: [], flags: [], properties: ['dsr_row_id'] },
  can_activate: false,
  metadata: { document: 'GET /crm/properties/2026-09/{object}', fetched_at: '2026-09-26T10:00:00.000+00:00', property_count: 3 },
}

const HTTP_ERROR = Symbol('httpError')
export const httpError = (status, body) => ({ [HTTP_ERROR]: true, status, body })

/**
 * Install a fetch stub that answers by path.
 *
 * An unlisted path throws loudly, so a test cannot silently pass against the wrong
 * response. The match is a whole-segment prefix, longest key first, so a stub for
 * `.../map_1` cannot answer a request for `.../map_1-typo`.
 */
export function stubApi(routes) {
  const calls = []
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  const matches = (path, candidate) =>
    path === candidate || path.startsWith(`${candidate}/`) || path.startsWith(`${candidate}?`)

  globalThis.fetch = async (url, options = {}) => {
    const path = String(url).replace('/api', '')
    calls.push({ path, method: options.method || 'GET', body: options.body })

    const key = keys.find((candidate) => matches(path, candidate))
    if (!key) throw new Error(`unstubbed request: ${path}`)

    const entry = routes[key]
    const failure = entry && typeof entry === 'object' && entry[HTTP_ERROR] ? entry : null
    const status = failure ? failure.status : 200
    const body = failure ? failure.body : entry

    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: failure ? 'Error' : 'OK',
      json: async () => (typeof body === 'function' ? body() : body),
    }
  }
  return calls
}

/** The routes a fully-loaded page needs. Overrides merge one level deep. */
export function routes(overrides = {}) {
  return {
    [`${BASE}/vocabulary`]: VOCABULARY,
    [`${BASE}/inferences`]: INFERENCES,
    [`${BASE}/summary`]: SUMMARY,
    [`${BASE}/transforms`]: TRANSFORMS,
    [`${BASE}/connections`]: CONNECTIONS,
    [`${BASE}/connections/conn_1`]: { connection: CONNECTIONS.connections[0], mappings: MAPPINGS.mappings, metadata: [] },
    [`${BASE}/connections/conn_1/mappings`]: MAPPINGS,
    [`${BASE}/connections/conn_1/mappings/map_1`]: MAPPING,
    [`${BASE}/connections/conn_1/mappings/map_1/validation`]: VALIDATION,
    [`${BASE}/connections/conn_1/mappings/map_1/sync-key`]: SYNC_KEY,
    [`${BASE}/connections/conn_1/properties`]: PROPERTIES,
    [`${BASE}/connections/conn_1/mappings/map_1/preview`]: PREVIEW,
    ...overrides,
  }
}
