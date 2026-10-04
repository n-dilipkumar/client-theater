/**
 * The fixture contract for the WF-050 gap reconciliation page.
 *
 * Every payload here is the shape the server actually produces, so a drift between
 * this file and the API is a drift between the page and the API rather than a test
 * that quietly passes. `backend/tests/test_wf050.py` pins the server half.
 */

export const ROOM = {
  id: 'room-northwind',
  name: 'Northwind Traders',
  account: 'Northwind Traders',
}

export const ROOM_QUIET = {
  id: 'room-quiet',
  name: 'Contoso quiet',
  account: 'Contoso',
}

export const ROOM_BROKEN = { id: 'room-broken', name: 'Broken', account: 'Broken' }

export const VOCABULARY = {
  gap_change_types: ['GAP_CREATE', 'GAP_UPDATE', 'GAP_DELETE', 'GAP_UNDELETE'],
  overflow_change_type: 'GAP_OVERFLOW',
  change_types: [
    'GAP_CREATE',
    'GAP_UPDATE',
    'GAP_DELETE',
    'GAP_UNDELETE',
    'GAP_OVERFLOW',
  ],
  gap_operations: {
    GAP_CREATE: 'create',
    GAP_UPDATE: 'update',
    GAP_DELETE: 'delete',
    GAP_UNDELETE: 'undelete',
  },
  vendors: ['salesforce', 'dataverse'],
  unsupported_vendors: ['hubspot'],
  unsupported_vendor_quote:
    'HubSpot has no documented gap/overflow analogue (it uses webhook redelivery instead)',
  cursor_kinds: [
    { kind: 'replay_id', meaning: 'The Salesforce Replay ID', scope: 'entity' },
    { kind: 'delta_link', meaning: 'The Dataverse delta link', scope: 'org' },
  ],
  dirty_states: ['dirty', 'reconciled'],
  dirty_key: ['room_id', 'entity', 'record_id'],
  run_states: [
    { value: 'open', terminal: false },
    { value: 'complete', terminal: true },
    { value: 'expired_cursor', terminal: true },
    { value: 'refused', terminal: true },
  ],
  deleted_sources: [
    { value: 'difference', meaning: 'Option a', vendor: 'any' },
    { value: 'recycle_bin', meaning: 'Option b', vendor: 'salesforce' },
    { value: 'dataverse_delta', meaning: 'Dataverse inline', vendor: 'dataverse' },
  ],
  numbers: {
    overflow_change_threshold: { value: 100000, quote: 'more than 100,000 changes' },
    change_tracking_expiry_days: { value: 7, quote: 'within a default value of seven days' },
  },
  last_modified_field: 'LastModifiedDate',
  classification:
    'a gap event names one record and is repaired by one read; an overflow event names no record and is repaired by a whole-entity read plus a delete diff',
}

const GAP_EVENTS = [
  {
    id: 'crm_gap_event_1',
    room_id: ROOM.id,
    change_type: 'GAP_UPDATE',
    kind: 'gap',
    operation: 'update',
    vendor: 'salesforce',
    entity: 'Opportunity',
    record_ids: ['006A000001'],
    commit_timestamp: '2026-09-27T10:00:00+00:00',
    transaction_key: 'tx-1',
    change_count: null,
    exceeds_overflow_threshold: false,
    replay_id: null,
    subscription_state: 'subscribed',
  },
  {
    id: 'crm_gap_event_2',
    room_id: ROOM.id,
    change_type: 'GAP_DELETE',
    kind: 'gap',
    operation: 'delete',
    vendor: 'salesforce',
    entity: 'Opportunity',
    record_ids: ['006A000003'],
    commit_timestamp: '2026-09-27T11:00:00+00:00',
    transaction_key: 'tx-2',
    change_count: null,
    exceeds_overflow_threshold: false,
    replay_id: null,
    subscription_state: 'subscribed',
  },
  {
    id: 'crm_gap_event_4',
    room_id: ROOM.id,
    change_type: 'GAP_CREATE',
    kind: 'gap',
    operation: 'create',
    vendor: 'salesforce',
    entity: 'Opportunity',
    record_ids: ['006A000009'],
    commit_timestamp: '2026-09-27T12:00:00+00:00',
    transaction_key: 'tx-4',
    change_count: null,
    exceeds_overflow_threshold: false,
    replay_id: null,
    subscription_state: 'subscribed',
  },
  {
    id: 'crm_gap_event_3',
    room_id: ROOM.id,
    change_type: 'GAP_OVERFLOW',
    kind: 'overflow',
    operation: '',
    vendor: 'salesforce',
    entity: 'Opportunity',
    record_ids: [],
    commit_timestamp: '2026-09-27T12:00:00+00:00',
    transaction_key: 'tx-overflow',
    change_count: 150000,
    exceeds_overflow_threshold: true,
    replay_id: 'seed-replay-0',
    subscription_state: 'unsubscribed',
  },
]

