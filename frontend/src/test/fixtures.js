/**
 * Fixtures shaped like real API responses.
 *
 * Deliberately hand-written from the backend's own response shape rather than
 * generated, because the point of these tests is to pin the contract between the
 * two layers. If the backend changes a field name, these break, which is the
 * signal we want.
 */

export const CONFIG = {
  cname_target: 'cname.dsr.test',
  base_url: 'http://127.0.0.1:8000',
  record_type: 'CNAME',
  propagation_note: 'A new CNAME can take up to 24 hours to propagate.',
  cloudflare_note: 'If this domain is proxied by Cloudflare, set the CNAME to DNS only.',
  format_note: 'The domain must be in subdomain format, for example proposals.acme.com.',
}

export const ROOMS = {
  count: 2,
  records: [
    {
      id: 'room_acme',
      collection: 'room',
      revision: 1,
      data: { name: 'Proposal Name', account: 'Acme', domain: 'proposals.acme.com' },
    },
    {
      id: 'room_northwind',
      collection: 'room',
      revision: 1,
      data: { name: 'Northwind Traders' },
    },
  ],
}

export function whiteLabel(overrides = {}) {
  return {
    room_id: 'room_acme',
    name: 'Proposal Name',
    revision: 3,
    base_url: 'http://127.0.0.1:8000',
    cname_target: 'cname.dsr.test',
    domain: 'proposals.acme.com',
    domain_status: 'verified',
    domain_cname_target: 'cname.dsr.test',
    domain_observed: ['cname.dsr.test'],
    domain_last_checked_at: '2026-09-26T10:00:00.000+00:00',
    domain_activated_at: '2026-09-26T10:00:00.000+00:00',
    domain_history: [],
    has_link_secret: true,
    has_collaborator_token: true,
    branding: {},
    slug: 'Proposal-Name-aB3xY9zK1q',
    path: '/r/Proposal-Name-aB3xY9zK1q',
    share_url: 'https://proposals.acme.com/r/Proposal-Name-aB3xY9zK1q',
    default_host_share_url: 'http://127.0.0.1:8000/r/Proposal-Name-aB3xY9zK1q',
    collaborator_url: 'http://127.0.0.1:8000/collab/tokEN123',
    ...overrides,
  }
}

export function report(overrides = {}) {
  return {
    domain: 'proposals.acme.com',
    cname_target: 'cname.dsr.test',
    ready: true,
    status: 'verified',
    observed: ['cname.dsr.test'],
    checked_at: '2026-09-26T10:00:00.000+00:00',
    cloudflare_note: 'Set the CNAME to DNS only if you use Cloudflare.',
    checks: [
      {
        name: 'cname',
        label: 'CNAME points at cname.dsr.test',
        ok: true,
        detail: 'proposals.acme.com resolves to cname.dsr.test',
      },
      { name: 'format', label: 'Subdomain format', ok: true, detail: 'Well formed.' },
      { name: 'available', label: 'Not already in use', ok: true, detail: 'No other room is using it.' },
    ],
    ...overrides,
  }
}

/** Brand for a non-200 response.
 *
 *  Explicit on purpose. An earlier version sniffed for a `status` key, which
 *  silently misfired: the real verify report carries a top-level
 *  `domain_status`/`status` field, so a perfectly good 200 body was mistaken for
 *  an error descriptor and every request "failed". A brand cannot collide with
 *  anything the API returns. */
const HTTP_ERROR = Symbol('httpError')

export const httpError = (status, body) => ({ [HTTP_ERROR]: true, status, body })

/** Install a fetch stub that answers by path.
 *
 *  A route value is the response body, or an `httpError(status, body)` for a
 *  failure. Unlisted paths throw loudly so a test cannot silently pass against
 *  the wrong response.
 */
export function stubApi(routes) {
  const calls = []
  // Longest prefix wins, so a specific route like `.../branding` is not
  // swallowed by a broader one like `.../white-label` listed first. Object key
  // order must not decide which stub answers a request.
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)

  globalThis.fetch = async (url, options = {}) => {
    const path = String(url).replace('/api', '')
    calls.push({ path, method: options.method || 'GET', body: options.body })

    const key = keys.find(
      (candidate) =>
        path === candidate ||
        path.startsWith(candidate + '/') ||
        path.startsWith(candidate + '?') ||
        path.startsWith(candidate),
    )
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
