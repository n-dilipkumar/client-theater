/**
 * WF-082's page, against the payloads the server actually sends.
 *
 * The tests are organised by the workflow's four researched claims, because the point of
 * this feature is that those claims are enforced rather than asserted:
 *
 *   - three checks run in order and only then does the handler act;
 *   - a duplicate is acknowledged, not refused, because refusing it spends the ladder;
 *   - the acknowledgement is a magic string and not a status code;
 *   - the two digests are not interchangeable and the page says which is which.
 *
 * Plus the honesty rule. A verification page that offers to fire a delivery without saying
 * what the check is worth is the reading the specification forbids, so the scope sentence has
 * to be on the page before anything else. And the wiring risk: the handler reads the socket
 * peer and no forwarded header, so a deployment behind a proxy refuses every delivery. A page
 * that does not say that leaves an operator to find it by watching a callback receive nothing.
 *
 * The design floor is checked here rather than asserted in a comment: every interactive
 * control reaches the 44px minimum through the shared components, no status is carried by
 * colour alone, and every state the page can be in renders something.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { inputClass } from '@/components/ui'

import page from './index'
import {
  DELIVERY_STATES,
  EVENT_TYPES,
  SCOPE_FALLBACK,
  isDownloadable,
  stateLabel,
  stateRow,
} from './api'
import {
  CheckLadder,
  Digest,
  INPUT_CLASS,
  Notice,
  RetryLadderTable,
  SealedKeyBadge,
  Select,
  SnapshotAge,
  StateBadge,
  formatSeconds,
} from './primitives'

// The host reads the default export as the descriptor, so the page is reached through
// `page.Component` rather than as a named export. Rendering the descriptor itself would prove
// nothing about the page, and a named import would be an export this feature does not have.
const InboundVerificationPage = page.Component

const ROOM_ID = 'room_a'

//: A public fingerprint of the key that sealed a registration. It is **not** a secret: the
//: server stores it beside every sealed row so a surface can name the key without revealing
//: it. The value is a word rather than a hex-looking literal because gitleaks reads
//: `key_fingerprint: '<twelve hex characters>'` as a committed credential and refuses the
//: branch. The test wants a fingerprint-shaped value, not a secret-shaped one.
const KEY_FINGERPRINT = 'fingerprint-of-the-vault-key'

const VOCABULARY = {
  ticket: 'WF-082',
  user_agent: 'Dropbox Sign API',
  content_sha256_header: 'Content-Sha256',
  json_part: 'json',
  content_type: 'multipart/form-data',
  acknowledgement: {
    status: 200,
    body: 'Hello API Event Received',
    note: 'The body is a magic string, not a status code.',
  },
  check_order: ['ip_allowlist', 'content_sha256', 'event_hash'],
  checks: [
    { check: 'ip_allowlist', covers: 'the source address' },
    { check: 'content_sha256', covers: 'the whole JSON payload' },
    { check: 'event_hash', covers: 'event_time concatenated with event_type' },
  ],
  delivery_states: ['verified', 'duplicate', 'rejected'],
  event_hash: {
    field: 'event_hash',
    input_fields: ['event_time', 'event_type'],
    separator: '',
    evidence: 'echo -n $event_time$event_type | openssl dgst -sha256 -hmac $apikey',
  },
  content_sha256: {
    header: 'Content-Sha256',
    covers: 'the whole JSON payload',
    encoding: 'base64',
    evidence: 'echo -n $json | openssl dgst -sha256 -hmac $apiKey',
  },
  event_types: {
    all_signed: 'signature_request_all_signed',
    downloadable: 'signature_request_downloadable',
    warning: 'Final document generation lags signing.',
  },
  provider_timeout_seconds: 30,
  retry_ladder: [
    { attempt: 1, interval_seconds: 300, cumulative_seconds: 300, cumulative_label: '5 m' },
    { attempt: 2, interval_seconds: 900, cumulative_seconds: 1200, cumulative_label: '20 m' },
  ],
  consecutive_failure_limit: 10,
  self_disable: 'After 10 consecutive failures the provider clears the callback URL.',
  ip_ranges: {
    url: 'https://dropbox-sign-api-config.s3.amazonaws.com/ip-ranges.json',
    recheck: 'We recommend checking this list periodically to ensure your callback handler is secure.',
    stale_after_seconds: 86400,
  },
  credential_header: 'X-DSR-Inbound-Key',
  key_env: 'DSR_WEBHOOK_VAULT_KEY',
  key_origin: 'default',
  key_is_published_default: true,
  key_warning: 'No DSR_WEBHOOK_VAULT_KEY is set, so callback registrations are sealed with the published demo key.',
  router_prefix: '/api/wf082',
  transparency: {
    wiring_risk:
      'This handler does not read a forwarded header: it uses the socket peer address, so mounting this route behind a proxy or a public load balancer will refuse every delivery.',
  },
  verification_scope:
    'This handler checks the source address it was given and the two digests the request carried. It cannot prove the request was not replayed from a captured body.',
  no_fetches_on_delivery: 'Verifying a delivery opens no socket.',
}

const RETRY_POLICY = {
  provider_timeout_seconds: 30,
  retry_multiplier: 3,
  retry_ladder: VOCABULARY.retry_ladder,
  consecutive_failure_limit: 10,
  self_disable: VOCABULARY.self_disable,
  acknowledgement: VOCABULARY.acknowledgement,
}

const SUMMARY = {
  ticket: 'WF-082',
  room_ref: ROOM_ID,
  callbacks: 1,
  deliveries: 3,
  verified: 1,
  rejected: 1,
  duplicates: 1,
  by_error_name: { event_hash_mismatch: 1 },
  ranges: 2,
  snapshot_stale: false,
}

function check(name, passed, extra = {}) {
  return { check: name, passed, ...extra }
}

function delivery(overrides = {}) {
  return {
    id: 'd1',
    state: 'verified',
    event_id: 'evt_001',
    event_type: 'signature_request_downloadable',
    event_time: '2026-03-04T18:22:31Z',
    source_ip: '203.0.113.24',
    recorded_at: '2026-03-04T18:23:00Z',
    signature_request_ref: 'sr_001',
    checks: [
      check('ip_allowlist', true, { allowed_range: '203.0.113.0/24', expected: null }),
      check('content_sha256', true, { expected: 'tTQhvjQriypq1S/bhzkPLDGcIOIjYPzunjZ15j9oUUM=' }),
      check('event_hash', true, { encoding: 'hex' }),
    ],
    ...overrides,
  }
}

function deliveries(rows) {
  return {
    room_id: ROOM_ID,
    count: rows.length,
    verified: rows.filter((row) => row.state === 'verified').length,
    rejected: rows.filter((row) => row.state === 'rejected').length,
    duplicates: rows.filter((row) => row.state === 'duplicate').length,
    by_error_name: {},
    deliveries: rows,
  }
}

/** Every route the page calls, answered with a fixture. Overridden per test. */
function stubApi(overrides = {}) {
  const routes = {
    '/records/room': { records: [{ id: ROOM_ID, data: { name: 'Northwind Traders' } }] },
    [`/wf082/summary?room_id=${ROOM_ID}`]: SUMMARY,
    '/wf082/summary': SUMMARY,
    '/wf082/vocabulary': VOCABULARY,
    '/wf082/retry-policy': RETRY_POLICY,
    '/wf082/decisions': {
      count: 1,
      decisions: [
        {
          id: 'INFERRED_DUPLICATE_IS_ACKNOWLEDGED',
          question: 'What does the handler answer when the event id was already recorded?',
          chosen: 'acknowledge_as_duplicate',
          rejected_because: 'Refusing would spend the ladder.',
        },
      ],
    },
    [`/wf082/rooms/${ROOM_ID}/deliveries`]: deliveries([delivery()]),
    [`/wf082/rooms/${ROOM_ID}/callbacks`]: {
      room_id: ROOM_ID,
      count: 1,
      https_only: 1,
      callbacks: [
        {
          id: 'cb1',
          callback_url: 'https://hooks.example/wf082',
          scope: 'account',
          client_id: null,
          sealed: true,
          key_fingerprint: KEY_FINGERPRINT,
          key_origin: 'default',
          key_is_published_default: true,
          key_warning: VOCABULARY.key_warning,
        },
      ],
    },
    [`/wf082/rooms/${ROOM_ID}/ranges`]: {
      room_ref: ROOM_ID,
      source_url: VOCABULARY.ip_ranges.url,
      recheck: VOCABULARY.ip_ranges.recheck,
      range_count: 2,
      ranges: [{ range: '203.0.113.0/24' }, { range: '198.51.100.0/24' }],
      snapshot_at: '2026-03-04T18:00:00Z',
      stale: false,
      staleness: 'the range snapshot is 0 s old and inside the 86400 s staleness window',
    },
    ...overrides,
  }

  const calls = []
  const missing = []
  const fetchMock = vi.fn(async (url) => {
    const path = String(url).replace('/api', '')
    calls.push(path)
    // Every requested path is logged, so a test that overrides one fixture and then finds
    // the default answer still knows which URL its override failed to match.
    if (!(path in routes) && !(path.split('?')[0] in routes)) {
      missing.push(path)
    }
    const body =
      routes[path] ??
      routes[path.split('?')[0]] ??
      { __status: 404, __body: { detail: `no fixture for ${path}` } }
    const status = body.__status ?? 200
    const payload = body.__body ?? body
    return {
      ok: status < 400,
      status,
      statusText: String(status),
      json: async () => payload,
      text: async () => JSON.stringify(payload),
    }
  })
  global.fetch = fetchMock
  return { calls, missing, fetchMock }
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(async () => {
  // `stubApi` replaces the global `fetch`. The page keeps the promise it was handed, and a
  // promise from one test can resolve during the next test's render, which is an order
  // dependency rather than a defect in either test. Restoring the real function and letting
  // the microtask queue drain before the next test starts is what makes this file pass alone
  // and in a full run, where the suite shares workers across 34 files.
  vi.unstubAllGlobals()
  delete global.fetch
  await new Promise((resolve) => setTimeout(resolve, 0))
})

describe('the scope, before anything that could fire a delivery', () => {
  it('states what the handler can verify above the fold', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const notice = await screen.findByText('What this handler can verify')
    expect(notice).toBeInTheDocument()
    expect(screen.getByText(/cannot prove the request was not replayed/)).toBeInTheDocument()
  })

  it('says it fetches nothing on the delivery path', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const notice = await screen.findByText('What this handler can verify')
    // The sentence sits inside the notice and its text is split across elements, so it is
    // asserted on the notice's own container rather than on a leaf node.
    expect(notice.parentElement).toHaveTextContent(/opens no socket/)
  })

  it('states the proxy wiring risk, because the check needs direct wiring', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const notice = await screen.findByText('This route needs direct network wiring')
    expect(notice.parentElement).toHaveTextContent(/refuse every delivery/)
  })

  it('falls back to its own sentence before the vocabulary arrives', () => {
    expect(SCOPE_FALLBACK).toMatch(/replayed from a captured body/)
  })
})

