import Documents from './Documents'
import { DOCUMENT_ICON } from './icons'

/**
 * WF-003, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array and all twelve
 * conflicted.
 *
 * `icon` is a name from the shared `PATHS` map; this feature's glyph is not in
 * it, so `iconPath` carries the path instead. `Icon` falls back to `path` when
 * given one, and ui.jsx is not edited.
 */
export default {
  id: 'wf-003-document-library',
  label: 'Documents',
  icon: 'schema',
  iconPath: DOCUMENT_ICON,
  order: 200,
  Component: Documents,
}
