/**
 * Fixtures shaped like WF-076's own responses.
 *
 * Hand-written from the backend's route shapes rather than generated, because
 * the point of these tests is to pin the contract between the two halves. The
 * prefix appears once, in `BASE`; every stub is keyed on this feature's own
 * paths, so a request to another prefix fails loudly rather than being answered
 * by a route that happens to share a prefix's characters.
 */

export const BASE = '/wf-076'
export const ROOM_ID = 'room_northwind'

export const ROOMS = {
  count: 2,
  records: [
    {
      id: ROOM_ID,
      collection: 'room',
      revision: 1,
      data: { name: 'Northwind Traders', account: 'Northwind Traders' },
    },
    {
      id: 'room_contoso',
      collection: 'room',
      revision: 1,
      data: { name: 'Contoso Health', account: 'Contoso Health', frozen: true },
    },
  ],
}

export const VOCABULARY = {
  collections: {
    revocation_link: 'Papermark Link',
    revocation_group: 'DataroomGroup',
  },
  operations: {
    revoke_link: {
      route: 'POST /api/wf-076/rooms/{room_id}/links/{link_id}/revoke',
      effect: 'the public URL stops resolving on the next request',
      row: 'soft-deleted and kept',
      quote: 'Soft-deletes the link. The public URL stops resolving immediately.',
    },
    delete_group: {
      route: 'POST /api/wf-076/rooms/{room_id}/groups/{group_id}/delete',
      effect: 'the group, its memberships, its permissions and every link pointing at it',
      row: 'soft-deleted, one transaction, one audit row naming all of them',
      quote: 'Deletes the group, its memberships, its permissions, AND every share link.',
      confirmation: 'confirm must equal the group id',
    },
  },
  guarantee: {
    when: 'request-time, not scheduled',
    grace_period_minutes: 0,
    cached_copy_recall: 'none',
    row_kept: true,
    reversible: false,
    quote: 'Revocation is request-time, not scheduled.',
  },
  frozen_refusals: ['attach', 'detach', 'move'],
  frozen_rule: 'Revocation is not in that list.',
}

export const INFERENCES = {
  count: 2,
  rule: 'Each entry is a point where the research does not say what to do.',
  inferences: [
    {
      id: 'cascade-keeps-rows',
      claim: 'Group delete cannot be undone, but never says the rows are destroyed.',
      reading: 'Every revocation is a soft delete, including the cascades.',
      basis: 'A hard delete in the workflow about keeping the audit row would be the exception.',
      change_it: 'HARD_DELETE in dsr/revocation/__init__.py',
      blast_radius: 'Every cascade.',
    },
    {
      id: 'membership-gates-resolution',
      claim: 'Removing a group membership is documented; the resolution rule is not.',
      reading: 'A group link resolves only for a live member of its group.',
      basis: 'Otherwise removing one membership would be indistinguishable from nothing.',
      change_it: 'Revocation.resolve',
      blast_radius: 'Every group link.',
    },
  ],
}

export const SUMMARY = {
  collections: ['revocation_link'],
  cascade_collections: ['revocation_link'],
  rooms: 1,
  by_room: { [ROOM_ID]: { revocation_link: 3 } },
  guarantee: { grace_period_minutes: 0, cached_copy_recall: 'none', reversible: false },
  revocation_causes: ['revoked', 'cascade_group', 'cascade_room'],
}

export const STATE = {
  room_id: ROOM_ID,
  frozen: false,
  team: 'Northwind Traders',
  counts: {
    links: 2,
    links_revoked: 1,
    groups: 1,
    members: 2,
    viewers: 3,
    permissions: 1,
    attached: 2,
    detached: 1,
  },
  available_actions: [
    'attach',
    'delete_group',
    'detach',
    'purge',
    'remove_member',
    'revoke_link',
    'set_permissions',
  ],
  refused_actions: {},
  frozen_refusals: ['attach', 'detach', 'move'],
  guarantee: {
    request_time: true,
    grace_period_minutes: 0,
    cached_copy_recall: 'none',
    row_kept_for_audit: true,
    reversible: false,
    hard_delete: false,
  },
}

