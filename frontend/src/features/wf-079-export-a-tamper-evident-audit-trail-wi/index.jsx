import AuditTrailExport from './AuditTrailExport'

/**
 * WF-079, registered.
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name and no shared file has to be touched
 * to add or change it. That is the whole point: the workflow this was built from
 * would otherwise have appended an entry to the hard-coded ROUTES array in App.jsx,
 * which is what made the original workflow branches conflict and none of them merge.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf079_export_a_tamper_evident_audit_trail_wi.py` exactly,
 * which is what lets the two halves of a feature be found by one name.
 *
 * The glyph is the shared `audit` mark rather than a new one: this workflow is a
 * read of the audit log, so `components/ui.jsx` already draws the right thing and
 * `PATHS` does not need editing.
 */
export default {
  id: 'wf-079-export-a-tamper-evident-audit-trail-wi',
  label: 'Audit trail export',
  icon: 'audit',
  order: 310,
  Component: AuditTrailExport,
}