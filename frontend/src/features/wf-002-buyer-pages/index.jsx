import BuyerPages from './BuyerPages'
import { PAGE_GLYPH } from './icons'

/**
 * WF-002: build the room's buyer-facing pages from DSR fragments.
 *
 * This file is the whole registration. The frontend host globs every
 * `index.jsx` one folder under `features`, so this page appears in the nav by
 * this file existing, and nothing in App.jsx or a routes array is edited.
 *
 * `id` is the hash route (`#/wf-002-buyer-pages`) and matches the backend
 * module's `FEATURE["id"]` exactly, so the two halves of the feature are
 * findable by one name.
 *
 * The icon is the design system's `schema` glyph by name, plus a `path` for a
 * page glyph. `components/ui.jsx` is shared, and its own `PATHS` map is
 * explicitly not for features to append to, so the path is passed per the
 * feature contract rather than added to the map once and inherited by everyone.
 */
export default {
  id: 'wf-002-buyer-pages',
  label: 'Buyer pages',
  icon: 'schema',
  iconPath: PAGE_GLYPH,
  order: 200,
  Component: BuyerPages,
}
