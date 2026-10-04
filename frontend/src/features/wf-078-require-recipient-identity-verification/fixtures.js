/**
 * Fixtures for the WF-078 frontend tests.
 *
 * One place decides what this feature's API looks like, so a test and the stub it runs
 * against cannot drift. Every shape here is copied from a real response of the routes in
 * `backend/dsr/features/wf078_require_recipient_identity_verification.py`, including the
 * four fields every response carries - `limitation`, `not_proof`, `authentication_owner`
 * and `not_sourced`. A fixture that dropped them would let a page render without the caveat
 * and the test would still pass, which is the one thing this workflow's tests exist to
 * prevent.
 */

const LIMITATION =
  'Verification raises the cost of opening a document under the wrong identity. It does not prove who is holding the phone, and the knowledge-based and ID checks compare what a recipient typed against what a sender recorded. Neither one is a background check.'

const NOT_PROOF =
  'A pass records that the right answer was given, not that the person who gave it is who the sender meant.'

const AUTHENTICATION_OWNER =
  'This room owns the authentication decision. The vendor’s own guidance is that you are solely responsible for making sure that your signer or end-user authentication process is sufficient and complies with any and all applicable laws and regulations.'

const NOT_SOURCED =
  'Three parts of this workflow are assumptions, not sourced facts.'

export const VOCABULARY = {
  places: [
    {
      id: 'before_open',
      label: 'Before open',
      description: 'Before the recipient can view the document',
      audience: 'all_recipients',
      required_role: null,
    },
    {
      id: 'before_sign',
      label: 'Before sign',
      description: 'Before the recipient can sign',
      audience: 'signers_only',
      required_role: 'signer',
    },
  ],
  methods: [
    {
      id: 'passcode',
      label: 'Typed passcode',
      description: 'The recipient types a passcode the sender chose.',
      vendor_field: 'passcode_verification',
      config_key: 'passcode',
      needs_delivery: false,
    },
    {
      id: 'sms',
      label: 'SMS one-time password',
      description: 'The recipient types a one-time code the room sent to their phone.',
      vendor_field: 'phone_verification',
      config_key: 'phone_number',
      needs_delivery: true,
    },
    {
      id: 'kba',
      label: 'Knowledge-based authentication',
      description: 'The recipient answers identity questions the sender recorded.',
      vendor_field: 'kba_verification',
      config_key: 'questions',
      needs_delivery: false,
    },
    {
      id: 'id',
      label: 'Government-issued ID check',
      description: 'The recipient states the details of a government-issued ID.',
      vendor_field: 'id_verification',
      config_key: 'id_document',
      needs_delivery: false,
    },
  ],
  roles: [
    { id: 'signer', label: 'Signer' },
    { id: 'recipient', label: 'Recipient' },
  ],
  passcode: {
    min_length: 6,
    max_length: 100,
    requires_letter: true,
    requires_digit: true,
    rule: 'A passcode must be 6 to 100 characters, with at least one letter and at least one digit.',
  },
  phone: {
    prefix: '+',
    example: '+1555667890',
    min_digits: 8,
    max_digits: 15,
    rule: 'A phone number must be in international format, written as a plus and the country code, for example +1555667890.',
  },
  sms: {
    types: [
      { id: 'authentication', meaning: 'The number carries the one-time code and nothing else.' },
      { id: 'delivery', meaning: 'The number receives the document and is not an authentication factor.' },
      { id: 'both', meaning: 'The number receives the document and carries the one-time code.' },
    ],
    field: 'sms_phone_number_type',
    code_digits: 6,
    resend_allowed: true,
  },
  kba: { min_questions: 1, max_questions: 10, fields: ['prompt', 'answer'] },
  id_check: {
    fields: ['document_type', 'document_number', 'issuing_country'],
    document_types: ['passport', 'national_id', 'drivers_licence', 'residence_permit'],
  },
  outcomes: ['pass', 'fail'],
  failure_reasons: [
    'no_verification_configured',
    'passcode_mismatch',
    'passcode_not_provable',
    'no_code_sent',
    'code_mismatch',
    'answers_incomplete',
    'answer_mismatch',
    'id_details_mismatch',
    'recipient_is_not_a_signer',
  ],
  body_states: ['withheld', 'released'],
  collections: [
    'wf078_verified_document',
    'wf078_verified_recipient',
    'wf078_verification_attempt',
    'wf078_verification_session',
    'wf078_sms_code',
  ],
  code_bands: { verification: [47, 54], email_otp: [69, 70] },
  code_table_owner: 'dsr.audit_export.vocabulary.verification_code',
  code_owner_note: 'The integer action codes for a verification outcome are not defined here.',
  decisions: { count: 11, ids: ['OWNERSHIP_WF078_OWNS_THE_GATE'] },
  assumptions_recorded: [
    'ASSUMED_KBA_PUBLIC_RECORD_SOURCE',
    'ASSUMED_ID_VERIFICATION_PROVIDER',
    'ASSUMED_RECIPIENT_SETTINGS_PANEL',
  ],
  authentication_owner: AUTHENTICATION_OWNER,
  not_sourced: NOT_SOURCED,
  limitation: LIMITATION,
  not_proof: NOT_PROOF,
}

