import { apiRequest } from '@/lib/api'

/**
 * Sync log API calls (WF-040).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a
 * method to it is precisely the conflict the feature host exists to remove. The
 * host's `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under
 * `/api/wf-040`, and `apiRequest` already prepends `/api`.
 *
 * Nothing here compiles a list the server owns. The connectors, the status chips,
 * the dispositions and the error model all come from `/vocabulary` and
 * `/connectors`, so a name added on the server reaches this page without a change
 * to this file.
 */

const PREFIX = '/wf-040'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

/** Rows inside a batch the room can check before it is sent. */
function rows(items) {
  return items.map((item) => ({
    row_key: item.rowKey,
    entity: item.entity,
    trace_id: item.traceId || undefined,
    values: item.values || {},
  }))
}

export const syncApi = {
  /** The connectors, row statuses, dispositions, the error model, the collections. */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /** Per vendor: the request that yields per-record outcomes, the statuses, the gaps. */
  connectors: () => apiRequest(`${PREFIX}/connectors`),

  /** Every judgement call the research left open, named and bounded. */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The classification overrides, the pre-flight rules, and the attempt bound. */
  rules: () => apiRequest(`${PREFIX}/rules`),

  /**
   * Retune the rules without a redeploy. A partial patch; the write is audited.
   *
   * The researched extension point: "a deployment can add a rule ... without
   * changing the transport".
   */
  updateRules: (patch) =>
    apiRequest(`${PREFIX}/rules`, { method: 'PATCH', body: JSON.stringify(patch) }),

  /**
   * Reject invalid writes before the batch is sent.
   *
   * Writes nothing, so it leaves no audit rows: a row refused here never reaches a
   * CRM and therefore never appears in the Sync log.
   */
  validate: (connector, items) =>
    apiRequest(`${PREFIX}/validate`, {
      method: 'POST',
      body: JSON.stringify({ connector, rows: rows(items) }),
    }),

  /** Sync runs, newest first, with the counts a rep reads them for. */
  runs: (params = {}) => apiRequest(`${PREFIX}/runs${query(params)}`),

  /**
   * Hand the room a batch result: the rows that were sent and the vendor's response.
   *
   * This is the connector seam. The room holds no vendor credentials and opens no
   * socket, so the connector sends the batch and posts what came back.
   */
  recordRun: (payload, params = {}) =>
    apiRequest(`${PREFIX}/runs${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** One run, with its per-row outcomes and the notes explaining anything odd. */
  run: (runId) => apiRequest(`${PREFIX}/runs/${runId}`),

  /**
   * The researched admin action: retry failed rows only.
   *
   * Called with no payload it reports the rows to re-send; called with
   * `{ vendor }` it applies the connector's response.
   */
  retryRun: (runId, payload) =>
    apiRequest(`${PREFIX}/runs/${runId}/retry`, {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    }),

  /** What drains automatically, and what waits for a person. */
  queue: (params = {}) => apiRequest(`${PREFIX}/queue${query(params)}`),

  /**
   * The researched automation. Called with no body it reports the batch that is
   * due; called with `{ vendor }` it applies the responses.
   *
   * `asOf` is how a caller gets past a row's backoff without waiting for it, and
   * how a question about the past is answered.
   */
  drain: (params = {}, payload) =>
    apiRequest(`${PREFIX}/queue/drain${query(params)}`, {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    }),

  /** The room's Sync log: one row per outcome, newest attempt first. */
  syncLog: (roomId, params = {}) =>
    apiRequest(`${PREFIX}/rooms/${roomId}/sync-log${query(params)}`),

  /** Field-level error detail: which property, what was sent, what was expected. */
  row: (roomId, rowId) => apiRequest(`${PREFIX}/rooms/${roomId}/rows/${rowId}`),
}

export { rows as batchRows }
