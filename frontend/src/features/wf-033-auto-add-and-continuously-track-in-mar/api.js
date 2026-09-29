/**
 * In-market company intent API (WF-033).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the collision the feature host exists to remove.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-033`, and `apiRequest` already prepends `/api`.
 *
 * Two of these calls can be refused with a status rather than an error the page
 * should retry: the segment filter and the exclusions list both need HubSpot
 * Credits, and the three signal-driven CRM writes need the Data enrichment
 * permission. `apiRequest` puts the status on the Error, so the page can offer
 * the right remedy instead of showing a generic failure.
 *
 * Every call here is one the page below actually makes. A client method nobody
 * calls is a method that will disagree with the server the first time the server
 * changes, and this is a folder of one feature rather than a shared module, so
 * there is no argument for keeping it as a convenience.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-033'

/**
 * Drop empty values so we never send `?segment=` and confuse a filter, and
 * repeat a key for list-valued filters, which is how a query string carries a
 * list of traffic sources or page clauses.
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

/** The query parameters the Visitors table sends, from the page's filter state. */
export function companyQuery(filters) {
  return {
    days: filters.days === null || filters.days === '' ? '' : filters.days,
    visitor_intent: filters.visitor_intent ? 'true' : '',
    in_target_markets: filters.in_target_markets ? 'true' : '',
    traffic_source: filters.traffic_source,
    country: filters.country,
    // `path` is `operator:value` or `operator:value@domain` - the five researched
    // operators plus Domain - because a query parameter cannot carry a list of
    // objects, and a saved view's stored filter set is the same shape.
    path: filters.path ? [filters.path] : [],
    segment: filters.segment,
    lifecycle_stage: filters.lifecycle_stage,
    deal_stage: filters.deal_stage,
    owner: filters.owner,
    sort: filters.sort,
    direction: filters.direction,
  }
}

export const intentApi = {
  /**
   * Every published vocabulary: the five path operators with the vendor's own
   * labels, the three sort keys, the traffic sources, the news signal types, the
   * four stock auto-add categories with their definitions, the credit costs, and
   * the four Buyer Intent card fields. Every picker on the page renders from this.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the workflow rests on, and how to change each one. The
   * page shows how many there are and the first few, because the sourced half of
   * this workflow is the filter vocabulary and the four stock categories, and
   * everything below that is where the research stops.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  settings: () => apiRequest(`${PREFIX}/settings`),
  saveSettings: (payload, actor) =>
    apiRequest(`${PREFIX}/settings${query({ actor })}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /** What this caller may do, so the page hides what the API would refuse. */
  capabilities: (actor) => apiRequest(`${PREFIX}/capabilities${query({ actor })}`),

  criteria: () => apiRequest(`${PREFIX}/criteria`),
  addCriterion: (payload) =>
    apiRequest(`${PREFIX}/criteria`, { method: 'POST', body: JSON.stringify(payload) }),

  markets: () => apiRequest(`${PREFIX}/markets`),
  topics: () => apiRequest(`${PREFIX}/topics`),

  /** Credit-gated: "excluding companies" is one of the two things credits buy. */
  exclusions: () => apiRequest(`${PREFIX}/exclusions`),
  exclude: (payload, actor) =>
    apiRequest(`${PREFIX}/exclusions${query({ actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  unexclude: (domain, actor) =>
    apiRequest(`${PREFIX}/exclusions/${encodeURIComponent(domain)}${query({ actor })}`, {
      method: 'DELETE',
    }),

  /** The Research tab: topic matches and company news, counted apart. */
  research: (params) => apiRequest(`${PREFIX}/research${query(params)}`),

  companies: (params) => apiRequest(`${PREFIX}/companies${query(params)}`),
  card: (key) => apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}/card`),
  pageViews: (key, params) =>
    apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}/page-views${query(params)}`),
  contacts: (key) => apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}/contacts`),

  /** The researched manual control, beside the automated ones. */
  enroll: (key, payload, actor) =>
    apiRequest(`${PREFIX}/companies/${encodeURIComponent(key)}/enroll${query({ actor })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  views: () => apiRequest(`${PREFIX}/views`),
  saveView: (payload) => apiRequest(`${PREFIX}/views`, { method: 'POST', body: JSON.stringify(payload) }),
  viewCompanies: (id) => apiRequest(`${PREFIX}/views/${id}/companies`),

  automations: () => apiRequest(`${PREFIX}/automations`),
  saveAutomation: (payload) =>
    apiRequest(`${PREFIX}/automations`, { method: 'POST', body: JSON.stringify(payload) }),
  runAutomation: (id, actor) =>
    apiRequest(`${PREFIX}/automations/${id}/run${query({ actor })}`, { method: 'POST' }),

  categories: () => apiRequest(`${PREFIX}/categories`),
  setCategory: (id, payload) =>
    apiRequest(`${PREFIX}/categories/${id}`, { method: 'POST', body: JSON.stringify(payload) }),
  runCategory: (id, actor) =>
    apiRequest(`${PREFIX}/categories/${id}/run${query({ actor })}`, { method: 'POST' }),

  tracked: () => apiRequest(`${PREFIX}/tracked`),
  renew: (actor) => apiRequest(`${PREFIX}/tracked/renew${query({ actor })}`, { method: 'POST' }),
  credits: () => apiRequest(`${PREFIX}/credits`),
  overview: (actor) => apiRequest(`${PREFIX}/overview${query({ actor })}`),
}

/** Whether the refusal was one of the two researched gates. */
export function isGateError(error) {
  return error?.status === 402 || error?.status === 403
}

/**
 * The message for a gate, in the research's own words.
 *
 * "To access buyer intent features like filtering by segments and excluding
 * companies, you need HubSpot Credits" and "Super Admin must assign users with
 * Data enrichment permissions" are two different remedies, so a page that shows
 * one generic "not allowed" leaves the user to work out which one applies.
 */
export function gateMessage(error) {
  if (error?.status === 402) {
    return 'This needs HubSpot Credits. Filtering by segments and excluding companies are both gated on them.'
  }
  if (error?.status === 403) {
    return 'This writes a CRM record from an intent signal, so a Super Admin has to assign you the Data enrichment permission first.'
  }
  return String(error?.message || error)
}

/** How many of a run's enrichments actually changed something. */
export function enrichedCount(response) {
  return (response?.enriched || []).filter((entry) => entry.outcome === 'enriched').length
}
