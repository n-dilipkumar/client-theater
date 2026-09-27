import BatchUpsert from './BatchUpsert'
import { UPSERT_ICON } from './icons'

/**
 * Batch-upsert engagement rows keyed on the external ID, registered (WF-038).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`,
 * `main.jsx` or `lib/features.js` learns this feature's name, and no shared file
 * has to be edited to add or change it.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "two rows
 * merging into one" is not in the shared `PATHS` map, and that file is not ours to
 * edit. `icon` carries the closest shared name as a fallback for a consumer that
 * reads only that field; the nav renders `iconPath`. See `./icons.jsx`.
 */
export default {
  id: 'wf-038-batch-upsert-engagement-rows-keyed-on-',
  label: 'Batch upsert',
  icon: 'database',
  iconPath: UPSERT_ICON,
  order: 380,
  Component: BatchUpsert,
}
