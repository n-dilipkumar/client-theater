import { useEffect, useState } from 'react'

import Gate from './Gate'
import LinkGating from './LinkGating'
import { GATE_ICON } from './icons'

/**
 * WF-069, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name and no shared file has to be touched
 * to add or change it. That is the entire point: the original workflow branches each
 * appended an entry to a hard-coded `ROUTES` array in `App.jsx`, which is what made
 * twelve of them mutually unmergeable.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf069_gate_each_buyer_link_with_a_password_a.py` exactly, so
 * the two halves of the feature are findable by one name.
 *
 * Why the component below is a router
 * ----------------------------------
 *
 * This workflow has two faces and only one of them belongs in the seller's sidebar.
 * The seller wants a board of gated links; the buyer wants a password box and nothing
 * else. The buyer arrives by following an emailed link, which lands on a hash that
 * matches no route id, so the seller's shell would fall back to the dashboard and the
 * buyer would be shown someone else's pipeline.
 *
 * Handling that needs a route the shell knows about, and `App.jsx` is shared. WF-015
 * hit exactly this and solved it the same way, by intercepting the buyer hash inside
 * its own descriptor. The cost is the same: because `App.jsx` renders this component,
 * the buyer also sees the seller's sidebar. The security property is unaffected - no
 * room content is requested until the API grants it - and the fix is one line in
 * `App.jsx`. It is a human's decision about shared chrome, not this feature's, and it
 * is reported rather than smuggled across the boundary.
 *
 * `#/view-link/<link_id>` is deliberately not WF-015's `#/view/<room_id>`: two
 * features must not both claim one hash pattern, and the two are different things
 * anyway - a link id here, a room id there.
 */

const BUYER_ROUTE = /^view-link\/([^/?]+)/

function buyerLinkId() {
  const hash = window.location.hash.replace(/^#\/?/, '')
  const match = hash.match(BUYER_ROUTE)
  return match ? decodeURIComponent(match[1]) : null
}

function Gating() {
  const [linkId, setLinkId] = useState(buyerLinkId)

  useEffect(() => {
    const onHashChange = () => setLinkId(buyerLinkId())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  if (linkId) return <Gate linkId={linkId} />
  return <LinkGating />
}

export default {
  id: 'wf-069-gate-each-buyer-link-with-a-password-a',
  label: 'Link gating',
  // The glyph is not in the shared PATHS map, so `iconPath` carries the path and
  // `icon` falls back to the shared mark. `components/ui.jsx` is not edited.
  icon: 'schema',
  iconPath: GATE_ICON,
  order: 690,
  Component: Gating,
}
