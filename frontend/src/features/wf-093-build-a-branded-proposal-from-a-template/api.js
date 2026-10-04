/**
 * WF-093's own API wrapper.
 *
 * `apiRequest` from `@/lib/api` is the transport, exactly as the contract asks: the
 * shared `api` object grows no methods, so a hundred features can each talk to their own
 * `/api/<feature>` routes without anyone editing a shared file.
 *
 * Reads outnumber writes here, and that is the shape of the workflow: the board, the
 * vocabulary, the eight decisions, the templates, the brands, the quotes this workflow
 * reads and the documents it rendered are all reads. The four writes are the authoring
 * steps.
 */

import { apiRequest } from '@/lib/api'

const BASE = '/wf-093'

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

export const proposalApi = {
  // -- the board and the research --------------------------------------------

  /** The headline numbers, read back from the store. */
  summary: (roomId) => call(`/summary${query({ room_id: roomId })}`),

  /**
   * The researched vocabulary, so the page cannot drift from the rules behind it: the
   * eight module kinds, the brand tokens, the logo precedence, the lifecycle, and the
   * three document models with the two this workflow rejected.
   */
  vocabulary: () => call('/vocabulary'),

  /** Every judgement call this workflow made, with the alternative it rejected. */
  decisions: () => call('/decisions'),

  // -- templates -------------------------------------------------------------

  /** Every template definition, with its module order and its custom-module flag. */
  templates: (roomId) => call(`/templates${query({ room_id: roomId })}`),

  /** One template definition. */
  template: (templateId) => call(`/templates/${encode(templateId)}`),

  /** Create or replace one template. The same key updates rather than duplicates. */
  saveTemplate: (payload, { roomId, actor, templateId } = {}) =>
    send('/templates', 'POST', payload, { room_id: roomId, actor, template_id: templateId }),

  // -- brands ----------------------------------------------------------------

  /** Every brand kit, with the four tokens the specification's brand carries. */
  brands: (roomId) => call(`/brands${query({ room_id: roomId })}`),

  /** Create or replace one brand kit. */
  saveBrand: (payload, { roomId, actor, brandId } = {}) =>
    send('/brands', 'POST', payload, { room_id: roomId, actor, brand_id: brandId }),

  // -- the quotes this workflow reads ----------------------------------------

  /**
   * Every quote this workflow can merge into.
   *
   * WF-086 provisions these rows, so the list carries `provisioned_by` and `read_only`.
   * An empty list is a supported state, not a failure: the page renders it as such.
   */
  quotes: (roomId) => call(`/quotes${query({ room_id: roomId })}`),

  /** One quote with its line items as this workflow can read them. */
  quote: (quoteId) => call(`/quotes/${encode(quoteId)}`),

  // -- the merge -------------------------------------------------------------

  /**
   * Merge without writing anything.
   *
   * The merge is pure, so the page can show exactly what a proposal would render before
   * the reviewer commits to one.
   */
  preview: (templateId, quoteId) => call(`/preview${query({ template_id: templateId, quote_id: quoteId })}`),

  /** Merge, and store the document. The stored row is a snapshot, not a live view. */
  instantiate: (templateId, quoteId, { roomId, actor, overrides } = {}) =>
    send(
      '/documents',
      'POST',
      { template_id: templateId, quote_id: quoteId, overrides },
      { room_id: roomId, actor },
    ),

  /** Every rendered document, newest first. */
  documents: (roomId) => call(`/documents${query({ room_id: roomId })}`),

  /** One rendered document, with the non-retroactive rule computed rather than asserted. */
  document: (documentId) => call(`/documents/${encode(documentId)}`),

  /**
   * Move a document one step.
   *
   * A re-render of a published document is refused with 403 and the response carries the
   * remediation, so the page can show it rather than swallowing it.
   */
  transition: (documentId, action, { actor } = {}) =>
    send(`/documents/${encode(documentId)}/transition`, 'POST', { action }, { actor }),
}

/**
 * The three states a rendered document can be in, and what each one means.
 *
 * `published` is a distinct state rather than a flag because the researched rule is stated
 * only about published quotes: "Updating your logo and branding won't update existing
 * published quotes, only currently drafted quotes and quotes created after updating." A
 * draft therefore re-renders and a published one does not, and the page shows that as a
 * per-state consequence rather than describing it in a footnote.
 */
export const DOCUMENT_STATES = [
  {
    value: 'draft',
    label: 'Draft',
    meaning: 'Still being changed. A template or branding change re-renders this document.',
    tone: 'warning',
  },
  {
    value: 'instantiated',
    label: 'Instantiated',
    meaning:
      'Merged and stored. It re-renders on request and takes the current template each time.',
    tone: 'info',
  },
  {
    value: 'published',
    label: 'Published',
    meaning:
      'Issued to the buyer. A later template or branding change does not alter it. Issue the quote again to produce a new one.',
    tone: 'success',
  },
]

/** One document state by value, with a fallback so the page never renders blank. */
export function documentState(value) {
  return DOCUMENT_STATES.find((state) => state.value === value) || DOCUMENT_STATES[0]
}

