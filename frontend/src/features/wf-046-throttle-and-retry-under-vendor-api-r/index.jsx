import ThrottleQuota from './ThrottleQuota'
import { THROTTLE_ICON } from './primitives'

/**
 * Quota and throttling (WF-046).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf046_throttle_and_retry_under_vendor_api_ra.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a bucket
 * with something missing from it" is not in the shared `PATHS` map, and that file
 * is not ours to edit. `icon` carries the closest shared name as a fallback for
 * any consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-046-throttle-and-retry-under-vendor-api-ra',
  label: 'Quota and throttling',
  icon: 'database',
  iconPath: THROTTLE_ICON,
  order: 460,
  Component: ThrottleQuota,
}