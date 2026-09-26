/**
 * Frontend feature host.
 *
 * A feature is a folder at src/features/<id>/index.jsx whose default export is
 * a route descriptor:
 *
 *   export default {
 *     id: 'wf-014-access-windows',
 *     label: 'Access windows',
 *     icon: 'audit',            // any name known to components/ui.jsx Icon
 *     order: 200,               // optional; sorts within the feature group
 *     Component: MyPage,
 *   }
 *
 * Vite expands the glob into a static import graph at build time, so nothing
 * here enumerates features by hand. Adding a feature means adding a folder;
 * App.jsx is never edited again, which is what lets many features merge without
 * a conflict on one shared routes array.
 */

const modules = import.meta.glob('../features/*/index.jsx', { eager: true })

function descriptors() {
  const seen = new Map()
  const problems = []

  for (const [path, module] of Object.entries(modules)) {
    const descriptor = module?.default
    if (!descriptor?.Component || !descriptor?.id) {
      problems.push({ path, error: 'default export must provide { id, label, Component }' })
      continue
    }
    if (seen.has(descriptor.id)) {
      problems.push({ path, error: `duplicate feature id '${descriptor.id}'` })
      continue
    }
    seen.set(descriptor.id, { ...descriptor, source: path })
  }

  return { descriptors: [...seen.values()], problems }
}

const loaded = descriptors()

/** Feature routes, ordered: declared `order` first, then alphabetically. */
export const featureRoutes = loaded.descriptors.sort((a, b) => {
  const order = (a.order ?? 100) - (b.order ?? 100)
  if (order !== 0) return order
  return String(a.label || a.id).localeCompare(String(b.label || b.id))
})

/** Features that failed to load. Surfaced in the UI rather than swallowed. */
export const featureProblems = loaded.problems

export function featureById(id) {
  return loaded.descriptors.find((feature) => feature.id === id) || null
}