export function link(overrides = {}) {
  return {
    id: 'revocation_link_one',
    room_id: ROOM_ID,
    slug: 'northwind-overview',
    original_slug: null,
    label: 'Northwind overview',
    target: 'room',
    group_id: null,
    domain: 'share.northwind.example',
    custom_domain: true,
    issued_at: '2026-10-01T09:00:00.000+00:00',
    live: true,
    gated_on_membership: false,
    revoked: false,
    revoked_at: null,
    revoked_by: null,
    revoked_via: null,
    revocation_reason: null,
    slug_released: false,
    reversible: false,
    ...overrides,
  }
}

export const LINKS = {
  room_id: ROOM_ID,
  include_revoked: true,
  count: 3,
  links: [
    link(),
    link({
      id: 'revocation_link_group',
      slug: 'northwind-legal',
      label: 'Legal group link',
      target: 'group',
      group_id: 'revocation_group_legal',
      gated_on_membership: true,
      custom_domain: false,
    }),
    link({
      id: 'revocation_link_gone',
      slug: 'revoked-fabrikam-diligence-9f3a1c02',
      original_slug: 'fabrikam-diligence',
      label: 'Fabrikam diligence (embargoed)',
      live: false,
      revoked: true,
      revoked_at: '2026-10-01T08:00:00.000+00:00',
      revoked_by: 'dana',
      revoked_via: 'revoked',
      revocation_reason: 'Diligence embargoed pending the renewal.',
      slug_released: true,
    }),
  ],
}

export const GROUP = {
  id: 'revocation_group_procurement',
  collection: 'revocation_group',
  room_id: ROOM_ID,
  deleted_at: null,
  data: { name: 'Procurement' },
  members: [
    {
      id: 'revocation_member_one',
      room_id: ROOM_ID,
      deleted_at: null,
      data: { group_id: 'revocation_group_procurement', viewer_email: 'buyer@northwind.example' },
    },
  ],
  permissions: [
    {
      id: 'revocation_permission_one',
      room_id: ROOM_ID,
      deleted_at: null,
      data: {
        group_id: 'revocation_group_procurement',
        document_id: 'doc_contract',
        view: false,
        download: false,
      },
    },
  ],
  links: [],
  member_count: 1,
  permission_count: 1,
  link_count: 0,
}

export const GROUPS = { room_id: ROOM_ID, count: 1, groups: [GROUP] }

export const VIEWERS = {
  room_id: ROOM_ID,
  count: 2,
  viewers: [
    {
      id: 'revocation_viewer_one',
      room_id: ROOM_ID,
      deleted_at: null,
      data: { email: 'buyer@northwind.example' },
      memberships: 1,
      in_any_group: true,
    },
    {
      id: 'revocation_viewer_two',
      room_id: ROOM_ID,
      deleted_at: null,
      data: { email: 'analyst@partner.example' },
      memberships: 0,
      in_any_group: false,
    },
  ],
}

export const DOCUMENTS = {
  room_id: ROOM_ID,
  include_detached: true,
  count: 2,
  documents: [
    {
      id: 'revocation_grant_one',
      room_id: ROOM_ID,
      deleted_at: null,
      data: {
        document_id: 'doc_deck',
        document_team: 'Northwind Traders',
        title: 'Enterprise Overview Deck',
        status: 'attached',
      },
      attached: true,
      document_kept: true,
    },
    {
      id: 'revocation_grant_two',
      room_id: ROOM_ID,
      deleted_at: '2026-10-01T07:00:00.000+00:00',
      data: {
        document_id: 'doc_pricing',
        document_team: 'Northwind Traders',
        title: 'Pricing One-Pager',
        status: 'detached',
      },
      attached: false,
      document_kept: true,
    },
  ],
}

