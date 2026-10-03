import SandboxValidation from './SandboxValidation'
import { VALIDATE_ICON } from './primitives'

/**
 * Sandbox validation, registered (WF-048).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so both halves of
 * the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath`, because the plug-and-check glyph is
 * not in the shared `PATHS` map and that file is not ours to edit. `icon`
 * carries the closest shared name as a fallback.
 *
 * Without this file the page is not discovered. `npm run build` succeeds either
 * way, so the bundle is the place to check: it carried zero occurrences of
 * `wf-048` before this file existed.
 */
export default {
  id: 'wf-048-validate-the-connector-against-a-sandb',
  label: 'Sandbox validation',
  icon: 'audit',
  iconPath: VALIDATE_ICON,
  order: 285,
  Component: SandboxValidation,
}