/**
 * The recipient the specification's extensibility note is about: one object, verified
 * differently for viewing and for signing. Carrying two gates is the sentence made
 * visible, so the fixture has to carry two.
 */
export const TWO_AXIS_RECIPIENT = {
  id: 'wf078_verified_recipient_aaaa',
  room_id: 'room_a',
  document_id: 'wf078_verified_document_aaaa',
  email: 'procurement@vantage.example',
  name: 'Alex Doyle',
  role: 'signer',
  created_at: '2026-10-04T09:00:00.000+00:00',
  updated_at: '2026-10-04T09:00:00.000+00:00',
  revision: 1,
  gate_count: 2,
  gates: [
    {
      place: 'before_open',
      label: 'Before open',
      description: 'Before the recipient can view the document',
      audience: 'all_recipients',
      method: 'passcode',
      method_label: 'Typed passcode',
      vendor_field: 'passcode_verification',
      sms_type: null,
      sms_type_meaning: null,
      needs_delivery: false,
    },
    {
      place: 'before_sign',
      label: 'Before sign',
      description: 'Before the recipient can sign',
      audience: 'signers_only',
      method: 'kba',
      method_label: 'Knowledge-based authentication',
      vendor_field: 'kba_verification',
      sms_type: null,
      sms_type_meaning: null,
      needs_delivery: false,
    },
  ],
  gate_names: ['before_open', 'before_sign'],
  authentication_factor_gates: [],
  last_attempts: [],
  limitation: LIMITATION,
  not_proof: NOT_PROOF,
  authentication_owner: AUTHENTICATION_OWNER,
  not_sourced: NOT_SOURCED,
}

/** An SMS recipient whose number delivers the document and is not an authentication factor. */
export const DELIVERY_ONLY_RECIPIENT = {
  ...TWO_AXIS_RECIPIENT,
  id: 'wf078_verified_recipient_bbbb',
  email: 'ap@kestrel.example',
  name: 'Robin Hale',
  gate_count: 1,
  gates: [
    {
      place: 'before_sign',
      label: 'Before sign',
      description: 'Before the recipient can sign',
      audience: 'signers_only',
      method: 'sms',
      method_label: 'SMS one-time password',
      vendor_field: 'phone_verification',
      sms_type: 'delivery',
      sms_type_meaning: 'The number receives the document and is not an authentication factor.',
      needs_delivery: true,
    },
  ],
  gate_names: ['before_sign'],
  authentication_factor_gates: [],
}

/** The ordinary case. Most recipients in a room carry no verification at all. */
export const UNGATED_RECIPIENT = {
  ...TWO_AXIS_RECIPIENT,
  id: 'wf078_verified_recipient_cccc',
  email: 'observer@vantage.example',
  name: 'Sam Ito',
  role: 'recipient',
  gate_count: 0,
  gates: [],
  gate_names: [],
}

