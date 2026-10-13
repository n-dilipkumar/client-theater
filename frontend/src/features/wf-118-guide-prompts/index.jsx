import GuidePromptsPage from './GuidePromptsPage'

/**
 * WF-118, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name and no shared file is touched to
 * add or change it.
 *
 * The folder is one segment deep because that is the shape `lib/features.js`
 * globs (`../features/*\/index.jsx`). A deeper folder would compile and then be
 * silently undiscovered, which is the worst kind of failure to debug.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf118_order_throttle_and_repeat_in_app_guide_prompts.py`
 * exactly, which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `schema` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-118-guide-prompts',
  label: 'Guide prompts',
  icon: 'schema',
  iconPath: 'M4 4h16v12H8l-4 4V4z',
  order: 118,
  Component: GuidePromptsPage,
}
