/**
 * The WF-051 API client.
 *
 * Every call goes through `apiRequest` from the shared `@/lib/api`, which is the
 * escape hatch a feature plugin is meant to use. Nothing here adds a method to
 * the shared `api` object, because that file is on the shared-file list and a
 * hundred features editing it is exactly what this architecture exists to stop.
 *
 * `apiRequest` prepends `/api`, so every path below starts at `/wf-051`.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-051'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

function post(path, payload) {
  return apiRequest(path, { method: 'POST', body: JSON.stringify(payload ?? {}) })
}

function del(path) {
  return apiRequest(path, { method: 'DELETE' })
}

/** `rooms/<id>` with the id encoded, so a room id can never alter the path. */
function room(roomId) {
  return `${BASE}/rooms/${encodeURIComponent(roomId)}`
}

export const routerApi = {
  vocabulary: () => apiRequest(`${BASE}/vocabulary`),
  inferences: () => apiRequest(`${BASE}/inferences`),
  summary: () => apiRequest(`${BASE}/summary`),

  routers: (params) => apiRequest(`${BASE}/routers${query(params)}`),
  router: (routerId) => apiRequest(`${BASE}/routers/${encodeURIComponent(routerId)}`),
  createRouter: (payload, params) => post(`${BASE}/routers${query(params)}`, payload),
  publishRouter: (routerId, payload, params) =>
    post(`${BASE}/routers/${encodeURIComponent(routerId)}/publish${query(params)}`, payload),

  sellers: (params) => apiRequest(`${BASE}/sellers${query(params)}`),
  createSeller: (payload, params) => post(`${BASE}/sellers${query(params)}`, payload),
  roomSellers: (roomId, params) => apiRequest(`${room(roomId)}/sellers${query(params)}`),

  route: (roomId, payload, params) => post(`${room(roomId)}/route${query(params)}`, payload),
  session: (roomId, routeId) => apiRequest(`${room(roomId)}/route/${encodeURIComponent(routeId)}`),
  slots: (roomId, routeId) => apiRequest(`${room(roomId)}/route/${encodeURIComponent(routeId)}/slots`),
  schedule: (roomId, routeId, payload, params) =>
    post(`${room(roomId)}/route/${encodeURIComponent(routeId)}/schedule-simple${query(params)}`, payload),
  expire: (roomId, routeId, params) =>
    post(`${room(roomId)}/route/${encodeURIComponent(routeId)}/expire${query(params)}`, {}),

  bookings: (roomId, params) => apiRequest(`${room(roomId)}/bookings${query(params)}`),
  booking: (roomId, bookingId) =>
    apiRequest(`${room(roomId)}/bookings/${encodeURIComponent(bookingId)}`),
  cancelBooking: (roomId, bookingId, params) =>
    del(`${room(roomId)}/bookings/${encodeURIComponent(bookingId)}${query(params)}`),
}

export default routerApi