export const SUMMARY = {
  documents: 4,
  recipients: 5,
  gated_recipients: 4,
  ungated_recipients: 1,
  before_open_recipients: 3,
  before_sign_recipients: 2,
  two_axis_recipients: 1,
  delivery_only_sms_recipients: 1,
  signers: 3,
  attempts: 5,
  attempts_passed: 3,
  attempts_failed: 2,
  recipients_awaiting_verification: 2,
  by_method: { passcode: 2, kba: 2, sms: 1 },
  by_outcome: { pass: 3, fail: 2 },
  by_place: { before_open: 4, before_sign: 1 },
  by_reason: { answer_mismatch: 1, passcode_mismatch: 1 },
  authentication_owner: AUTHENTICATION_OWNER,
  not_sourced: NOT_SOURCED,
  limitation: LIMITATION,
  not_proof: NOT_PROOF,
}

export const ATTEMPTS = {
  count: 3,
  code_table_owner: VOCABULARY.code_table_owner,
  attempts: [
    {
      id: 'wf078_verification_attempt_1',
      room_id: 'room_a',
      recipient_id: TWO_AXIS_RECIPIENT.id,
      document_id: TWO_AXIS_RECIPIENT.document_id,
      place: 'before_open',
      method: 'passcode',
      outcome: 'pass',
      reason: null,
      sms_type: null,
      event_code: 48,
      gate_effect: 'gate_cleared',
      at: '2026-10-04T09:01:00.000+00:00',
      revision: 1,
      not_proof: NOT_PROOF,
      limitation: LIMITATION,
    },
    {
      id: 'wf078_verification_attempt_2',
      room_id: 'room_a',
      recipient_id: DELIVERY_ONLY_RECIPIENT.id,
      document_id: DELIVERY_ONLY_RECIPIENT.document_id,
      place: 'before_open',
      method: 'kba',
      outcome: 'fail',
      reason: 'answer_mismatch',
      sms_type: null,
      event_code: 51,
      gate_effect: 'gate_not_cleared',
      at: '2026-10-04T09:02:00.000+00:00',
      revision: 1,
      not_proof: NOT_PROOF,
      limitation: LIMITATION,
    },
    {
      id: 'wf078_verification_attempt_3',
      room_id: 'room_b',
      recipient_id: DELIVERY_ONLY_RECIPIENT.id,
      document_id: DELIVERY_ONLY_RECIPIENT.document_id,
      place: 'before_sign',
      method: 'sms',
      outcome: 'fail',
      reason: 'no_code_sent',
      sms_type: 'delivery',
      event_code: 53,
      gate_effect: 'gate_not_cleared',
      at: '2026-10-04T09:03:00.000+00:00',
      revision: 1,
      not_proof: NOT_PROOF,
      limitation: LIMITATION,
    },
  ],
  limitation: LIMITATION,
  not_proof: NOT_PROOF,
}

export const DECISIONS = {
  count: 2,
  decisions: [
    {
      id: 'OWNERSHIP_WF078_OWNS_THE_GATE',
      question: 'The e-signature workflow and this workflow both describe a recipient identity verification object. Which one owns the setting?',
      chosen: 'wf078_owns_gate',
      rejected_because: 'WF-095 is not implemented in this repository, so a design that hands it the gate cannot be tested.',
      jev_audit_id: 'jev-20261004T140557-22596-57651',
    },
    {
      id: 'TWO_AXES_NOT_ONE',
      question: "Is a recipient's verification one method at one moment, or a method per moment?",
      chosen: 'gate_keyed_mapping',
      rejected_because: 'One method and one moment cannot express "verified differently for viewing and signing".',
    },
  ],
}

