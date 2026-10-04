import { useEffect, useState } from 'react'

import AgreementGate from './AgreementGate'
import NdaGate from './NdaGate'
import { NDA_ICON } from './icons'

/**
 * WF-070, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name and no shared file has to be touched
 * to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf070_require_nda_acceptance_before_viewing.py` exactly, so
 * the two halves of the feature are findable by one name.
 *
 * Why the component below is a router
 * -----------------------------------
 *
 * This workflow has two faces and only one of them belongs in the seller's sidebar.
 * The seller wants a board of gated links; the viewer wants a block of legal text and
 * one button. The viewer arrives by following a shared link, which lands on a hash
 * that matches no route id, so the seller's shell would fall back to the dashboard
 * and the viewer would be shown someone else's pipeline.
 *
 * Handling that needs a route the shell knows about, and `App.jsx` is shared. WF-015
 * hit exactly this and solved it the same way, by intercepting the viewer hash inside
 * its own descriptor, and WF-069 did it again for buyer links. The cost is the same:
 * because `App.jsx` renders this component, the viewer also sees the seller's
 * sidebar. The security property is unaffected, because no room content is requested
 * until the API grants it, and the fix is one line in `App.jsx`. It is a human's
 * decision about shared chrome, so it is reported rather than smuggled across the
 * boundary.
 *
 * `#/accept/<link_id>` is deliberately not WF-069's `#/view-link/<link_id>` and not
 * WF-015's `#/view/<room_id>`. Two features must not both claim one hash pattern,
 * and the three are different things anyway: a link id here, a link id there, a room
 * id in WF-015.
 */
const VIEWER_ROUTE = /^accept\/([^/?]+)/

function viewerLinkId() {
  const hash = window.location.hash.replace(/^#\/?/, '')
  const match = hash.match(VIEWER_ROUTE)
  return match ? decodeURIComponent(match[1]) : null
}

function NdaGateWithViewerRoute() {
  const [linkId, setLinkId] = useState(viewerLinkId)

  useEffect(() => {
    const onHashChange = () => setLinkId(viewerLinkId())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  if (linkId) return <AgreementGate linkId={linkId} />
  return <NdaGate />
}

export default {
  id: 'wf-070-require-nda-acceptance-before-viewing',
  label: 'NDA gate',
  // The glyph is not in the shared PATHS map, so `iconPath` carries the path and
  // `icon` falls back to the shared mark. `components/ui.jsx` is not edited.
  icon: 'audit',
  iconPath: NDA_ICON,
  order: 700,
  Component: NdaGateWithViewerRoute,
}