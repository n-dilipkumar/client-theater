import LibrarySearch from './LibrarySearch'
import { LIBRARY_ICON } from './icons'

/**
 * WF-010, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded `ROUTES` array and all twelve
 * conflicted.
 *
 * The branch registered this page by adding an import and a `ROUTES` entry to
 * `App.jsx`, and by adding eight methods to the `api` object in `lib/api.js`.
 * Both files are shared. Here the page talks to `./api`, which wraps `apiRequest`,
 * and the entry below is all the registration there is.
 *
 * `id` matches the backend `FEATURE["id"]` exactly, which is what ties the nav
 * entry to the router the host mounted.
 *
 * `icon` is a name from the shared `PATHS` map; this feature's glyph is not in it,
 * so `iconPath` carries the path instead. `Icon` falls back to `path` when given
 * one, and `ui.jsx` is not edited.
 */
export default {
  id: 'wf-010-library-search',
  label: 'Library search',
  icon: 'search',
  iconPath: LIBRARY_ICON,
  order: 200,
  Component: LibrarySearch,
}
