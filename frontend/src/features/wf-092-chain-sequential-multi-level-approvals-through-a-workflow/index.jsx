import ApprovalChain from './ApprovalChain'
import { APPROVAL_CHAIN_ICON } from './primitives'

/**
 * Sequential multi-level quote approvals (WF-092).
 *
 * This file is the whole of the frontend registration. The host globs
 * `src/features/*\/index.jsx` at build time, so nothing in App.jsx, main.jsx or
 * lib/features.js learns this feature's name, and no shared file has to be touched to
 * add or change it.
 *
 * `id` matches `FEATURE["id"]` in
 * `backend/dsr/features/wf092_chain_sequential_multi_level_approvals_through_a_workflow.py`
 * exactly, so the two halves of the feature are findable by one name.
 *
 * The nav glyph is passed as `iconPath` rather than as an `icon` name: a document with
 * a check on it is not in the shared `PATHS` map, and that file is not ours to edit.
 * `icon` carries the closest shared name as a fallback for any consumer that reads only
 * that field; the nav itself renders `iconPath`.
 */
export default {
  id: 'wf-092-chain-sequential-multi-level-approvals-through-a-workflow',
  label: 'Quote approval chain',
  icon: 'audit',
  iconPath: APPROVAL_CHAIN_ICON,
  order: 920,
  Component: ApprovalChain,
}