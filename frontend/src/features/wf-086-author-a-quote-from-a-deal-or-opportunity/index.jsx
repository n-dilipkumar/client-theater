import AuthorQuote from './QuoteAuthor'

/**
 * WF-086, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf086_author_a_quote_from_a_deal_or_opportunity.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * One page, not a board and an editor on two routes. A seller authoring a quote is
 * looking at one document and changing one of its lines; splitting the quote and
 * its editor across two routes would mean a line-item edit navigated away from the
 * totals it changes. The quote list is a picker on the same page rather than a
 * separate view, so returning to the list does not lose the editor.
 *
 * The glyph is a document with a currency mark, which is what this workflow
 * produces. It is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared mark. `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-086-author-a-quote-from-a-deal-or-opportunity',
  label: 'Author a quote',
  icon: 'audit',
  iconPath: 'M7 3h7l5 5v13a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zm7 0v5h5M9 13h6M9 17h4',
  order: 86,
  Component: AuthorQuote,
}