/**
 * The three logo sources, in the precedence the specification lists them.
 *
 * "Logos can come from the quote branding settings, account branding, or the brand." An
 * earlier source wins, and the page shows which one answered, because "no logo" and "a
 * logo from the third source" are different states a reviewer needs to tell apart.
 */
export const LOGO_SOURCES = [
  {
    value: 'quote_branding',
    label: 'Quote branding settings',
    meaning: 'Set on the quote itself. This wins over both sources below it.',
  },
  {
    value: 'brand_kit',
    label: 'Brand kit',
    meaning: 'Set on the brand the template associates with.',
  },
  {
    value: 'account_branding',
    label: 'Account branding',
    meaning: 'Set on the account. This is the last source consulted.',
  },
  {
    value: 'company_name_fallback',
    label: 'Company name fallback',
    meaning:
      'No logo was set anywhere. The company name was rendered instead, because the template asked for that fallback.',
  },
  {
    value: 'none',
    label: 'No logo',
    meaning: 'No source carried a logo, and the company-name fallback was not asked for.',
  },
]

/** One logo source by value, with a fallback so the page never renders blank. */
export function logoSource(value) {
  return LOGO_SOURCES.find((source) => source.value === value) || LOGO_SOURCES[LOGO_SOURCES.length - 1]
}

/**
 * The three outcomes a binding can have.
 *
 * `unresolved` is a state rather than a failure: the store holds arbitrary JSON, so a
 * template may bind to a field a particular quote does not carry. One empty field must not
 * cost a buyer the whole proposal, so it renders empty and is named.
 */
export const BINDING_STATES = [
  {
    value: 'resolved',
    label: 'Resolved',
    meaning: 'A value was found on the quote record.',
    tone: 'success',
  },
  {
    value: 'literal',
    label: 'Literal',
    meaning: 'The binding named no path, so the template supplies fixed text.',
    tone: 'neutral',
  },
  {
    value: 'unresolved',
    label: 'Unresolved',
    meaning:
      'The binding named a path the quote does not carry. The field renders empty and is listed here rather than failing the whole proposal.',
    tone: 'warning',
  },
]

/** One binding state by value, with a fallback so the page never renders blank. */
export function bindingState(value) {
  return BINDING_STATES.find((state) => state.value === value) || BINDING_STATES[2]
}

/** Which level set a branding token: the brand, the template, or the quote. */
export const BRANDING_LEVELS = {
  brand_kit: 'Brand kit',
  template_override: 'Template override',
  quote_branding: 'Set on this quote',
  template_default: 'No source set this token',
}

/** A branding level's label, with a fallback so the page never renders blank. */
export function brandingLevel(value) {
  return BRANDING_LEVELS[value] || 'No source set this token'
}

/**
 * An instant as the page renders it.
 *
 * Both accepted encodings reach this page, because the engagement rows store Unix
 * milliseconds and this workflow's own rows store ISO. A value the page cannot read
 * renders as `unknown` rather than as a date a rep could misread.
 */
export function formatInstant(value) {
  if (value === undefined || value === null || value === '') return 'unknown'
  const text = String(value)
  const millis = /^-?\d+$/.test(text) ? Number(text) : Date.parse(text)
  if (!Number.isFinite(millis)) return 'unknown'
  const date = new Date(millis)
  if (Number.isNaN(date.getTime())) return 'unknown'
  return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z')
}

/** A monetary amount with the quote's currency, at two decimal places. */
export function formatMoney(amount, currencyLabel) {
  const value = Number(amount)
  if (!Number.isFinite(value)) return 'unknown'
  const rendered = value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return currencyLabel ? `${currencyLabel} ${rendered}` : rendered
}

/** A count with a singular and a plural noun, so a page never says "1 records". */
export function plural(count, noun) {
  const total = Number(count) || 0
  return `${total} ${noun}${total === 1 ? '' : 's'}`
}

/**
 * The three document models the research names, and which one this workflow renders.
 *
 * The research names all three and chooses none, so the page states the choice and both
 * rejections rather than leaving a reader to assume a document format was researched.
 */
export const DOCUMENT_MODELS = [
  {
    value: 'html_json_document_model',
    label: 'HTML and JSON document model',
    chosen: true,
    reason:
      'The merge produces an ordered document model stored as the quote\u2019s presentation layer. No vendor schema and no content library are needed, so it is the only one of the three that can be verified over localhost.',
  },
  {
    value: 'word_content_control_xml_merge',
    label: 'Word content-control XML merge',
    chosen: false,
    reason:
      'Needs an entity XML schema this repository does not hold, and its 100-related-record cap would arrive as a vendor truncation rather than a rule this product states and tests.',
  },
  {
    value: 'pandadoc_content_placeholders',
    label: 'Content-placeholder merge',
    chosen: false,
    reason:
      'Needs a content library this ticket does not claim, and every placeholder must be replaced with 1 to 10 library items at creation, so an unstocked template would fail to instantiate at all.',
  },
]

/** One document model by value, with a fallback so the page never renders blank. */
export function documentModel(value) {
  return DOCUMENT_MODELS.find((model) => model.value === value) || DOCUMENT_MODELS[0]
}