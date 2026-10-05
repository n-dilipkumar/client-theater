/**
 * WF-100's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the shared
 * `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * Reads outnumber writes here, and that is the shape of the workflow. The board, the
 * vocabulary, the recorded decisions, the contracts, the templates, the pipelines, the
 * quotes, the deals and the workflows are all reads. The writes are: create a template,
 * create a pipeline, create a quote, share a quote, accept a quote, create a workflow and
 * run one.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-100'

function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, value)
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** As `apiRequest`, but keeps the parsed error body on the thrown error. */
async function requestWithBody(path, options) {
  const response = await fetch(`/api${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is the best we have.
    }
    const error = new Error(
      body?.detail || body?.error || `${response.status} ${response.statusText}`,
    )
    error.status = response.status
    error.code = body?.error || null
    error.errors = body?.errors || null
    error.body = body
    throw error
  }

  if (response.status === 204) return null
  return response.json()
}

function call(path) {
  return apiRequest(`${BASE}${path}`)
}

function send(path, method, payload, params) {
  return requestWithBody(`${path}${query(params)}`, { method, body: JSON.stringify(payload) })
}

const encode = encodeURIComponent

export const renewalApi = {
  // -- the board and the research --------------------------------------------

  /** The headline numbers, read back from the store. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the page cannot drift from the rules behind it: the four
   * change effective date modes, the renewal date rule with both branches, the Evergreen
   * label, the deal selection methods, the contract targets and the re-enrol decision.
   */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- contracts -------------------------------------------------------------

  /**
   * Every contract with its term label, its renewal date and when its alert falls due.
   *
   * A contract is a plain JSON record this workflow reads. It never writes one except as
   * the renewal an acceptance creates, so a contract a caller seeded by hand is a supported
   * state rather than a failure.
   */
  contracts: (roomId) => call(`/contracts${query({ room_id: roomId })}`),

  /** One contract with its renewal chain in both directions. */
  contract: (contractId) => call(`/contracts/${encode(contractId)}`),

  // -- templates and pipelines -----------------------------------------------

  /** Every renewal and change quote template. This workflow owns the collection. */
  templates: (roomId) => call(`/templates${query({ room_id: roomId })}`),

  /** Create one renewal or change quote template. */
  createTemplate: (payload, { roomId, actor } = {}) =>
    send('/templates', 'POST', payload, { room_id: roomId, actor }),

  /** Every deal pipeline the seller can start a renewal deal in. */
  pipelines: (roomId) => call(`/pipelines${query({ room_id: roomId })}`),

  /** Register one deal pipeline with its stages. */
  createPipeline: (payload, { roomId, actor } = {}) =>
    send('/pipelines', 'POST', payload, { room_id: roomId, actor }),

  // -- renewal quotes --------------------------------------------------------

  /** Every renewal quote, newest first. */
  quotes: (roomId) => call(`/quotes${query({ room_id: roomId })}`),

  /** One renewal quote with its resolved effective date and proration answer. */
  quote: (quoteId) => call(`/quotes/${encode(quoteId)}`),

  /**
   * Create a renewal quote from a contract.
   *
   * The quote is prefilled from the contract, so a seller never types the commercial content
   * twice. A contract that cannot be renewed answers 409 and the body carries the remedy.
   */
  createQuote: (payload, { roomId, actor } = {}) =>
    send('/quotes', 'POST', payload, { room_id: roomId, actor }),

  /** Change a quote's effective date mode or its proration flag. */
  updateQuote: (quoteId, payload, { roomId, actor } = {}) =>
    requestWithBody(`/quotes/${encode(quoteId)}${query({ room_id: roomId, actor })}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /**
   * Move a quote to draft, shared or superseded.
   *
   * `accepted` is refused here with a 400, because accepting creates the new contract and
   * the renewal deal and that has its own route.
   */
  changeState: (quoteId, state, { roomId, actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/state`, 'POST', { state }, { room_id: roomId, actor }),

  /**
   * Accept a quote. This is the researched transition: it creates the new contract, links
   * the chain and creates the renewal deal.
   *
   * A second acceptance answers 409, so the page can show the conflict rather than produce
   * a duplicate contract in the chain.
   */
  accept: (quoteId, payload = {}, { roomId, actor } = {}) =>
    send(`/quotes/${encode(quoteId)}/accept`, 'POST', payload, { room_id: roomId, actor }),

  // -- renewal deals ---------------------------------------------------------

  /** Every renewal deal this workflow created. Deals appear at acceptance, not at quote time. */
  deals: (roomId) => call(`/deals${query({ room_id: roomId })}`),

  /** One renewal deal, with the quote and the new contract it tracks. */
  deal: (dealId) => call(`/deals/${encode(dealId)}`),

  // -- the renewal workflow action -------------------------------------------

  /** Every renewal workflow definition, with the note that there is no vendor call. */
  workflows: (roomId) => call(`/workflows${query({ room_id: roomId })}`),

  /** One renewal workflow definition. */
  workflow: (workflowId) => call(`/workflows/${encode(workflowId)}`),

  /** Create a renewal workflow against one contract or against all associated contracts. */
  createWorkflow: (payload, { roomId, actor } = {}) =>
    send('/workflows', 'POST', payload, { room_id: roomId, actor }),

  /**
   * Run a renewal workflow, creating a renewal quote for each contract it covers.
   *
   * The response names the scope and says how many quotes were created, so the page can tell
   * the two researched contract scopes apart without reading the definition back.
   */
  runWorkflow: (workflowId, payload = {}, { roomId, actor } = {}) =>
    send(`/workflows/${encode(workflowId)}/run`, 'POST', payload, { room_id: roomId, actor }),
}

/**
 * The four researched states a renewal quote can be in.
 *
 * The research names the entry point and the acceptance outcome and nothing between them, so
 * `draft`, `shared` and `superseded` are this implementation's choice. Each entry says so, and
 * the page shows the note rather than presenting the whole chain as sourced.
 */
export const QUOTE_STATES = [
  {
    value: 'draft',
    label: 'Draft',
    meaning: 'Created from a contract. No buyer has seen it.',
    tone: 'warning',
    sourced: false,
  },
  {
    value: 'shared',
    label: 'Shared with the buyer',
    meaning: 'The seller shared it. No state change happens in the data.',
    tone: 'info',
    sourced: false,
  },
  {
    value: 'accepted',
    label: 'Accepted by the buyer',
    meaning:
      'The new contract is created and linked to the prior one, and the renewal deal is created. This is the one transition the research states.',
    tone: 'success',
    sourced: true,
  },
  {
    value: 'superseded',
    label: 'Superseded by a newer quote',
    meaning: 'A newer renewal quote was created from the same contract. This one cannot be accepted.',
    tone: 'neutral',
    sourced: false,
  },
]

/** One quote state by value, with a fallback so the page never renders blank. */
export function quoteState(value) {
  return QUOTE_STATES.find((state) => state.value === value) || QUOTE_STATES[0]
}

/** The four researched change effective date modes, under the labels the research gives. */
export const EFFECTIVE_DATE_MODES = [
  {
    value: 'on_agreement',
    label: 'On agreement',
    hint: 'Takes effect on the day the buyer accepts.',
  },
  {
    value: 'custom_date',
    label: 'Custom date',
    hint: 'Takes effect on a date the seller chose with a date picker.',
  },
  {
    value: 'delayed_start',
    label: 'Delayed start',
    hint: 'Takes effect a set number of days after the day of agreement.',
  },
  { value: 'months', label: 'Months', hint: 'Takes effect a set number of months after the day of agreement.' },
]

/** The two researched deal selection methods for a renewal. */
export const DEAL_SELECTION_METHODS = [
  {
    value: 'new_deal_default_stage',
    label: 'New deal using default pipeline and stage',
    meaning: 'The acceptance creates a new deal in the pipeline and stage the seller chose.',
  },
  {
    value: 'existing_deal',
    label: 'Existing deal',
    meaning: 'The renewal attaches to a deal the seller named. No deal is created.',
  },
]

/** The two researched contract scopes for a renewal workflow. */
export const CONTRACT_TARGETS = [
  { value: 'one_contract', label: 'One contract' },
  { value: 'all_associated', label: 'Contracts: all associated' },
]

/** One contract target by value, with a fallback so the page never renders blank. */
export function contractTarget(value) {
  return CONTRACT_TARGETS.find((target) => target.value === value) || CONTRACT_TARGETS[0]
}

/**
 * An ISO date as the page renders it, or `not set`.
 *
 * A date a page cannot read renders as `not set` rather than as `Invalid Date`, because a
 * renewal board whose dates read `Invalid Date` cannot be reviewed.
 */
export function formatDate(value) {
  if (value === undefined || value === null || value === '') return 'not set'
  const text = String(value)
  const millis = /^\d{4}-\d{2}-\d{2}$/.test(text) ? Date.parse(`${text}T00:00:00Z`) : Date.parse(text)
  if (!Number.isFinite(millis)) return 'not set'
  const date = new Date(millis)
  if (Number.isNaN(date.getTime())) return 'not set'
  return date.toISOString().slice(0, 10)
}

/** A monetary amount with its currency, at two decimal places. */
export function formatMoney(amount, currency) {
  const value = Number(amount)
  if (!Number.isFinite(value)) return 'unknown'
  const rendered = value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return currency ? `${currency} ${rendered}` : rendered
}

/** A count with a singular and a plural noun, so a page never says "1 records". */
export function plural(count, noun) {
  const total = Number(count) || 0
  return `${total} ${noun}${total === 1 ? '' : 's'}`
}