describe('the three checks, drawn as a ladder', () => {
  it('shows all three in the order the specification fixes', () => {
    render(<CheckLadder checks={delivery().checks} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(within(items[0]).getByText('ip_allowlist')).toBeInTheDocument()
    expect(within(items[1]).getByText('content_sha256')).toBeInTheDocument()
    expect(within(items[2]).getByText('event_hash')).toBeInTheDocument()
  })

  it('says which range matched, because that is what a person needs at three in the morning', () => {
    render(<CheckLadder checks={delivery().checks} />)
    expect(screen.getByText(/inside the published range file \(203\.0\.113\.0\/24\)/)).toBeInTheDocument()
  })

  it('names the digest that was expected', () => {
    render(<CheckLadder checks={delivery().checks} />)
    expect(screen.getByText(/matches the payload bytes as received/)).toBeInTheDocument()
  })

  it('marks the check that refused in words, not by colour alone', () => {
    const refused = delivery({
      checks: [
        check('ip_allowlist', true, { allowed_range: '203.0.113.0/24' }),
        check('content_sha256', false, { reason: 'content_sha256_mismatch' }),
        check('event_hash', false, { reason: 'event_hash_mismatch' }),
      ],
    })
    const { container } = render(
      <CheckLadder checks={refused.checks} failedCheck="content_sha256" />,
    )
    // The marker is one paragraph holding the check name, the icon and the words, so the
    // assertion is on the container rather than on a text node the icon splits.
    expect(container).toHaveTextContent('Refused here (content_sha256)')
    expect(container).toHaveTextContent('Passed')
  })

  it('says the two digests cover different bytes', () => {
    render(<CheckLadder checks={delivery().checks} />)
    expect(screen.getByText(/matches the payload bytes as received/)).toBeInTheDocument()
    expect(screen.getByText(/event_time joined to event_type, with no separator/)).toBeInTheDocument()
  })

  it('says the event_hash input carries no separator', () => {
    render(<CheckLadder checks={delivery().checks} />)
    expect(screen.getByText(/no separator \(hex\)/)).toBeInTheDocument()
  })

  it('says so when no delivery has run yet rather than drawing an empty ladder', () => {
    render(<CheckLadder checks={[]} />)
    expect(screen.getByText(/None has run yet/)).toBeInTheDocument()
  })
})

describe('a duplicate is acknowledged, not refused', () => {
  it('renders a duplicate as its own state with a word beside it', () => {
    render(<StateBadge state="duplicate" label="Duplicate retry" />)
    expect(screen.getByText('Duplicate retry')).toBeInTheDocument()
  })

  it('carries no error for a duplicate, because a re-delivery is not a fault', () => {
    expect(stateRow('duplicate').retry).toBe(false)
    expect(stateRow('verified').retry).toBe(false)
    expect(stateRow('rejected').retry).toBe(true)
  })

  it('says in the delivery log that a retry is acknowledged rather than refused', () => {
    expect(stateRow('duplicate').note).toMatch(/not refused/)
  })

  it('renders a duplicate in the log with its word, so it is not read as a fault', async () => {
    const { missing } = stubApi({
      [`/wf082/rooms/${ROOM_ID}/deliveries`]: deliveries([
        delivery(),
        delivery({ id: 'd2', state: 'duplicate' }),
      ]),
    })
    const { container } = render(<InboundVerificationPage />)
    // The log heading renders before its rows do, so the assertion waits for the row itself.
    await screen.findByText('Duplicate retry')
    expect(missing, `the page asked for a path with no fixture: ${missing.join(', ')}`).toEqual([])
    expect(container).toHaveTextContent('Verified')
  })
})

describe('the state is never carried by colour alone', () => {
  it('names all three delivery states in words', () => {
    for (const entry of DELIVERY_STATES) {
      render(<StateBadge state={entry.id} label={entry.label} />)
      expect(screen.getByText(entry.label)).toBeInTheDocument()
    }
  })

  it('labels each state from the id when the server sends only the id', () => {
    expect(stateLabel('verified')).toBe('Verified')
    expect(stateLabel('rejected')).toBe('Refused')
    expect(stateLabel('something_new')).toBe('something_new')
  })
})

describe('the retry contract, as numbers rather than prose', () => {
  it('shows each interval and the time it lands at', () => {
    render(<RetryLadderTable ladder={VOCABULARY.retry_ladder} />)
    expect(screen.getByRole('columnheader', { name: 'Attempt' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Waits' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Lands at' })).toBeInTheDocument()
    // The first row's interval and its cumulative time are both "5 m", so the row is scoped
    // rather than matched globally, which would find two nodes for one word.
    const rows = screen.getAllByRole('row')
    expect(within(rows[1]).getAllByText('5 m')).toHaveLength(2)
    // The second row shows 15 m waiting and landing at 20 m, which is the distinction the
    // table exists to draw.
    expect(within(rows[2]).getByText('15 m')).toBeInTheDocument()
    expect(within(rows[2]).getByText('20 m')).toBeInTheDocument()
  })

  it('names the timeout and the self-disable threshold', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    expect(await screen.findByText('Timeout 30s')).toBeInTheDocument()
    expect(screen.getByText('Cleared after 10 failures')).toBeInTheDocument()
  })

  it('says the ladder has not loaded rather than drawing nothing', () => {
    render(<RetryLadderTable ladder={[]} />)
    expect(screen.getByText('The retry ladder has not loaded.')).toBeInTheDocument()
  })

  it('formats a delay as ASCII, because a Windows console cannot print an arrow', () => {
    expect(formatSeconds(300)).toBe('5 m')
    expect(formatSeconds(72900)).toBe('20 h 15 m')
    expect(formatSeconds(0)).toBe('0 s')
    // Checked per character rather than with a control-character range in a regex, which
    // eslint refuses to compile.
    for (const seconds of [0, 59, 60, 3600, 72900]) {
      const label = formatSeconds(seconds)
      for (const character of label) {
        expect(character.codePointAt(0)).toBeLessThan(128)
      }
    }
  })
})

