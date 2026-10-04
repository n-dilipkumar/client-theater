import AgreementExpiry from './AgreementExpiry'
import { AGREEMENT_EXPIRY_ICON } from './primitives'

/**
 * Agreement expiry and pre-expiry reminders (WF-081).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the original workflow
 * branches each appended to a hard-coded ROUTES array, and all of them
 * conflicted.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf081_expire_an_agreement_and_drive_pre_expiry.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a document
 * with a clock across it is not in the shared `PATHS` map, and that file is not
 * ours to edit. `icon` carries the closest shared name as a fallback for any
 * consumer that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-081-expire-an-agreement-and-drive-pre-expiry',
  label: 'Agreement expiry',
  icon: 'audit',
  iconPath: AGREEMENT_EXPIRY_ICON,
  order: 810,
  Component: AgreementExpiry,
}