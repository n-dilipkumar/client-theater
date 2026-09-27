/**
 * The WF-024 API, owned by this feature.
 *
 * Every call goes through `apiRequest` from `@/lib/api`, the escape hatch the
 * host provides for exactly this case. `lib/api.js` is read by every feature, so
 * appending methods to it is the collision the plugin host exists to prevent.
 *
 * The filter shape is the feature's own: `date_from`/`date_to`/`owner`/`team`
 * are repeatable or comma-separated, and `query()` flattens a list of chosen
 * values into repeated parameters rather than a string, so a value containing a
 * comma cannot be misread as two values.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-024'

/** Drop empty values so a filter is never sent as `?owner=`. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== undefined && item !== null && item !== '') search.append(key, item)
      }
    } else {
      search.set(key, value)
    }
  }
  const suffix = search.toString()
  return suffix ? `?${suffix}` : ''
}

export const engagementApi = {
  /** What the report can be filtered and sorted by, discovered server-side. */
  filters: () => apiRequest(`${BASE}/filters`),

  /** The five tiles, over every workspace. */
  report: (params) => apiRequest(`${BASE}/report${query(params)}`),

  /** The expanded tile: every account, sortable by any column. */
  accounts: (params) => apiRequest(`${BASE}/accounts${query(params)}`),

  /** One account, with the individuals behind its numbers. */
  account: (accountKey, params) =>
    apiRequest(`${BASE}/accounts/${encodeURIComponent(accountKey)}${query(params)}`),

  /** The same rollup narrowed to one workspace. */
  workspace: (roomId, params) =>
    apiRequest(`${BASE}/rooms/${encodeURIComponent(roomId)}/engagement${query(params)}`),

  teamUsage: (params) => apiRequest(`${BASE}/reports/team-usage${query(params)}`),

  implementations: (params) => apiRequest(`${BASE}/reports/implementations${query(params)}`),

  /** The effective configuration, so a reader can see the rules being applied. */
  config: () => apiRequest(`${BASE}/config`),

  /** Record one client interaction. */
  recordEvent: (payload, roomId) =>
    apiRequest(`${BASE}/events${query({ room_id: roomId })}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