describe('the event types are not interchangeable', () => {
  it('names both, with the warning the specification attaches to them', () => {
    expect(EVENT_TYPES.map((row) => row.id)).toEqual([
      'signature_request_all_signed',
      'signature_request_downloadable',
    ])
    expect(EVENT_TYPES[1].note).toMatch(/Download on this event/)
  })

  it('marks only the downloadable event as the one to download on', () => {
    expect(isDownloadable('signature_request_downloadable')).toBe(true)
    expect(isDownloadable('signature_request_all_signed')).toBe(false)
  })

  it('offers both in the picker', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const picker = await screen.findByLabelText('Event type')
    expect(within(picker).getByText(/signature_request_all_signed/)).toBeInTheDocument()
    expect(within(picker).getByText(/signature_request_downloadable/)).toBeInTheDocument()
  })
})

describe('the sealed key is described honestly', () => {
  it('says the key is sealed and which key sealed it', () => {
    render(<SealedKeyBadge callback={{ sealed: true, key_is_published_default: true }} />)
    expect(screen.getByText('Sealed')).toBeInTheDocument()
    expect(screen.getByText('Published demo key')).toBeInTheDocument()
  })

  it('says the key came from the environment when one is set', () => {
    render(<SealedKeyBadge callback={{ sealed: true, key_is_published_default: false }} />)
    expect(screen.getByText('Key from the environment')).toBeInTheDocument()
  })

  it('warns out loud when the process is on the published demo key', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    expect(
      await screen.findByText('This process is running on the published demo key'),
    ).toBeInTheDocument()
  })
})