export const ASSUMPTIONS = {
  count: 3,
  assumptions: [
    {
      id: 'ASSUMED_KBA_PUBLIC_RECORD_SOURCE',
      question: 'Where do the identity questions come from?',
      chosen: 'sender_recorded',
      rejected_because: 'No vendor is named in any cited source.',
    },
    {
      id: 'ASSUMED_ID_VERIFICATION_PROVIDER',
      question: 'What checks a government-issued ID here?',
      chosen: 'compare_recorded_details',
      rejected_because: 'A provider was rejected because none is named in any cited source.',
    },
    {
      id: 'ASSUMED_RECIPIENT_SETTINGS_PANEL',
      question: 'What does the sender-side panel look like?',
      chosen: 'two_independent_pickers',
      rejected_because: 'The specification describes the method and the moment as two independent axes.',
    },
  ],
  not_sourced: NOT_SOURCED,
}

export const ROOMS = {
  records: [
    { id: 'room_a', data: { name: 'Northwind data room' } },
    { id: 'room_b', data: { name: 'Halcyon data room' } },
  ],
}

export const PROMPT = {
  recipient_id: TWO_AXIS_RECIPIENT.id,
  place: 'before_open',
  method: 'passcode',
  method_label: 'Typed passcode',
  description: 'The recipient types a passcode the sender chose.',
  asks: 'passcode',
  min_length: 6,
  max_length: 100,
  not_proof: NOT_PROOF,
  limitation: LIMITATION,
}

/**
 * The stub router. Keyed on this feature's own prefix plus the one core route the room
 * picker reads, so the page cannot accidentally satisfy a request from a different
 * feature's fixture.
 */
export function routes(url, options = {}) {
  const path = String(url).split('?')[0]
  const method = options.method || 'GET'

  if (path === '/api/wf-078/vocabulary') return { status: 200, body: VOCABULARY }
  if (path === '/api/wf-078/summary') return { status: 200, body: SUMMARY }
  if (path === '/api/wf-078/decisions') return { status: 200, body: DECISIONS }
  if (path === '/api/wf-078/assumptions') return { status: 200, body: ASSUMPTIONS }
  if (path === '/api/wf-078/recipients') {
    return {
      status: 200,
      body: {
        count: 3,
        recipients: [TWO_AXIS_RECIPIENT, DELIVERY_ONLY_RECIPIENT, UNGATED_RECIPIENT],
        limitation: LIMITATION,
        not_sourced: NOT_SOURCED,
      },
    }
  }
  if (path === '/api/wf-078/attempts') return { status: 200, body: ATTEMPTS }
  if (path === '/api/records/room') return { status: 200, body: ROOMS }

  if (/^\/api\/wf-078\/recipients\/[^/]+\/prompt$/.test(path)) return { status: 200, body: PROMPT }
  if (/^\/api\/wf-078\/recipients\/[^/]+\/codes$/.test(path) && method === 'POST') {
    return {
      status: 201,
      body: {
        recipient_id: DELIVERY_ONLY_RECIPIENT.id,
        place: 'before_sign',
        digits: 6,
        sms_type: 'delivery',
        sms_type_meaning: 'The number receives the document and is not an authentication factor.',
        is_authentication_factor: false,
        superseded_previous: true,
        issued_at: '2026-10-04T09:04:00.000+00:00',
        code_returned: false,
        limitation: LIMITATION,
      },
    }
  }
  if (/^\/api\/wf-078\/recipients\/[^/]+\/attempts$/.test(path) && method === 'POST') {
    const body = JSON.parse(options.body || '{}')
    const passed = body.passcode === 'Deal2026'
    return {
      status: 201,
      body: {
        attempt_id: 'wf078_verification_attempt_new',
        recipient_id: TWO_AXIS_RECIPIENT.id,
        document_id: TWO_AXIS_RECIPIENT.document_id,
        place: 'before_open',
        method: 'passcode',
        method_label: 'Typed passcode',
        outcome: passed ? 'pass' : 'fail',
        reason: passed ? null : 'passcode_mismatch',
        sms_type: null,
        event_code: passed ? 48 : 52,
        gate_effect: passed ? 'gate_cleared' : 'gate_not_cleared',
        at: '2026-10-04T09:05:00.000+00:00',
        gated: true,
        reasserted: true,
        not_proof: NOT_PROOF,
        limitation: LIMITATION,
      },
    }
  }

  return { status: 404, body: { detail: `no fixture for ${method} ${path}` } }
}
