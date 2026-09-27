import PlayAutomations from './PlayAutomations'
import { PLAY_ICON } from './icons'

/**
 * Play automations, registered (WF-028).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf028_turn_a_signal_into_an_automatic_seller.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a signal
 * going out and a task arriving" is not in the shared `PATHS` map, and that file is
 * not ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`. See
 * `./icons.jsx`.
 */
export default {
  id: 'wf-028-turn-a-signal-into-an-automatic-seller',
  label: 'Play automations',
  icon: 'schema',
  iconPath: PLAY_ICON,
  order: 280,
  Component: PlayAutomations,
}
