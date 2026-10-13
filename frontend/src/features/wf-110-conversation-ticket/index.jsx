import ConversationTicket from './ConversationTicket'
import { CONVERSATION_TICKET_ICON } from './primitives'

export { ConversationTicket }

/**
 * Conversation tickets, registered (WF-110).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/<id>/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be touched
 * to add or change it.
 *
 * `id` matches the backend module's `FEATURE["id"]` exactly, so the two halves of
 * the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: the "ticket
 * with a check" glyph is not in the shared `PATHS` map, and that file is not ours
 * to edit. `icon` carries the closest shared name as a fallback for any consumer
 * that reads only that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-110-conversation-ticket',
  label: 'Conversation tickets',
  icon: 'audit',
  iconPath: CONVERSATION_TICKET_ICON,
  order: 310,
  Component: ConversationTicket,
}
