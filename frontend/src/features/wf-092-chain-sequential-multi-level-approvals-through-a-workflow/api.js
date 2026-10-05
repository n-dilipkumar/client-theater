/**
 * Sequential multi-level quote approvals API (WF-092).
 *
 * These live in the feature folder rather than on the shared `api` object in
 * `@/lib/api`, because that file is shared and a hundred features each adding a method
 * to it is exactly the collision the feature host exists to remove. The host's
 * `apiRequest` is the escape hatch that makes that unnecessary.
 *
 * `PREFIX` is the feature's own: the backend module mounts these under `/api/wf-092`,
 * and `apiRequest` already prepends `/api`.
 */

import { apiRequest } from '@/lib/api'

const PREFIX = '/wf-092'

export const approvalApi = {
  /**
   * The researched surface: the branch property, the default threshold, the five
   * operators, the chain states, the three approver requirements, and both caps. Every
   * number the page quotes comes from this rather than from a list compiled here, so a
   * cap changed on the server reaches every client at once.
   */
  vocabulary: () => apiRequest(`${PREFIX}/vocabulary`),

  /**
   * Every judgement call this workflow rests on: what the research fixes, what it leaves
   * open, which reading this build took, and what it deliberately does not build.
   */
  inferences: () => apiRequest(`${PREFIX}/inferences`),

  /** The one approval workflow. The research says it cannot be duplicated. */
  workflow: () => apiRequest(`${PREFIX}/workflow`),

  createWorkflow: (body = {}) =>
    apiRequest(`${PREFIX}/workflow`, { method: 'POST', body: JSON.stringify(body) }),

  toggleReEnrol: (reEnroll) =>
    apiRequest(`${PREFIX}/workflow/re-enrol-toggle`, {
      method: 'POST',
      body: JSON.stringify({ re_enroll: reEnroll }),
    }),

  /** Every configured branch, each with the priority levels it pushes. */
  branches: () => apiRequest(`${PREFIX}/branches`),

  /**
   * Add one branch on a quote property, with the ranked approvers it pushes.
   *
   * `sequences` is the researched shape: one entry per priority, each carrying an
   * `approvers` list, a `requirement`, and the message its approvers see. Both caps are
   * enforced by the server before any row is written, so the page reports the refusal
   * rather than discovering it on the next load.
   */
  addBranch: (body) => apiRequest(`${PREFIX}/branches`, { method: 'POST', body: JSON.stringify(body) }),

  /** The quotes a branch can be evaluated against. */
  quotes: () => apiRequest(`${PREFIX}/quotes`),

  /** Which branches qualify for one quote, and why. Nothing is written. */
  evaluate: (quoteId) => apiRequest(`${PREFIX}/quotes/${quoteId}/evaluate`),

  /**
   * Start the approval flow for one quote.
   *
   * Each qualifying branch pushes an approval step and the chain waits at priority 1.
   * A quote that matched no step comes back auto-approved, which is the researched
   * valve rather than an error.
   */
  enrol: (quoteId) => apiRequest(`${PREFIX}/quotes/${quoteId}/enrol`, { method: 'POST' }),

  enrolments: () => apiRequest(`${PREFIX}/enrolments`),

  enrolment: (id) => apiRequest(`${PREFIX}/enrolments/${id}`),

  /**
   * Record one approver's decision.
   *
   * The server refuses a decision from a lower priority before the current one is
   * done, and answers with `outcome: 'not_yet_your_priority'` rather than a status
   * code, because that refusal is the researched rule working and not a fault.
   */
  decide: (id, approver, decision) =>
    apiRequest(`${PREFIX}/enrolments/${id}/decide`, {
      method: 'POST',
      body: JSON.stringify({ approver, decision }),
    }),

  /** Send a decided chain back to priority 1. Refused unless the switch is on. */
  reEnrol: (id) => apiRequest(`${PREFIX}/enrolments/${id}/re-enrol`, { method: 'POST' }),
}