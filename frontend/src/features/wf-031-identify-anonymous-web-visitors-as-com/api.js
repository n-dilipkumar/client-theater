/**
 * Anonymous-visitor identification API (WF-031).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-031`, and `apiRequest` already prepends `/api`.
 *
 * Three refusals here are the researched ones rather than generic failures, and
 * each has its own message, because a seller who typed a full URL into the page
 * definition and was told "bad request" has no idea what to change:
 *
 * * `path_carries_a_domain` - "When you type in the web page URL do not include
 *   the domain."
 * * `personal_data_refused` - a capture that tries to name an individual.
 * * `unknown_installation` - a capture from a Client ID that is not installed.
 *
 * Every call here is one the page actually makes.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-031'

/**
 * Drop empty values so we never send `?segment=` and confuse a filter, and repeat
 * a key for list-valued filters, which is how a query string carries a list of
 * page ids or tags.
 */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const entry of value) search.append(key, entry)
    } else {
      search.set(key, value)
    }
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

/**
 * The query parameters the lead list sends, from the page's filter state.
 *
 * `page` is repeated once per selected intent page. The backend reads "any of",
 * so naming two pages widens the selection, which is what "isolate companies that
 * visited those pages" says.
 */
export function leadQuery(filters) {
  return {
    page: filters.page,
    tag: filters.tag,
    segment: filters.segment,
    icp: filters.icp,
    country: filters.country,
    size: filters.size,
    limit: filters.limit,
  }
}

export const visitorApi = {
  /**
   * Every published value: the three match conditions with the vendor's own
   * labels, the three public capture parameters, the five researched company
   * fields, the two keys a contact may carry, the lead-list filter set, the
   * ranking, and the two downstream surfaces this workflow hands a company list to
   * without building them. Every picker on the page renders from this.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one. The
   * page shows how many there are, because the sourced half of this workflow is
   * the field set, the three conditions and the path rule, and everything below
   * that is where the research stops.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  installations: () => apiRequest(`${PREFIX}/installations`),
  install: (payload) =>
    apiRequest(`${PREFIX}/installations`, { method: 'POST', body: JSON.stringify(payload) }),

  /**
   * The tracking snippet's endpoint: one anonymous request. The body carries only
   * the three public parameters the research names, plus the path and the Client
   * ID. The page does not offer a field for anything else, because the server
   * refuses it and a control that is always refused is a control that teaches a
   * seller the product is broken.
   */
  capture: (payload) =>
    apiRequest(`${PREFIX}/captures`, { method: 'POST', body: JSON.stringify(payload) }),

  pages: (clientId) => apiRequest(`${PREFIX}/pages${query({ client_id: clientId })}`),
  definePage: (payload, clientId) =>
    apiRequest(`${PREFIX}/pages${query({ client_id: clientId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  amendPage: (id, payload) =>
    apiRequest(`${PREFIX}/pages/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  dropPage: (id) =>
    apiRequest(`${PREFIX}/pages/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  profiles: () => apiRequest(`${PREFIX}/icps`),
  saveProfile: (payload) =>
    apiRequest(`${PREFIX}/icps`, { method: 'POST', body: JSON.stringify(payload) }),
  dropProfile: (id) => apiRequest(`${PREFIX}/icps/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  companies: (params) => apiRequest(`${PREFIX}/companies${query(params)}`),
  company: (key) => apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}`),
  addCompany: (payload) =>
    apiRequest(`${PREFIX}/companies`, { method: 'POST', body: JSON.stringify(payload) }),
  amendCompany: (key, payload, actor) =>
    apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}${query({ actor })}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  visits: (key, params) =>
    apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}/visits${query(params)}`),
  companyPages: (key) => apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}/pages`),
}

/**
 * The message for the three researched refusals, in the research's own words.
 *
 * A generic "could not save" for a page definition that carries a domain would
 * leave the seller guessing, so each one says what to do instead.
 *
 * `apiRequest` puts the server's `detail` on the Error and keeps the status, so
 * the matching is on the detail text and the status rather than on a code - the
 * code is not carried through. Each pattern therefore matches the sentence the
 * server actually sends, which is quoted here so a change to the wording shows up
 * as a failing test rather than as a silent loss of the useful message.
 */
export function refusalMessage(error) {
  const text = String(error?.message || error)
  if (error?.status === 422 && /carries a domain|without the domain/i.test(text)) {
    return 'Enter the path without the domain, for example /newsroom/converting-the-unconverted-article'
  }
  if (/person-level parameter|identifies an individual|companies only/i.test(text)) {
    return 'This workflow identifies companies, not people. A capture cannot name an individual.'
  }
  if (/no tracking snippet is installed|client id/i.test(text)) {
    return 'Install the tracking snippet first, then use the Client ID it issued.'
  }
  return text
}

/** The names of the intent pages a company has satisfied, for a row. */
export function matchedPageNames(company, pages) {
  const byId = new Map((pages || []).map((page) => [page.id, page]))
  return (company?.matched_pages || []).map((id) => byId.get(id)?.name || id)
}

/** Whether a row carries any of the five researched company fields. */
export function isIdentified(company) {
  return Boolean(company?.name || company?.website || company?.address || company?.size)
}

/** A short label for a company with no name yet, so a row is never blank. */
export function companyLabel(company) {
  if (!company) return ''
  return company.name || company.company_key || 'Unnamed company'
}