const HEALTH = {
  room_id: ROOM.id,
  dirty_count: 1,
  reconciled_count: 1,
  gap_event_count: 4,
  open_run_count: 0,
  run_count: 1,
  unresumable_cursors: [],
  clean: false,
  summary: '1 record(s) are dirty and owe a full re-read',
  dirty_records: [],
  cursors: [],
}

const DIRTY = {
  room_id: ROOM.id,
  state: 'dirty',
  count: 1,
  records: [
    {
      id: 'crm_gap_dirty_1',
      room_id: ROOM.id,
      entity: 'Opportunity',
      record_id: '006A000009',
      state: 'dirty',
      dirty: true,
      gap_commit_timestamp: '2026-09-27T12:00:00+00:00',
      gap_event_id: 'crm_gap_event_4',
      change_type: 'GAP_CREATE',
      age_note: 'dirty for 4h',
      run_id: null,
    },
  ],
}

const CURSORS = {
  room_id: ROOM.id,
  count: 1,
  note: 'a Salesforce Replay ID is scoped to one entity type and does not expire',
  cursors: [
    {
      id: 'crm_gap_cursor_1',
      kind: 'replay_id',
      vendor: 'salesforce',
      scope: 'entity',
      scope_value: 'Opportunity',
      position: 'seed-replay-0',
      held_since: '2026-09-27T12:00:00+00:00',
      age_days: 0.0,
      expiry_days: 7,
      applies: false,
      present: true,
      resumable: true,
      state: 'resumable',
      fallback: '',
      reason: '',
    },
  ],
}

const RUNS = {
  room_id: ROOM.id,
  count: 1,
  runs: [
    {
      id: 'crm_gap_run_1',
      room_id: ROOM.id,
      state: 'complete',
      terminal: true,
      kind: 'gap',
      vendor: 'salesforce',
      entity: 'Opportunity',
      record_id: '006A000001',
      event_id: 'crm_gap_event_1',
      deleted_source: 'difference',
      counts: { written: 1, deleted: 0 },
      opened_at: '2026-09-27T10:05:00+00:00',
      closed_at: '2026-09-27T10:05:01+00:00',
    },
  ],
}

const LOG_LINES = [
  {
    id: 'crm_gap_log_1',
    run_id: 'crm_gap_run_1',
    seq: 1,
    event: 'run_opened',
    detail: 'gap on Opportunity',
  },
  {
    id: 'crm_gap_log_2',
    run_id: 'crm_gap_run_1',
    seq: 2,
    event: 'record_read',
    detail: 'the vendor returned Opportunity/006A000001',
  },
  {
    id: 'crm_gap_log_3',
    run_id: 'crm_gap_run_1',
    seq: 3,
    event: 'dirty_flag_cleared',
    detail: 'Opportunity/006A000001',
  },
]

const QUIET_HEALTH = { ...HEALTH, dirty_count: 0, gap_event_count: 0, run_count: 0, clean: true, summary: 'no record is dirty; every gap this room saw has been repaired' }
const QUIET_DIRTY = { room_id: ROOM_QUIET.id, state: 'dirty', count: 0, records: [] }
const QUIET_EVENTS = { room_id: ROOM_QUIET.id, count: 0, events: [] }
const QUIET_CURSORS = { room_id: ROOM_QUIET.id, count: 0, note: '', cursors: [] }
const QUIET_RUNS = { room_id: ROOM_QUIET.id, count: 0, runs: [] }

export const REPORTED = {
  event: GAP_EVENTS[0],
  kind: 'gap',
  replay_id_stored: false,
  dirty_records: [DIRTY.records[0]],
}

export const RECONCILED = {
  run: RUNS.runs[0],
  scope: 'record',
  dirty_cleared: true,
  replica: {
    id: 'crm_gap_replica_1',
    room_id: ROOM.id,
    entity: 'Opportunity',
    record_id: '006A000001',
    deleted: false,
    payload: { Name: 'Northwind rollout', Amount: 48000 },
    reconciled_at: '2026-09-27T10:05:01+00:00',
    run_id: 'crm_gap_run_1',
    event_id: 'crm_gap_event_1',
  },
}

