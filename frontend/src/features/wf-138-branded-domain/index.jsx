import BrandedDomain from './BrandedDomain'

/**
 * WF-138, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf138_branded_custom_domain.py` exactly, so the two
 * halves of the feature are findable by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `components/ui.jsx` is not edited. The `icon` name stays because `Icon` falls
 * back to it, and a bare `iconPath` with no fallback would draw nothing on an
 * older build.
 *
 * The page is the seller's view: brands, tenant token overrides, host claims and
 * the rooms that inherit a brand. A buyer reaching a branded host is served by
 * `GET /api/wf-138/resolve`, which takes the Host header; that buyer surface has
 * no route of its own in this product yet, so this descriptor registers only the
 * seller page.
 */
export default {
  id: 'wf-138-branded-domain',
  label: 'Branded domain',
  icon: 'rooms',
  iconPath:
    'M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c2.5 2.5 3.8 5.6 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.6-3.8-9S9.5 5.5 12 3z',
  order: 138,
  Component: BrandedDomain,
}
