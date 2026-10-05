/**
 * Quote guardrails API (WF-090).
 *
 * The methods live in this feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method
 * to it is the collision the feature host exists to remove. `apiRequest` is the escape
 * hatch that makes that unnecessary.
 *
 * `PREFIX` is this feature's own. The backend module mounts these under `/api/wf-090`
 * and `apiRequest` already prepends `/api`, so the two spellings cannot drift.
 *
 * `listQuotes` reads `wf086_quote` through the generic records route rather than through
 * a route of this feature's own, because WF-086 owns quotes and the guardrail feature
 * only reads them. A quote the rule engine cannot see is a bug, not a second source of
 * truth.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-090'

export const guardrailApi = {
  /**
   * The vocabulary: the field names, the two outcomes, the six scopes, the five
   * aggregates, the two quantifiers, every reason code, and the two documented limits.
   * Everything the page prints as a law comes from the server so a changed limit reaches
   * every client at once.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on: what the research fixes, what it leaves
   * open, which reading this build took, and what it deliberately does not build.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** Every configured rule, in the room the caller is looking at. */
  rules: (params = {}) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') search.set(key, value)
    }
    const suffix = search.toString() ? `?${search}` : ''
    return apiRequest(`${PREFIX}/rules${suffix}`)
  },

  /** One rule by id. */
  rule: (id) => apiRequest(`${PREFIX}/rules/${id}`),

  /**
   * Add one rule.
   *
   * The server validates the definition against the DSL before writing anything, so a
   * rule that cannot be read comes back 422 with the parser's own reason code and line
   * rather than being stored and failing later on a seller's quote.
   */
  createRule: (body) =>
    apiRequest(`${PREFIX}/rules`, { method: 'POST', body: JSON.stringify(body) }),

  /** Edit one rule. A merge patch: only the keys present are changed. */
  patchRule: (id, body) =>
    apiRequest(`${PREFIX}/rules/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  /** Remove one rule. */
  deleteRule: (id) => apiRequest(`${PREFIX}/rules/${id}`, { method: 'DELETE' }),

  /**
   * Ask the parser whether a definition is readable, before saving.
   *
   * This always answers 200. A definition that does not parse comes back as
   * `valid: false` with the reason code, because "is this readable" is a question with a
   * yes-or-no answer and not a fault. That is what the research calls the sandbox: an
   * author tries a definition without touching a live quote.
   */
  validate: (definition) =>
    apiRequest(`${PREFIX}/rules/validate`, {
      method: 'POST',
      body: JSON.stringify({ rule_definition: definition }),
    }),

  /**
   * Evaluate one quote against every enabled rule.
   *
   * Nothing is written. The specification says rules evaluate continuously as a quote is
   * built, and a page that polled a writing endpoint would leave an audit row per
   * keystroke; this is the read that makes "continuously" affordable.
   */
  evaluate: (quoteId) => apiRequest(`${PREFIX}/quotes/${quoteId}/evaluate`),

  /**
   * Attempt to publish one quote.
   *
   * A quote that breaks a Block publish rule comes back 409 with the blocking rules
   * named, its own code `guardrail_block_publish`, and the rule's own message. That
   * refusal is the researched rule working, not a transport fault, so the caller reads
   * the body rather than treating every failure alike.
   */
  publish: (quoteId) => apiRequest(`${PREFIX}/quotes/${quoteId}/publish`, { method: 'POST' }),

  /** The verdicts recorded for one quote, newest first. */
  evaluations: (quoteId) => apiRequest(`${PREFIX}/quotes/${quoteId}/evaluations`),

  /** Every recorded evaluation in the room. */
  allEvaluations: () => apiRequest(`${PREFIX}/evaluations`),

  /** Counts for the room header: rules, verdicts, publish attempts. */
  summary: () => apiRequest(`${PREFIX}/summary`),

  /** The quotes a rule can be evaluated against. WF-086 owns these records. */
  listQuotes: () => apiRequest('/records/wf086_quote'),

  /** The line items of one quote, which most rules read. */
  listLineItems: (quoteId) =>
    apiRequest(`/records/wf086_line_item?${new URLSearchParams({ where: `quote_id=${quoteId}` })}`),
}
