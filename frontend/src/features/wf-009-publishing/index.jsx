import PublishingPage from './PublishingPage'
import { PUBLISH_ICON } from './icons'

/**
 * WF-009, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array in App.jsx and all
 * twelve conflicted.
 *
 * `id` matches `FEATURE["id"]` in backend/dsr/features/wf009_publishing.py
 * exactly. `icon` is a name the shared `PATHS` map knows; this feature's glyph
 * is not in it, so `iconPath` carries the path instead and `Icon` falls back to
 * it. ui.jsx is not edited.
 */
export default {
  id: 'wf-009-publishing',
  label: 'Approval & publishing',
  icon: 'audit',
  iconPath: PUBLISH_ICON,
  order: 200,
  Component: PublishingPage,
}
