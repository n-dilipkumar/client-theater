import CrmReadPanel from './CrmReadPanel'
import { CRM_READ_ICON } from './primitives'

/**
 * The CRM read panel (WF-042).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx, or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: a hundred workflows land
 * at once, and the previous run of this project stalled with a dozen branches
 * built and none merged because all twelve appended to one shared route list.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf042_pull_crm_deal_account_and_contact_data_into.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: "a panel
 * reading a record out of a CRM" is not in the shared `PATHS` map, and that file
 * is not ours to edit. `icon` carries the closest shared name as a fallback for
 * any consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-042-pull-crm-deal-account-and-contact-data-into',
  label: 'CRM read panel',
  icon: 'database',
  iconPath: CRM_READ_ICON,
  order: 420,
  Component: CrmReadPanel,
}