export const TRAIL = {
  room_id: ROOM_ID,
  count: 3,
  trail: [
    {
      seq: 9,
      ts: '2026-10-01T09:30:00.000+00:00',
      action: 'delete',
      source: `POST ${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`,
      actor: 'sam',
      room_id: ROOM_ID,
      record_id: 'revocation_link_one',
      collection: 'revocation_link',
      summary: 'deleted revocation_link revocation_link_one',
      counts: null,
    },
    {
      seq: 8,
      ts: '2026-10-01T09:29:59.000+00:00',
      action: 'delete',
      source: `POST ${BASE}/rooms/${ROOM_ID}/groups/revocation_group_legal/delete`,
      actor: 'dana',
      room_id: ROOM_ID,
      record_id: null,
      collection: null,
      summary: 'deleted 4 record(s)',
      counts: 4,
    },
    {
      seq: 7,
      ts: '2026-10-01T09:29:58.000+00:00',
      action: 'insert',
      source: `POST ${BASE}/rooms/${ROOM_ID}/links`,
      actor: 'dana',
      room_id: ROOM_ID,
      record_id: 'revocation_link_one',
      collection: 'revocation_link',
      summary: 'created revocation_link revocation_link_one',
      counts: null,
    },
  ],
}

export const REVOKE_RESULT = {
  link_id: 'revocation_link_one',
  room_id: ROOM_ID,
  revoked: true,
  revoked_at: '2026-10-01T09:30:00.000+00:00',
  revoked_by: 'sam',
  reason: 'Deal went cold.',
  via: 'revoked',
  row_kept: true,
  original_slug: 'northwind-overview',
  slug: 'revoked-northwind-overview-1a2b3c4d',
  slug_released: true,
  slug_available_now: true,
  grace_period_minutes: 0,
  cached_copy_recall: 'none',
  reversible: false,
  retained: {
    id: 'revocation_link_one',
    collection: 'revocation_link',
    room_id: ROOM_ID,
    revision: 3,
    created_at: '2026-10-01T09:00:00.000+00:00',
    updated_at: '2026-10-01T09:30:00.000+00:00',
    deleted_at: '2026-10-01T09:30:00.000+00:00',
    live: false,
    data: {
      slug: 'revoked-northwind-overview-1a2b3c4d',
      original_slug: 'northwind-overview',
      custom_domain: true,
      revoked: true,
      revoked_at: '2026-10-01T09:30:00.000+00:00',
      revoked_by: 'sam',
      revoked_via: 'revoked',
      revocation_reason: 'Deal went cold.',
      grace_period_minutes: 0,
      cached_copy_recall: 'none',
      reversible: false,
    },
    audit: [
      {
        ts: '2026-10-01T09:00:00.000+00:00',
        action: 'insert',
        source: `POST ${BASE}/rooms/${ROOM_ID}/links`,
        actor: 'dana',
        summary: 'created revocation_link revocation_link_one',
        diff: null,
      },
      {
        ts: '2026-10-01T09:30:00.000+00:00',
        action: 'update',
        source: `POST ${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`,
        actor: 'sam',
        summary: 'updated revocation_link revocation_link_one',
        diff: { revoked: { from: false, to: true } },
      },
      {
        ts: '2026-10-01T09:30:00.000+00:00',
        action: 'delete',
        source: `POST ${BASE}/rooms/${ROOM_ID}/links/revocation_link_one/revoke`,
        actor: 'sam',
        summary: 'deleted revocation_link revocation_link_one',
        diff: null,
      },
    ],
  },
}

export function resolve(overrides = {}) {
  return {
    room_id: ROOM_ID,
    slug: 'northwind-overview',
    viewer: null,
    resolves: true,
    reason: 'ok',
    link_id: 'revocation_link_one',
    target: 'room',
    group_id: null,
    checked_at: '2026-10-01T09:30:00.000+00:00',
    grace_period_minutes: 0,
    cached_copy_recall: 'none',
    ...overrides,
  }
}

/** A route table for the happy path: every read this feature performs. */
export function routes(overrides = {}) {
  return {
    '/records/room': ROOMS,
    [`${BASE}/vocabulary`]: VOCABULARY,
    [`${BASE}/inferences`]: INFERENCES,
    [`${BASE}/summary`]: SUMMARY,
    [`${BASE}/rooms/${ROOM_ID}/state`]: STATE,
    [`${BASE}/rooms/${ROOM_ID}/links`]: LINKS,
    [`${BASE}/rooms/${ROOM_ID}/groups`]: GROUPS,
    [`${BASE}/rooms/${ROOM_ID}/viewers`]: VIEWERS,
    [`${BASE}/rooms/${ROOM_ID}/documents`]: DOCUMENTS,
    [`${BASE}/rooms/${ROOM_ID}/trail`]: TRAIL,
    ...overrides,
  }
}