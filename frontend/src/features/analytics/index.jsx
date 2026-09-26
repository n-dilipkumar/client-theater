import Engagement from './Engagement'

/**
 * Analytics: review buyer engagement and prioritise follow-up (WF-006).
 *
 * This file is the whole registration. The frontend host globs every
 * `index.jsx` one folder under `features`, so the page appears in the nav by
 * this file existing and nothing in App.jsx or the routes array is edited.
 *
 * The `id` is the hash route (`#/wf-006-engagement-analytics`) and must match
 * the backend module's FEATURE id, so the two halves of the feature are
 * findable by one name.
 *
 * The icon is the design system's `dashboard` glyph. It reuses an existing
 * name rather than adding one, because components/ui.jsx is shared and a new
 * entry there is a conflict with every other feature.
 */
export default {
  id: 'wf-006-engagement-analytics',
  label: 'Analytics',
  icon: 'dashboard',
  order: 200,
  Component: Engagement,
}
