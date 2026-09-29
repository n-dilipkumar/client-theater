/**
 * Meeting reminders API calls (WF-061).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-061`, and `apiRequest` already prepends `/api`.
 *
 * Every picker on the page is rendered from the `/vocabulary` and `/tags`
 * endpoints rather than from a list compiled into this file, so a team that adds
 * a condition or a dynamic tag ships a record instead of a change here.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-061'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const reminderApi = {
  /** The researched vocabulary. Drives every picker on the page. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Every dynamic tag and Cal token, with a rendered example each. */
  tags: () => apiRequest(`${PREFIX}/tags`),

  /**
   * What the research leaves open, what this build chose, and how to change it.
   *
   * Skip-reason precedence, where a follow-up is anchored, and whether a message
   * was actually translated are all product behaviour rather than comments, so
   * they are served as data a reviewer can disagree with by name.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  summary: (params = {}) => apiRequest(`${PREFIX}/summary${query(params)}`),

  /** The organisation's messaging setup: the no-reply domain and the numbers. */
  messaging: () => apiRequest(`${PREFIX}/messaging`),
  saveMessaging: (payload) =>
    apiRequest(`${PREFIX}/messaging`, { method: 'PATCH', body: JSON.stringify(payload) }),

  listReminders: (params = {}) => apiRequest(`${PREFIX}/reminders${query(params)}`),
  createReminder: (payload) =>
    apiRequest(`${PREFIX}/reminders`, { method: 'POST', body: JSON.stringify(payload) }),
  reminder: (id) => apiRequest(`${PREFIX}/reminders/${id}`),
  updateReminder: (id, payload) =>
    apiRequest(`${PREFIX}/reminders/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteReminder: (id) => apiRequest(`${PREFIX}/reminders/${id}`, { method: 'DELETE' }),

  listMeetingTypes: (params = {}) => apiRequest(`${PREFIX}/meeting-types${query(params)}`),
  createMeetingType: (payload, params = {}) =>
    apiRequest(`${PREFIX}/meeting-types${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  meetingType: (id) => apiRequest(`${PREFIX}/meeting-types/${id}`),

  /**
   * Attach a reminder to a meeting type, and the researched counterpart:
   * `detach` is "Remove from Meeting Type", which leaves the asset and its other
   * attachments alone. `deleteReminder` is the other action, and it is not the
   * same one.
   */
  attach: (meetingTypeId, reminderId) =>
    apiRequest(`${PREFIX}/meeting-types/${meetingTypeId}/reminders`, {
      method: 'POST',
      body: JSON.stringify({ reminder_id: reminderId }),
    }),
  detach: (meetingTypeId, reminderId) =>
    apiRequest(`${PREFIX}/meeting-types/${meetingTypeId}/reminders/${reminderId}`, {
      method: 'DELETE',
    }),

  listBookings: (roomId, params = {}) => apiRequest(`${PREFIX}/rooms/${roomId}/bookings${query(params)}`),
  createBooking: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings`, { method: 'POST', body: JSON.stringify(payload) }),
  booking: (roomId, bookingId) => apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingId}`),

  /** Attach every enabled reminder to a booking, recording the schedule. */
  plan: (roomId, bookingId) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingId}/plan`, { method: 'POST' }),

  /** The whole workflow for a booking: plan, decide, record. */
  deliver: (roomId, bookingId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingId}/deliver`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /**
   * Render and decide for a real booking, writing nothing at all.
   *
   * The researched Preview pane. It calls the same evaluation the delivery does,
   * so the answer here is the answer there.
   */
  preview: (roomId, bookingId, payload = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/bookings/${bookingId}/preview`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Meetings Activity: per reminder, a status, a reason, and the message. */
  listDeliveries: (params = {}) => apiRequest(`${PREFIX}/deliveries${query(params)}`),
  roomDeliveries: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/deliveries${query(params)}`),
  delivery: (id) => apiRequest(`${PREFIX}/deliveries/${id}`),

  /**
   * "When a guest replies to an SMS reminder, Chili Piper forwards the text to
   * your team by email." Refused with 428 without an organisation-owned Twilio
   * account, which is the research's own warning.
   */
  recordSmsReply: (deliveryId, payload) =>
    apiRequest(`${PREFIX}/deliveries/${deliveryId}/sms-replies`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The reminder as a Cal.com Workflow, with the version header Cal requires. */
  calWorkflow: (reminderId, params = {}) =>
    apiRequest(`${PREFIX}/reminders/${reminderId}/cal-workflow${query(params)}`),
  validateCalWorkflow: (payload) =>
    apiRequest(`${PREFIX}/cal-workflows/validate`, { method: 'POST', body: JSON.stringify(payload) }),

  /** The automation, on demand. A deployment would put this on a timer. */
  fire: (params = {}) => apiRequest(`${PREFIX}/fire${query(params)}`, { method: 'POST' }),
}
