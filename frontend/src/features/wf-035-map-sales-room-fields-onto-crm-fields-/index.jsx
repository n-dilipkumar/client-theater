import FieldMapping from './FieldMapping'
import { MAPPING_ICON } from './primitives'

/**
 * WF-035, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be touched
 * to add or change it. That is the whole point: the twelve original workflow
 * branches each appended to a hard-coded `ROUTES` array in App.jsx, and all twelve
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf035_map_sales_room_fields_onto_crm_fields_.py` exactly,
 * which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is two columns joined by a link, which is not in the shared `PATHS`
 * map, so `iconPath` carries the path and `icon` falls back to the shared `schema`
 * mark for any consumer that reads only that field. `components/ui.jsx` is not
 * edited.
 */
export default {
  id: 'wf-035-map-sales-room-fields-onto-crm-fields-',
  label: 'Field mapping',
  icon: 'schema',
  iconPath: MAPPING_ICON,
  order: 240,
  Component: FieldMapping,
}
