/**
 * The folder the host actually discovers, re-exporting the descriptor.
 *
 * The port brief for WF-001 names `src/features/wf-001/room-templates/index.jsx`
 * as this feature's file, and that is where the implementation lives: the page,
 * its API client, its icons, and the two primitives `components/ui.jsx` does not
 * carry. This one-line module exists because the host's glob in
 * `src/lib/features.js` reaches exactly one folder deep.
 *
 * A descriptor two folders down is not loaded, and a descriptor that is not
 * loaded is not a feature: it is a page nobody can reach. So the descriptor the
 * host globs has to sit at `src/features/<id>/index.jsx`, which is also what
 * `docs/FEATURE-CONTRACT.md` specifies and what all twenty-five sibling features
 * do. The alternative - widening the glob to any depth - would mean editing
 * `lib/features.js`, which is shared and refused, and it would let any nested
 * folder register a route.
 *
 * The path in the brief is therefore honoured as the implementation's home, and
 * this re-export is what makes it discoverable. Same module, same `id`, one
 * registration.
 */
export { default } from '../wf-001/room-templates/index.jsx'
