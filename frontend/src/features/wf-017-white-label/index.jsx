import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import PublicRoom from './PublicRoom'
import WhiteLabel from './WhiteLabel'
import { WHITE_LABEL_ICON } from './icons'

/**
 * WF-017, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`,
 * `main.jsx`, or `lib/features.js` learns this feature's name and no shared file
 * is touched. That is the point: the branch this was ported from added a
 * `white-label` entry to the hard-coded `ROUTES` array in `App.jsx` *and* a
 * path matcher for buyer links, which is what made all twelve original workflow
 * branches conflict and none of them merge.
 *
 * `id` matches `FEATURE["id"]` in `backend/dsr/features/wf_017_white_label.py`
 * exactly, which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries the path and
 * `icon` falls back to the shared `schema` mark. `Icon` prefers `path`, and
 * `components/ui.jsx` is not edited.
 *
 * ------------------------------------------------------------------------
 * Why this file also claims a URL path, and why that is a finding
 * ------------------------------------------------------------------------
 * A white-labelled share link is a real path on a real host --
 * `https://proposals.acme.com/r/Proposal-Name-aB3xY9zK1q` -- not a hash route.
 * The branch served it by editing `App.jsx`: a `ROOM_PATH_RE` was matched
 * against `window.location.pathname`, and a match short-circuited to
 * `<PublicRoom>`.
 *
 * The frontend host has no equivalent seam. `App.jsx` resolves
 * `window.location.hash` against the route list and nothing else, and it is a
 * shared file, so a feature cannot ask to be consulted about a path. Without a
 * seam, a buyer who opens a share link lands on the dashboard, and the link this
 * feature mints -- the entire product of WF-017 -- does not work.
 *
 * So the seam is built here, in the only place a feature can build one: at module
 * scope. `src/lib/features.js` imports this module eagerly while `App.jsx` is
 * still evaluating, which is before `main.jsx` calls `createRoot`, so this code
 * runs first and gets to decide what the page is.
 *
 * `mountShareLinkIfRequested` renders the buyer page into its own container
 * appended to `document.body` and hides the application root, which is left
 * mounted and untouched underneath. It is deliberately boring:
 *
 *   - it no-ops when the path is not a share link, which is every admin visit;
 *   - it no-ops outside a browser, so a production build and a test import are
 *     both inert;
 *   - it no-ops when its container is already present, so a second import cannot
 *     produce two rooms;
 *   - it only ever adds a sibling. It does not unmount, patch or replace the
 *     application's own tree, and it restores the root's visibility if the path
 *     stops matching.
 *
 * `WhiteLabelRoute` re-checks the same path, and `ShareLinkPage` -- the component
 * the shim actually renders -- does too, so both are path-aware on their own
 * terms. That is what makes the shim safe to delete: once `App.jsx` grows a path
 * hook, the hook replaces `mountShareLinkIfRequested`, `WhiteLabelRoute` already
 * does the right thing, and nothing else has to move.
 *
 * THE FINDING FOR THE INTEGRATOR, stated once: **`App.jsx` needs a path-routing
 * seam.** Something like an optional `matchPath(pathname)` on a feature
 * descriptor, consulted before the hash route, with the matching feature
 * rendering in place of `<App/>`. With that, `mountShareLinkIfRequested`, this
 * section, and the `react-dom/client` import all disappear. Please treat that as
 * the real deliverable and this shim as the workaround for not having it.
 */

const ROOM_PATH_RE = /^\/r\/([^/]+)\/?$/

/** The share-link path this feature owns, or null for anything else. */
export function shareLinkPath(pathname) {
  return typeof pathname === 'string' && ROOM_PATH_RE.test(pathname) ? pathname : null
}

/**
 * `window.location.pathname`, kept in step with history navigation.
 *
 * A listener rather than a single read on mount, because a buyer can move
 * between two share links with the Back button without a hash change, and
 * without this the second room would keep showing the first.
 */
function useSharePath() {
  const [path, setPath] = useState(() =>
    typeof window === 'undefined' ? '/' : window.location.pathname,
  )

  useEffect(() => {
    const sync = () => setPath(window.location.pathname)
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])

  return path
}

/**
 * The buyer page, as a component that follows the path.
 *
 * This exists rather than a bare `createRoot(host).render(<PublicRoom path={...} />)`
 * because that would capture the path once: a buyer who presses Back from room B
 * to room A would keep seeing room B, because nothing would re-read
 * `location.pathname`. An earlier cut of this shim had exactly that bug, and its
 * own docstring claimed a redundancy that did not exist -- `WhiteLabelRoute` does
 * re-check the path, but `App.jsx` resolves an empty hash to the dashboard, so on
 * a share link this component is the only thing rendered and the redundancy was
 * never in the path that matters.
 */
function ShareLinkPage() {
  const path = useSharePath()
  return shareLinkPath(path) ? <PublicRoom path={path} /> : null
}

/**
 * Render the buyer page when the browser is on a share link.
 *
 * Returns true when it took the page over, so a caller -- and a test -- can tell
 * "mounted the room" from "left the application alone".
 */
export function mountShareLinkIfRequested() {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false

  const appRoot = document.getElementById('root')
  const existing = document.getElementById('wf-017-share-link')

  const path = shareLinkPath(window.location.pathname)
  if (!path) {
    // Not a share link. If this somehow already ran, put the application back
    // rather than leaving a buyer page stranded over a dashboard.
    if (existing) existing.remove()
    if (appRoot) appRoot.style.removeProperty('display')
    return false
  }
  if (existing) return true

  const host = document.createElement('div')
  host.id = 'wf-017-share-link'
  document.body.appendChild(host)
  if (appRoot) appRoot.style.setProperty('display', 'none')

  createRoot(host).render(<ShareLinkPage />)
  return true
}

/**
 * The feature's route: the operator's screen, or the buyer's room if this
 * browser is on a share link.
 *
 * Both halves use the same matcher, so whichever one is reached -- the shim on a
 * bare share link, or this route once the frontend host grows a path seam -- the
 * answer is the same.
 */
function WhiteLabelRoute() {
  const path = useSharePath()

  if (shareLinkPath(path)) return <PublicRoom path={path} />
  return <WhiteLabel />
}

mountShareLinkIfRequested()

export default {
  id: 'wf-017-white-label',
  label: 'White-label',
  icon: 'schema',
  iconPath: WHITE_LABEL_ICON,
  order: 170,
  Component: WhiteLabelRoute,
}
