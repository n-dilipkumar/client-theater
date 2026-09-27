import ContentLibrary from './ContentLibrary'
import { LIBRARY_ICON } from './icons'

/**
 * WF-007, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the original workflow
 * branch appended to a hard-coded ROUTES array, and so did the other eleven, and
 * all twelve conflicted.
 *
 * `id` must match `FEATURE["id"]` in backend/dsr/features/wf007_library.py
 * exactly, or the two halves of one workflow stop agreeing about what they are.
 *
 * `icon` is a name from the shared `PATHS` map and `iconPath` carries this
 * feature's own glyph, which is not in that map. `Icon` prefers `path` when
 * given one, and ui.jsx is not edited.
 */
export default {
  id: 'wf-007-content-library',
  label: 'Content library',
  icon: 'database',
  iconPath: LIBRARY_ICON,
  order: 210,
  Component: ContentLibrary,
}