export const DROPPED = {
  applied: false,
  reason: 'dirty_and_newer_than_the_read',
  entity: 'Opportunity',
  record_id: '006A000001',
  dirty: true,
  detail:
    'the change is newer than the record the vendor returned, so it has not reached this room and the dirty marker stays open for the next re-read',
}

export const SUBSCRIBED = {
  subscription_state: 'subscribed',
  resubscribed_at: '2026-09-27T13:00:00+00:00',
  runs_closed: [],
  runs_annotated: [{ ...RUNS.runs[0] }],
  cursor: CURSORS.cursors[0],
  cursors: CURSORS.cursors,
}

/** Every room the picker offers. */
export const ROOMS = [ROOM, ROOM_QUIET, ROOM_BROKEN]

/**
 * Dispatch on the URL and return `{ status, body }`, so one place decides what the
 * API looks like. Every path is this feature's own prefix.
 */
export function routes(url, options = {}) {
  const path = String(url).split('?')[0]
  const method = options.method || 'GET'

  // The room decides first. Without this, a quiet room and a broken room both get
  // the busy room's answer, because every surface below matches on its own suffix.
  if (path.includes(ROOM_BROKEN.id)) {
    return { status: 500, body: { error: 'boom', detail: 'the vendor call failed' } }
  }
  if (path.includes(ROOM_QUIET.id)) {
    if (path.endsWith('/dirty')) return { status: 200, body: QUIET_DIRTY }
    if (path.endsWith('/health')) return { status: 200, body: QUIET_HEALTH }
    if (path.endsWith('/gap-events')) return { status: 200, body: QUIET_EVENTS }
    if (path.endsWith('/cursors')) return { status: 200, body: QUIET_CURSORS }
    if (path.endsWith('/runs')) return { status: 200, body: QUIET_RUNS }
    return { status: 404, body: { detail: 'no route' } }
  }

  if (path.endsWith('/vocabulary')) return { status: 200, body: VOCABULARY }
  if (path.endsWith('/inferences')) {
    return {
      status: 200,
      body: {
        sourced: { gap_types: 'GAP_CREATE, GAP_UPDATE, GAP_DELETE, GAP_UNDELETE' },
        research_gap: 'not reconciled in a single source',
        inferred: [{ id: 'vendor-scope', decision: 'salesforce_and_dataverse_only' }],
        ids: ['vendor-scope'],
        count: 1,
      },
    }
  }
  if (path === '/api/records/room') return { status: 200, body: { records: ROOMS } }

  if (path.includes('/gap-events') && method === 'POST') {
    return { status: 200, body: REPORTED }
  }
  if (path.endsWith('/gap-events')) {
    return { status: 200, body: { room_id: ROOM.id, count: GAP_EVENTS.length, events: GAP_EVENTS } }
  }
  if (/\/gap-events\/[^/]+$/.test(path)) {
    return { status: 200, body: GAP_EVENTS[0] }
  }
  if (path.endsWith('/dirty')) return { status: 200, body: DIRTY }
  if (path.endsWith('/health')) return { status: 200, body: HEALTH }
  if (path.endsWith('/cursors')) return { status: 200, body: CURSORS }
  if (path.endsWith('/runs')) return { status: 200, body: RUNS }
  if (/\/runs\/[^/]+\/log$/.test(path)) {
    return { status: 200, body: { run_id: 'crm_gap_run_1', count: LOG_LINES.length, lines: LOG_LINES } }
  }
  if (/\/runs\/[^/]+$/.test(path)) return { status: 200, body: { ...RUNS.runs[0], log: LOG_LINES } }
  if (path.endsWith('/replica')) {
    return {
      status: 200,
      body: {
        room_id: ROOM.id,
        count: 1,
        rows: [RECONCILED.replica],
        ownership: 'crm_gap_replica. Another feature owns the streaming replica.',
      },
    }
  }
  if (path.endsWith('/reconcile')) return { status: 200, body: RECONCILED }
  if (path.endsWith('/subscribe')) return { status: 200, body: SUBSCRIBED }
  if (path.endsWith('/change-events')) return { status: 200, body: DROPPED }

  return { status: 404, body: { detail: 'no route' } }
}

export { CURSORS, DIRTY, GAP_EVENTS, HEALTH, LOG_LINES, RUNS }