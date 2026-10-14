import ConversationIntelligence from './ConversationIntelligence'

/**
 * WF-135, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in `App.jsx`, `main.jsx`
 * or `lib/features.js` learns this feature's name and no shared file has to be
 * touched to add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf135_conversation_intelligence.py` exactly, so the two
 * halves of the feature are findable by one name.
 *
 * The glyph is not in the shared `PATHS` map, so `iconPath` carries a waveform
 * path and `components/ui.jsx` is not edited.
 */
export default {
  id: 'wf-135-conversation-intelligence',
  label: 'Conversation intelligence',
  icon: 'audit',
  iconPath: 'M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4',
  order: 135,
  Component: ConversationIntelligence,
}
