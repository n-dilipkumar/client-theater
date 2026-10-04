/**
 * Fixtures for the WF-042 CRM read panel tests.
 *
 * One place decides what this feature's API looks like, so a change to a route
 * name is a change to one file rather than to every test that stubs it. The
 * payloads are the researched shapes verbatim: the panel's five named fields, the
 * three paging modes, the vendor limits, and both display-label sources.
 */

export const ROOM = { id: 'room-1', name: 'Northwind Q4' }

export const ROOM_2 = { id: 'room-empty', name: 'Contoso pilot' }

export const VOCABULARY = {
  systems: ['salesforce', 'dataverse', 'hubspot'],
  objects: ['deal', 'contact', 'account'],
  display_labels: { salesforce: false, dataverse: true, hubspot: false },
  cache_states: ['fresh', 'stale', 'absent', 'expired'],
  read_outcomes: [
    'complete',
    'paged',
    'empty',
    'truncated_at_vendor_limit',
    'capability_fallback',
  ],
  identity_source: 'room_mapping',
  collections: [
    'crm_read_identity',
    'crm_read_option_set',
    'crm_read_record',
    'crm_read_snapshot',
    'crm_read_query',
  ],
  paging: {
    modes: {
      salesforce: 'query_locator',
      dataverse: 'odata_next_link',
      hubspot: 'paging_cursor',
    },
  },
  limits: {
    salesforce: { synchronous_records_per_request: 2000 },
    dataverse: { standard_rows_per_request: 5000, elastic_rows_per_request: 500, max_conditions: 500 },
    hubspot: {
      batch_read_ids: 100,
      objects_per_page: 200,
      query_characters: 3000,
      search_results: 10000,
    },
  },
}

export const PANEL_WITH_CRM = {
  crm_context: true,
  identity: { id: 'crm_read_identity_1', buyer_email: 'dana@northwind.example', system: 'salesforce' },
  outcome: 'capability_fallback',
  display_labels: false,
  label_source: 'room option sets',
  fallback_fills: ['deal.stage'],
  unlabelled: [{ object: 'account', field: 'account_industry', rows: 1 }],
  cache: {
    state: 'fresh',
    served_from_cache: false,
    age_seconds: 4,
    ttl_seconds: 300,
    expires_at: '2026-10-04T12:05:00+00:00',
    read_at: '2026-10-04T12:00:00+00:00',
    refresh_mode: 'pull',
    refresh_alternative: 'change_tracking',
  },
  panel: {
    system: 'salesforce',
    buyer: { email: 'dana@northwind.example', name: 'Dana Okafor' },
    deal: {
      found: true,
      name: 'Northwind rollout',
      stage: { value: 'Negotiation', label: 'In negotiation' },
      amount: 48000,
      external_id: '006-nw-5001',
    },
    contact: {
      found: true,
      name: 'Dana Okafor',
      title: 'VP Operations',
      email: 'dana@northwind.example',
      external_id: '003-nw-1001',
    },
    account: {
      found: true,
      name: 'Northwind Traders',
      industry: { value: 'Manufacturing', label: '' },
      external_id: '001-nw-0001',
    },
    read_set: [
      { object: 'deal', crm_field: 'Name', room_field: 'deal_name' },
      { object: 'deal', crm_field: 'StageName', room_field: 'stage' },
      { object: 'deal', crm_field: 'Region__c', room_field: 'deal_region' },
    ],
    objects_read: ['account', 'contact', 'deal'],
  },
  reads: {
    deal: { total: 1, returned: 1, done: true, read_set: ['Name', 'StageName', 'Region__c'] },
    contact: { total: 1, returned: 1, done: true, read_set: ['Name', 'Title', 'Email'] },
    account: { total: 1, returned: 1, done: true, read_set: ['Name', 'Industry'] },
  },
  queries: [],
}

/**
 * The room the research describes: authored by a seller with no CRM context.
 * The status code for this is 200, and the body says why.
 */
