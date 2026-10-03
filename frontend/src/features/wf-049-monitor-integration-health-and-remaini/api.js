import { apiRequest } from '@/lib/api'

/**
 * Integration monitor API calls (WF-049).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove.
 * The host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-049`, and `apiRequest` already prepends `/api`.
 *
 * Every list is rendered from what the API serves rather than from a list
 * compiled here. The vendors, the four error classes, the metric names, the
 * channels and the quota surfaces all come from `/vocabulary`, so a name added
 * on the server reaches this page without a change to this file.
 */

const PREFIX = '/wf-049'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const monitorApi = {
  /** Vendors, error classes, metrics, channels, and the researched surfaces. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call the research left open, named and bounded.
   *
   * Served as data rather than buried in code comments, so an operator or a
   * reviewer can disagree with a named entry instead of inferring the
   * assumptions from the numbers on screen.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Every monitored connector, optionally narrowed to one room. */
  connectors: (params = {}) => apiRequest(`${PREFIX}/connectors${query(params)}`),

  /** Register a connector for monitoring. */
  createConnector: (payload, params = {}) =>
    apiRequest(`${PREFIX}/connectors${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Pause, resume, or lower concurrency - the researched step 4. */
  updateConnector: (connectorId, payload) =>
    apiRequest(`${PREFIX}/connectors/${connectorId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /** Stop monitoring a connector. */
  deleteConnector: (connectorId) =>
    apiRequest(`${PREFIX}/connectors/${connectorId}`, { method: 'DELETE' }),

  /** Record one reading from a vendor surface - the connector hands it in. */
  recordQuota: (connectorId, payload) =>
    apiRequest(`${PREFIX}/connectors/${connectorId}/quota`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** One connector's latest "remaining today / remaining this window" pair. */
  quota: (connectorId) => apiRequest(`${PREFIX}/connectors/${connectorId}/quota`),

  /** Record the calls a connector made. */
  recordTelemetry: (connectorId, payload) =>
    apiRequest(`${PREFIX}/connectors/${connectorId}/telemetry`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** One connector's health aggregate, without recording anything. */
  telemetryView: (connectorId, params = {}) =>
    apiRequest(`${PREFIX}/connectors/${connectorId}/telemetry${query(params)}`),

  /** Record one change-stream lag observation. */
  recordStream: (connectorId, payload) =>
    apiRequest(`${PREFIX}/connectors/${connectorId}/stream`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The researched Monitoring dashboard, per connector. */
  dashboard: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/dashboard${query(params)}`),

  /** Record the Dataverse change-tracking audit, and its drift signal. */
  recordChangeTracking: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/change-tracking`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** The change-tracking audits, drift first. */
  changeTracking: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/change-tracking${query(params)}`),

  /** The room's alert rules, with their fire history. */
  alertRules: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/alerts${query(params)}`),

  /** Arm an alert rule. */
  createAlertRule: (roomId, payload) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/alerts/rules`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Retune an alert rule. */
  updateAlertRule: (ruleId, payload) =>
    apiRequest(`${PREFIX}/alerts/rules/${ruleId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /** Remove an alert rule. */
  deleteAlertRule: (ruleId) =>
    apiRequest(`${PREFIX}/alerts/rules/${ruleId}`, { method: 'DELETE' }),

  /** Evaluate every rule now, and fire. The poll calls this. */
  evaluateAlerts: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/alerts/evaluate${query(params)}`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
}