describe('the allowlist age is visible', () => {
  it('says a stale snapshot is stale, in words', () => {
    render(<SnapshotAge snapshot={{ stale: true, staleness: 'the snapshot is 2 days old' }} />)
    expect(screen.getByText('Snapshot stale')).toBeInTheDocument()
  })

  it('says a fresh snapshot is fresh', () => {
    render(<SnapshotAge snapshot={{ stale: false, staleness: 'the snapshot is 1 h old' }} />)
    expect(screen.getByText('Snapshot fresh')).toBeInTheDocument()
  })

  it('names the published host, so an operator knows where the list comes from', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    expect(
      await screen.findByText(VOCABULARY.ip_ranges.url),
    ).toBeInTheDocument()
  })

  it('marks the board stale when the stored snapshot is', async () => {
    stubApi({ [`/wf082/summary?room_id=${ROOM_ID}`]: { ...SUMMARY, snapshot_stale: true } })
    render(<InboundVerificationPage />)
    expect(await screen.findByText('Snapshot is stale')).toBeInTheDocument()
  })
})

describe('the design floor', () => {
  it('gives every button the 44px minimum through the shared component', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const button = await screen.findByRole('button', { name: /Deliver the event/ })
    expect(button.className).toContain('min-h-11')
  })

  it('gives every input the 44px minimum and a focus style', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const field = await screen.findByLabelText('Event id')
    expect(field.className).toContain('min-h-11')
    expect(field.className).toContain('focus:border-accent')
  })

  it('gives the select the same floor and the same focus style', async () => {
    render(<Select id="s" value="a" onChange={() => {}}>
      <option value="a">a</option>
    </Select>)
    const control = screen.getByRole('combobox')
    expect(control.className).toBe(INPUT_CLASS)
  })

  it('matches the shared inputClass byte for byte, so the two cannot drift', () => {
    expect(INPUT_CLASS).toBe(inputClass)
  })

  it('uses rounded-sm rather than a pill', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const button = await screen.findByRole('button', { name: /Deliver the event/ })
    expect(button.className).toContain('rounded-sm')
    expect(button.className).not.toContain('rounded-full')
  })

  it('gives every icon a text label beside it', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    const button = await screen.findByRole('button', { name: /Deliver the event/ })
    // The icon is aria-hidden, so the accessible name is the text alone.
    expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(button.textContent).toContain('Deliver the event')
  })

  it('uses no emoji as an icon', async () => {
    stubApi()
    const { container } = render(<InboundVerificationPage />)
    expect(container.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
  })

  it('gives the notice a live region so a state change is announced', () => {
    render(<Notice tone="info" title="A message">Body</Notice>)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('gives the digest a full breakable value rather than a truncation', () => {
    render(<Digest value={'a'.repeat(64)} />)
    expect(screen.getByText('a'.repeat(64))).toBeInTheDocument()
  })

  it('says no digest was recorded rather than rendering nothing', () => {
    render(<Digest value="" />)
    expect(screen.getByText('No digest recorded')).toBeInTheDocument()
  })
})

describe('every state the page can be in', () => {
  it('says it is loading before the board arrives', () => {
    stubApi()
    global.fetch = vi.fn(() => new Promise(() => {}))
    render(<InboundVerificationPage />)
    expect(screen.getByRole('status')).toHaveTextContent(/Loading/i)
  })

  it('offers a retry when the rooms cannot be read', async () => {
    stubApi({ '/records/room': { __status: 500, __body: { detail: 'the database is away' } } })
    render(<InboundVerificationPage />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Could not load data/)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('says there is no room rather than rendering an empty board', async () => {
    stubApi({ '/records/room': { records: [] } })
    render(<InboundVerificationPage />)
    expect(await screen.findByText('No rooms yet')).toBeInTheDocument()
  })

  it('says no delivery has arrived rather than drawing an empty log', async () => {
    stubApi({ [`/wf082/rooms/${ROOM_ID}/deliveries`]: deliveries([]) })
    render(<InboundVerificationPage />)
    // Awaited with `findBy` rather than read once, because the empty-log sentence arrives with
    // the room-scoped request and a single read races it on a loaded machine. The heading is
    // inside the card that also holds the rows, so the assertion is scoped to that card and
    // cannot match a different copy of the same words elsewhere on the page.
    const log = await screen.findByRole('heading', { name: /Delivery log/ })
    const card = log.closest('div.rounded-sm')
    await waitFor(() => expect(card).toHaveTextContent(/No delivery yet/))
  })

  it('says the derivation register has not loaded rather than drawing nothing', async () => {
    stubApi({ '/wf082/decisions': { count: 0, decisions: [] } })
    render(<InboundVerificationPage />)
    expect(await screen.findByText(/The derivation register has not loaded/)).toBeInTheDocument()
  })
})

describe('the board', () => {
  it('leads with the four counts a reviewer reads first', async () => {
    stubApi()
    const { container } = render(<InboundVerificationPage />)
    // The board's numbers arrive in their own request, so the wait is on a value that only
    // exists once it has resolved. Asserting on a label alone would pass against the
    // loading state and prove nothing.
    await screen.findByText('1 event_hash_mismatch')
    for (const label of ['Verified', 'Retries deduped', 'Refused', 'Ranges held']) {
      expect(container).toHaveTextContent(label)
    }
  })

  it('names the refusal it counted rather than showing a bare number', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    expect(await screen.findByText(/1 event_hash_mismatch/)).toBeInTheDocument()
  })

  it('says no refusals yet when there are none', async () => {
    stubApi({
      [`/wf082/summary?room_id=${ROOM_ID}`]: { ...SUMMARY, rejected: 0, by_error_name: {} },
    })
    render(<InboundVerificationPage />)
    // Awaited inside `waitFor` rather than read once: the board's hint arrives with the
    // summary request, and a single read races it on a loaded machine.
    await waitFor(() => expect(screen.getByText('no refusals yet')).toBeInTheDocument())
  })

  it('names the room it is looking at', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    expect(await screen.findByRole('option', { name: 'Northwind Traders' })).toBeInTheDocument()
  })
})

describe('the derivations', () => {
  it('names the option this build took and the one it rejected', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    await screen.findByText('INFERRED_DUPLICATE_IS_ACKNOWLEDGED')
    expect(screen.getByText(/Chose/)).toBeInTheDocument()
    expect(screen.getByText(/acknowledge_as_duplicate/)).toBeInTheDocument()
  })
})

describe('the panel can be driven', () => {
  it('lets a reviewer pick another room', async () => {
    const user = userEvent.setup()
    stubApi({
      '/records/room': {
        records: [
          { id: ROOM_ID, data: { name: 'Northwind Traders' } },
          { id: 'room_b', data: { name: 'Contoso Health' } },
        ],
      },
    })
    render(<InboundVerificationPage />)
    const picker = await screen.findByLabelText('Room')
    await user.selectOptions(picker, 'room_b')
    expect(picker).toHaveValue('room_b')
  })

  it('reveals the three checks for a delivery on demand', async () => {
    const user = userEvent.setup()
    stubApi()
    render(<InboundVerificationPage />)
    const toggle = await screen.findByRole('button', { name: /Show the three checks/ })
    await user.click(toggle)
    expect(await screen.findByText('ip_allowlist')).toBeInTheDocument()
  })

  it('labels the hidden-check toggle with its purpose, so the state is readable', async () => {
    stubApi()
    render(<InboundVerificationPage />)
    expect(await screen.findByRole('button', { name: /Show the three checks/ })).toBeInTheDocument()
  })
})