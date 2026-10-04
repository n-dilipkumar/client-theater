import ConciergeRouterPage from './ConciergeRouterPage'
import { ROUTER_ICON } from './icons'

/**
 * Route and book a demo request inline from a web form, registered (WF-051).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, because the two are
 * how a page and its API are tied together across the two languages.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name, because a
 * calendar with a route arrow is not in the shared `PATHS` map and that file is
 * not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field.
 */
export default {
  id: 'wf-051-route-and-book-a-demo-request-inline-f',
  label: 'Concierge router',
  icon: 'rooms',
  iconPath: ROUTER_ICON,
  order: 265,
  Component: ConciergeRouterPage,
}