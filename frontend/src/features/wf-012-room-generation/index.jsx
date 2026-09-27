import Generator from './Generator'
import { TEMPLATE } from './icons'

/**
 * Room generation: generate a personalised room programmatically from a
 * template (WF-012).
 *
 * This file is the whole registration. The frontend host globs every
 * `index.jsx` one folder under `features`, so the page appears in the nav by
 * this file existing and nothing in `App.jsx` or its `ROUTES` array is edited.
 * That is the property that let twelve workflow branches be written in twelve
 * worktrees without any of them conflicting.
 *
 * The `id` matches the backend module's `FEATURE["id"]` exactly, so the two
 * halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: the
 * "a template is a layout of placeholders" glyph is not in the shared
 * `components/ui.jsx` set, and that file is not ours to edit. See `icons.js`.
 * `icon` carries the closest shared name as a fallback for any consumer that
 * reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-012-room-generation',
  label: 'Template generator',
  icon: 'schema',
  iconPath: TEMPLATE,
  order: 210,
  Component: Generator,
}