export const PANEL_WITHOUT_CRM = {
  crm_context: false,
  identity: null,
  reason:
    'no CRM identity is registered for this buyer in this room. A seller may author a room ' +
    'without CRM context; the deal panel renders without CRM fields and the rest of the room ' +
    'is unaffected.',
  asked_for: { identity_id: null, buyer_email: 'nobody@example.test' },
  panel: {
    system: null,
    buyer: { email: 'nobody@example.test', name: null },
    deal: { found: false, name: null, stage: { value: null, label: '' }, amount: null },
    contact: { found: false, name: null, title: null, email: 'nobody@example.test' },
    account: { found: false, name: null, industry: { value: null, label: '' } },
    read_set: [],
    objects_read: [],
  },
  reads: {},
  display_labels: false,
  label_source: 'none',
  outcome: 'empty',
  cache: { state: 'absent', served_from_cache: false, age_seconds: null, ttl_seconds: 300 },
  queries: [],
}

export const QUERY_LOG = [
  {
    id: 'crm_read_query_3',
    object: 'account',
    vendor_name: 'Account',
    method: 'GET',
    endpoint: 'query',
    path: '/services/data/v61.0/query',
    query: { q: "SELECT Id, Name, Industry FROM Account WHERE Id = '001-nw-0001' LIMIT 200" },
    total: 1,
    returned: 1,
    done: true,
  },
  {
    id: 'crm_read_query_2',
    object: 'contact',
    vendor_name: 'Contact',
    method: 'GET',
    endpoint: 'query',
    path: '/services/data/v61.0/query',
    query: { q: "SELECT Id, Name, Title, Email FROM Contact WHERE Id = '003-nw-1001' LIMIT 200" },
    total: 1,
    returned: 1,
    done: true,
  },
  {
    id: 'crm_read_query_1',
    object: 'deal',
    vendor_name: 'Opportunity',
    method: 'GET',
    endpoint: 'query',
    path: '/services/data/v61.0/query',
    query: {
      q: "SELECT Id, Name, StageName, Region__c FROM Opportunity WHERE Id = '006-nw-5001' LIMIT 200",
    },
    total: 1,
    returned: 1,
    done: true,
  },
]

/**
 * Resolve one request to the body the page will receive.
 *
 * `room-broken` answers 500 on the panel read so the error branch has something to
 * render, and `room-quiet` answers a room with identities but no read plan yet.
 * Both are states this workflow produces, and both need a page.
 */
export function routes(url, options = {}) {
  const body = { status: 200, body: {} }
  if (url.includes('/wf-042/vocabulary')) return { status: 200, body: VOCABULARY }
  if (url.includes('/wf-042/panel/pull')) {
    return { status: 200, body: { status: 'ok', refresh: true } }
  }
  if (url.includes('/room-broken') && url.includes('/panel')) {
    return { status: 500, body: { detail: 'the vendor refused the read' } }
  }
  if (url.includes('/rooms/') && url.includes('/panel')) {
    if (url.includes('room-empty')) return { status: 200, body: PANEL_WITHOUT_CRM }
    return { status: 200, body: PANEL_WITH_CRM }
  }
  if (url.includes('/rooms/') && url.includes('/queries')) {
    if (url.includes('room-quiet')) return { status: 200, body: { count: 0, queries: [] } }
    return { status: 200, body: { count: QUERY_LOG.length, queries: QUERY_LOG } }
  }
  if (url.includes('/rooms/') && url.includes('/cache')) {
    return {
      status: 200,
      body: { fresh: 1, stale: 0, absent: 0, expired: 0, ttl_bounds: [30, 3600] },
    }
  }
  if (url.includes('/wf-042/identities')) {
    if (options.method === 'POST') return { status: 201, body: { id: 'crm_read_identity_2' } }
    if (url.includes('room-empty')) return { status: 200, body: { count: 0, identities: [] } }
    return {
      status: 200,
      body: {
        count: 1,
        identities: [
          { id: 'crm_read_identity_1', buyer_email: 'dana@northwind.example', system: 'salesforce' },
        ],
      },
    }
  }
  if (url.includes('/records/room')) {
    return { status: 200, body: { count: 2, records: [ROOM, ROOM_2] } }
  }
  return body
}