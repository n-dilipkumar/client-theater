import QuoteGuardrails from './QuoteGuardrails'
import { GUARDRAIL_ICON } from './primitives'

/**
 * Enforce configuration and discount guardrails with quote rules (WF-090).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be touched to
 * add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf090_quote_guardrails.py` exactly, so the two halves of the
 * feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a dial is not in
 * the shared `PATHS` map, and that file is not ours to edit. `icon` carries the closest
 * shared name as a fallback for any consumer that reads only that field; the nav itself
 * renders `iconPath`.
 */
export default {
  id: 'wf-090-quote-guardrails',
  label: 'Quote guardrails',
  icon: 'schema',
  iconPath: GUARDRAIL_ICON,
  order: 905,
  Component: QuoteGuardrails,
}
