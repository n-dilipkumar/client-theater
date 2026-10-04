import IdentifiedCompanies from './IdentifiedCompanies'
import { COMPANY_ICON } from './primitives'

/**
 * Identify anonymous web visitors as companies, and filter by pages read (WF-031).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be
 * touched to add or change it. That is the whole point: the twelve original
 * workflow branches each appended to a hard-coded ROUTES array, and all twelve
 * conflicted.
 *
 * The descriptor is pinned by `frontend/src/test/wf031-identified-companies.test.jsx`,
 * because a branch once shipped a page component with no `index.jsx`: the glob
 * never matched, the page was never imported, and the build was green while the
 * product carried zero occurrences of the ticket.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf031_identify_anonymous_web_visitors_as_com.py` exactly,
 * so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a building
 * attributed to a company rather than to a person is not in the shared `PATHS`
 * map, and that file is not ours to edit. `icon` carries the closest shared name
 * as a fallback for any consumer that reads only that field.
 */
export default {
  id: 'wf-031-identify-anonymous-web-visitors-as-com',
  label: 'Identified companies',
  icon: 'search',
  iconPath: COMPANY_ICON,
  order: 310,
  Component: IdentifiedCompanies,
}
