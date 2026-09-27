/**
 * The WF-018 API, owned by this feature.
 *
 * Every other workflow branch added its methods to the shared `api` object in
 * `@/lib/api`. That file is read by every feature, so appending to it is
 * exactly the collision the plugin host exists to prevent: twelve branches each
 * added methods to one object and none of them merged. Everything here goes
 * through `apiRequest`, the escape hatch the host provides for precisely this
 * case, so the shared client is never edited.
 *
 * The room list is read from the core `/records/room` collection rather than
 * imported from `api`, so this feature depends on the HTTP contract and not on
 * another module's idea of a room's shape.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-018'

/** Drop empty values, so a filter is never sent as `?room_id=`. */
function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const suffix = search.toString() ? `?${search}` : ''
  return suffix
}

export const pdfAnalyticsApi = {
  /** The exact vocabulary the backend validates against. */
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),

  /** Every judgement call this workflow makes, with how to change each. */
  inferences: () => apiRequest(`${BASE}/inferences`),

  /** Every room, so the page can scope to one deal or read the whole library. */
  rooms: () => apiRequest('/records/room?limit=100'),

  /** The Library list, with the analytics panel each asset can show. */
  assets: (params = {}) => apiRequest(`${BASE}/assets${query(params)}`),

  /** One asset and its whole Advanced Analytics block. */
  detail: (assetId, params = {}) => apiRequest(`${BASE}/assets/${assetId}${query(params)}`),

  /** The library assets shared into one room, with their engagement in it. */
  roomAssets: (roomId, params = {}) => apiRequest(`${BASE}/rooms/${roomId}/assets${query(params)}`),

  /** The two researched PDF metrics. 422 when the asset has no per-page curve. */
  pdfAnalytics: (assetId, params = {}) => apiRequest(`${BASE}/assets/${assetId}/pdf-analytics${query(params)}`),

  /** The researched watch time. 422 for anything that is not a self-hosted video. */
  videoAnalytics: (assetId, params = {}) => apiRequest(`${BASE}/assets/${assetId}/video-analytics${query(params)}`),

  /** The Core Analytics counts and the series behind the bar charts. */
  coreAnalytics: (assetId, params = {}) => apiRequest(`${BASE}/assets/${assetId}/core-analytics${query(params)}`),
}
