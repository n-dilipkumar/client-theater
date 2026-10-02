/**
 * Which room's library is on screen, kept in the URL.
 *
 * The branch made this linkable by teaching the shared shell to parse
 * `#/documents?room=...` and hand a `roomId` prop to the page. App.jsx is
 * shared, and the host renders `<Active />` with no props at all, so a feature
 * cannot ask the shell for anything. This hook reproduces the behaviour the
 * branch wanted, inside the feature.
 *
 * The room id goes in the query string rather than the hash on purpose. The host
 * resolves the current page by comparing the whole hash against a route id, so
 * `#/wf-003-document-library?room=x` would not match any route and the user
 * would land on the dashboard. `?room=x#/wf-003-document-library` keeps the
 * host's routing untouched and is still a shareable link.
 */

import { useCallback, useEffect, useState } from 'react'

const PARAM = 'room'

export function readRoomFromUrl() {
  return new URLSearchParams(window.location.search).get(PARAM) || ''
}

/** Replace the query string without navigating, so no reload is triggered. */
function writeRoomToUrl(roomId) {
  const params = new URLSearchParams(window.location.search)
  if (roomId) params.set(PARAM, roomId)
  else params.delete(PARAM)
  const search = params.toString()
  const next = `${window.location.pathname}${search ? `?${search}` : ''}${window.location.hash}`
  window.history.replaceState(window.history.state, '', next)
}

export function useRoomSelection(fallbackRoomId) {
  // Only what the operator picked is state. The room on screen falls back to
  // the first room during render, so there is no effect whose job was to copy
  // the fallback into state -- and no render pass in between in which the page
  // briefly had no room at all.
  const [chosenRoomId, setChosenRoomId] = useState(readRoomFromUrl)

  const roomId = chosenRoomId || fallbackRoomId || ''

  // A link pasted into the address bar, or a back/forward step, lands here.
  // This one legitimately subscribes to an external system, so it stays an
  // effect; the state it writes is the operator's choice, not a derived value.
  useEffect(() => {
    const sync = () => setChosenRoomId(readRoomFromUrl())
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])

  const selectRoom = useCallback((next) => {
    writeRoomToUrl(next || '')
    setChosenRoomId(next || '')
  }, [])

  return [roomId, selectRoom]
}
