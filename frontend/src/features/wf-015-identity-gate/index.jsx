import { useEffect, useState } from 'react'

import AccessSettings from './AccessSettings'
import Gate from './Gate'
import { ACCESS_ICON } from './icons'

/**
 * WF-015, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx` or
 * `lib/features.js` learns this feature's name and no shared file has to be touched
 * to add or change it. The branch this was ported from appended an `access` entry
 * to a hard-coded `ROUTES` array in `App.jsx`, which is what made all twelve
 * original workflow branches conflict and none of them merge.
 *
 * `id` is the hash route (`#/wf-015-identity-gate`) and matches `FEATURE["id"]` in
 * `backend/dsr/features/wf_015-identity-gate.py` exactly, so the two halves of the
 * feature are findable by one name.
 *
 * The gate is not in the nav, and this is why the component below is a router.
 * The emailed verification link redirects to `/#/view/{room_id}?token=...`, which
 * matches no route id in `App.jsx`, so the seller's shell would fall back to the
 * dashboard and the buyer would land somewhere meaningless. The branch handled this
 * with a `BUYER_ROUTE` branch inside `App.jsx`; that file is shared and a feature may
 * not edit it, so the interception lives here instead. `App.jsx` renders this
 * component, so the gate does get mounted - inside the seller's sidebar, which is
 * the cost, and a one-line change to `App.jsx` is the fix. It is reported rather than
 * smuggled across a boundary this port is not allowed to cross.
 */

const BUYER_ROUTE = /^view\/([^/?]+)/

function buyerRoomId() {
  const hash = window.location.hash.replace(/^#\/?/, '')
  const match = hash.match(BUYER_ROUTE)
  return match ? decodeURIComponent(match[1]) : null
}

function AccessAndIdentity() {
  const [roomId, setRoomId] = useState(buyerRoomId)

  useEffect(() => {
    const onHashChange = () => setRoomId(buyerRoomId())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  if (roomId) return <Gate roomId={roomId} />
  return <AccessSettings />
}

export default {
  id: 'wf-015-identity-gate',
  label: 'Access and identity',
  icon: 'schema',
  iconPath: ACCESS_ICON,
  order: 150,
  Component: AccessAndIdentity,
}
